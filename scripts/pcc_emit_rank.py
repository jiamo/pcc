#!/usr/bin/env python3
"""Rank frozen self-backend IR inputs by fresh pcc emit-worker cost."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import run_pcc_compile_ab as compile_ab


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = "pcc.stage2-object-inputs.v1"
OUTPUT_SCHEMA = "pcc.self-backend-emit-rank.v1"
_ACTIVE_PROCESSES: set[subprocess.Popen[str]] = set()
_ACTIVE_LOCK = threading.Lock()


class EmitRankError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _persist(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parse_darwin_time(path: Path) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        fields = raw_line.strip().split()
        if len(fields) == 2 and fields[0] in ("real", "user", "sys"):
            name = {
                "real": "wall_s",
                "user": "user_s",
                "sys": "system_s",
            }[fields[0]]
            metrics[name] = float(fields[1])
            continue
        if not fields or not fields[0].isdigit():
            continue
        value = int(fields[0])
        label = " ".join(fields[1:])
        name = {
            "maximum resident set size": "max_rss_bytes",
            "instructions retired": "instructions",
            "cycles elapsed": "cycles",
            "peak memory footprint": "peak_footprint_bytes",
        }.get(label)
        if name is not None:
            metrics[name] = value
    required = {
        "wall_s",
        "user_s",
        "system_s",
        "max_rss_bytes",
        "instructions",
        "cycles",
        "peak_footprint_bytes",
    }
    missing = required.difference(metrics)
    if missing:
        raise EmitRankError(
            f"Darwin time report {path} is missing {sorted(missing)}"
        )
    metrics["cpu_s"] = float(metrics["user_s"]) + float(metrics["system_s"])
    return metrics


def _matches_lane(size_bytes: int, lane: str) -> bool:
    if lane == "all":
        return True
    if lane == "oversized":
        return size_bytes >= 2_000_000
    if lane == "medium":
        return 1_000_000 <= size_bytes < 2_000_000
    if lane == "small":
        return size_bytes < 1_000_000
    raise EmitRankError(f"unknown lane: {lane}")


def _load_items(
    path: Path,
    lane: str,
    max_items: int,
    item_indices: set[int] | None = None,
) -> tuple[dict, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != INPUT_SCHEMA:
        raise EmitRankError(f"input manifest must use {INPUT_SCHEMA}")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise EmitRankError("input manifest items must be a list")
    items = []
    for raw_item in raw_items:
        item = dict(raw_item)
        item_path = path.parent / str(item.get("path", ""))
        size_bytes = int(item.get("size_bytes", -1))
        if not item_path.is_file() or item_path.stat().st_size != size_bytes:
            raise EmitRankError(f"input receipt mismatch: {item_path}")
        if _sha256(item_path) != item.get("sha256"):
            raise EmitRankError(f"input hash mismatch: {item_path}")
        item_index = int(item.get("index", -1))
        if (
            _matches_lane(size_bytes, lane)
            and (item_indices is None or item_index in item_indices)
        ):
            item["absolute_path"] = str(item_path.resolve())
            items.append(item)
    if max_items > 0:
        items = items[:max_items]
    if not items:
        raise EmitRankError(f"no {lane} inputs selected")
    return payload, items


def _register_process(process: subprocess.Popen[str]) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES.add(process)


def _unregister_process(process: subprocess.Popen[str]) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES.discard(process)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _interrupt(signum, _frame) -> None:
    with _ACTIVE_LOCK:
        active = tuple(_ACTIVE_PROCESSES)
    for process in active:
        _terminate_process(process)
    raise KeyboardInterrupt(f"interrupted by signal {signum}")


def _run_item(
    compiler: Path,
    output_dir: Path,
    item: dict,
    timeout_seconds: float,
) -> dict:
    item_index = int(item["index"])
    prefix = output_dir / f"item_{item_index:03d}"
    result_path = prefix.with_suffix(".result")
    assembly_path = prefix.with_suffix(".s")
    time_path = prefix.with_suffix(".time")
    stdout_path = prefix.with_suffix(".stdout")
    stderr_path = prefix.with_suffix(".stderr")
    command = [
        "/usr/bin/time",
        "-lp",
        "-o",
        str(time_path),
        str(compiler),
        "--pcc-self-backend-emit-worker",
        str(item["absolute_path"]),
        str(result_path),
        str(assembly_path),
        "",
    ]
    environment = os.environ.copy()
    environment.update(
        {
            "PCC_GC_BACKEND": "0",
            "PCC_PYTHON_IR_PASSES": "off",
            "PYTHONHASHSEED": "0",
        }
    )
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _register_process(process)
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _terminate_process(process)
            raise EmitRankError(
                f"item {item_index} timed out after {timeout_seconds:.1f}s"
            ) from exc
    finally:
        _unregister_process(process)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    if process.returncode != 0:
        raise EmitRankError(
            f"item {item_index} failed with rc={process.returncode}: {stderr.strip()}"
        )
    result_lines = result_path.read_text(encoding="utf-8").splitlines()
    if (
        len(result_lines) < 2
        or result_lines[0] != "self-aarch64-darwin-v0"
        or Path(result_lines[1]).resolve() != assembly_path.resolve()
    ):
        raise EmitRankError(f"item {item_index} produced an invalid worker receipt")
    metrics = _parse_darwin_time(time_path)
    return {
        **{key: value for key, value in item.items() if key != "absolute_path"},
        "assembly_path": assembly_path.name,
        "assembly_sha256": _sha256(assembly_path),
        "metrics": metrics,
    }


def run(args: argparse.Namespace) -> dict:
    if sys.platform != "darwin":
        raise EmitRankError("pcc_emit_rank currently requires Darwin /usr/bin/time -lp")
    compiler = Path(args.compiler).expanduser().resolve()
    input_manifest = Path(args.input_manifest).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise EmitRankError(f"compiler is not executable: {compiler}")
    if output_dir.exists():
        raise EmitRankError(f"output directory already exists: {output_dir}")
    if args.jobs <= 0 or args.timeout <= 0 or args.max_items < 0:
        raise EmitRankError("jobs/timeout must be positive and max-items nonnegative")
    source_manifest, items = _load_items(
        input_manifest,
        args.lane,
        args.max_items,
        set(args.item_index) if args.item_index else None,
    )
    output_dir.mkdir(parents=True)
    manifest_path = output_dir / "manifest.json"
    manifest = {
        "schema": OUTPUT_SCHEMA,
        "status": "RUNNING",
        "compiler": str(compiler),
        "compiler_sha256": _sha256(compiler),
        "input_manifest": str(input_manifest),
        "input_manifest_sha256": _sha256(input_manifest),
        "source_bundle_sha256": source_manifest.get("source_bundle_sha256"),
        "lane": args.lane,
        "requested_item_indices": sorted(set(args.item_index)),
        "jobs": args.jobs,
        "timeout_s": args.timeout,
        "selected_count": len(items),
        "completed_count": 0,
        "items": [],
    }
    _persist(manifest_path, manifest)
    started = time.monotonic()
    try:
        with compile_ab._performance_lock():
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(args.jobs, len(items))
            ) as executor:
                futures = {
                    executor.submit(
                        _run_item,
                        compiler,
                        output_dir,
                        item,
                        args.timeout,
                    ): item
                    for item in items
                }
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    manifest["items"].append(result)
                    manifest["completed_count"] = len(manifest["items"])
                    _persist(manifest_path, manifest)
                    print(
                        f"item={result['index']} module={result['module_name']} "
                        f"bytes={result['size_bytes']} "
                        f"wall={result['metrics']['wall_s']:.2f}s",
                        flush=True,
                    )
    except BaseException as exc:
        manifest["status"] = "ERROR"
        manifest["error"] = str(exc)
        manifest["elapsed_s"] = time.monotonic() - started
        _persist(manifest_path, manifest)
        raise
    ranked = sorted(
        manifest["items"],
        key=lambda item: (-float(item["metrics"]["wall_s"]), int(item["index"])),
    )
    manifest["items"] = sorted(manifest["items"], key=lambda item: item["index"])
    manifest["ranking"] = [
        {
            "rank": rank,
            "index": item["index"],
            "module_name": item["module_name"],
            "size_bytes": item["size_bytes"],
            "wall_s": item["metrics"]["wall_s"],
            "cpu_s": item["metrics"]["cpu_s"],
            "instructions": item["metrics"]["instructions"],
            "peak_footprint_bytes": item["metrics"]["peak_footprint_bytes"],
            "assembly_sha256": item["assembly_sha256"],
        }
        for rank, item in enumerate(ranked, 1)
    ]
    manifest["elapsed_s"] = time.monotonic() - started
    manifest["status"] = "COMPLETE"
    _persist(manifest_path, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--lane",
        choices=("oversized", "medium", "small", "all"),
        default="medium",
    )
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument(
        "--item-index",
        action="append",
        type=int,
        default=[],
        help="select one exact manifest item index; repeat for several",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        manifest = run(_parser().parse_args(argv))
    except (EmitRankError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"pcc emit rank error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "selected_count": manifest["selected_count"],
                "elapsed_s": manifest["elapsed_s"],
                "top": manifest["ranking"][:10],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
