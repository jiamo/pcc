#!/usr/bin/env python3
"""Inventory text-fallback AArch64 opcodes in packed indexed sidecars."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from contextlib import nullcontext
import datetime as dt
import fcntl
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPO_ROOT / "build" / ".pcc-performance.lock"


def fallback_opcodes(lines: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    in_text = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(".section "):
            in_text = stripped[len(".section ") :].startswith("__TEXT,__text,")
            continue
        if (
            not in_text
            or not stripped
            or stripped.endswith(":")
            or stripped.startswith(".")
        ):
            continue
        counts[stripped.split(None, 1)[0]] += 1
    return counts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _persist(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _source_receipt() -> dict:
    """Use the same source identity as the stage build receipts."""
    path = REPO_ROOT / "scripts" / "run_pcc_stage1_build.py"
    spec = importlib.util.spec_from_file_location("inventory_stage_sources", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load stage source manifest provider")
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    return provider.source_manifest(REPO_ROOT, provider._load_ab_tool())


@contextmanager
def _text_instruction_inventory():
    # Host-only observer, never a production emitter option or extra pcc1
    # allocation. Restore the exact function even if emission rejects its input.
    from pcc.backend import self_backend_aarch64_darwin as emitter

    original = emitter.append_emitted_instruction_record
    opcodes: Counter[str] = Counter()

    def append(line, *args, **kwargs):
        opcodes[line.split(None, 1)[0]] += 1
        return original(line, *args, **kwargs)

    emitter.append_emitted_instruction_record = append
    try:
        yield opcodes
    finally:
        emitter.append_emitted_instruction_record = original


@contextmanager
def _performance_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another performance run holds " + str(LOCK_PATH)) from exc
        owner = {
            "active": True,
            "argv": sys.argv,
            "pid": os.getpid(),
            "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps(owner, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        try:
            yield
        finally:
            owner["active"] = False
            owner["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
            stream.seek(0)
            stream.truncate()
            stream.write(json.dumps(owner, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def run(
    root: Path,
    output: Path,
    *,
    start_index: int = 0,
    limit: int | None = None,
) -> dict[str, object]:
    sys.path.insert(0, str(REPO_ROOT))
    from pcc.backend.self_backend_aarch64_darwin import (
        emit_aarch64_darwin_indexed_transport,
    )
    from pcc.backend.self_backend_indexed_codec import decode_indexed_module_file

    paths = [root] if root.is_file() else sorted(root.glob("module_*.direct.pidx"))
    if not paths:
        raise RuntimeError("no indexed module sidecars under " + str(root))
    available_files = len(paths)
    if start_index < 0 or start_index >= available_files:
        raise ValueError("inventory start index is outside input range")
    if limit is not None and limit <= 0:
        raise ValueError("inventory limit must be positive")
    paths = paths[start_index:] if limit is None else paths[start_index:start_index + limit]
    source_before = _source_receipt()
    result: dict[str, object] = {
        "schema": "pcc.structured-instruction-inventory.v1",
        "root": str(root),
        "status": "RUNNING",
        "files": [],
        "available_files": available_files,
        "start_index": start_index,
        "selected_files": len(paths),
        "bootstrap_source_sha256": source_before["bootstrap_source_sha256"],
        "tool_sha256": _sha256(Path(__file__)),
    }
    aggregate: Counter[str] = Counter()
    structured_total = 0
    fallback_total = 0
    direct_total = 0
    text_encoded_total = 0
    text_aggregate: Counter[str] = Counter()
    for index, path in enumerate(paths):
        input_sha256 = _sha256(path)
        module = decode_indexed_module_file(str(path))
        with _text_instruction_inventory() as text_opcodes:
            transport = emit_aarch64_darwin_indexed_transport(
                module,
                optimize=False,
                structured_instructions=True,
            )
        direct_count = transport.direct_instruction_count
        text_encoded = transport.structured_instruction_count - direct_count
        if text_encoded < 0 or sum(text_opcodes.values()) != (
            text_encoded + transport.fallback_instruction_count
        ):
            raise RuntimeError(path.name + " instruction origin inventory mismatch")
        if _sha256(path) != input_sha256:
            raise RuntimeError(path.name + " changed during inventory")
        opcodes = (
            Counter(line.strip().split(None, 1)[0]
                    for line in transport.fallback_instruction_lines)
            if transport.native_finalized else fallback_opcodes(transport.line_chunks)
        )
        classified = sum(opcodes.values())
        if classified != transport.fallback_instruction_count:
            raise RuntimeError(
                path.name
                + " fallback inventory mismatch: "
                + str(classified)
                + " != "
                + str(transport.fallback_instruction_count)
            )
        aggregate.update(opcodes)
        structured_total += transport.structured_instruction_count
        fallback_total += transport.fallback_instruction_count
        direct_total += direct_count
        text_encoded_total += text_encoded
        text_aggregate.update(text_opcodes)
        result["files"].append(
            {
                "name": path.name,
                "sha256": input_sha256,
                "size_bytes": path.stat().st_size,
                "structured": transport.structured_instruction_count,
                "fallback": transport.fallback_instruction_count,
                "fallback_opcodes": dict(sorted(opcodes.items())),
                "direct": direct_count,
                "text_encoded": text_encoded,
                "text_opcodes": dict(sorted(text_opcodes.items())),
            }
        )
        if transport.encoded_line_records is not None:
            transport.encoded_line_records.close()
        del transport
        del module
        gc.collect()
        result["completed_files"] = index + 1
        result["structured"] = structured_total
        result["fallback"] = fallback_total
        result["fallback_opcodes"] = dict(sorted(aggregate.items()))
        result["direct"] = direct_total
        result["text_encoded"] = text_encoded_total
        result["text_opcodes"] = dict(sorted(text_aggregate.items()))
        _persist(output, result)
        if (index + 1) % 10 == 0 or index + 1 == len(paths):
            print(
                "inventory "
                + str(index + 1)
                + "/"
                + str(len(paths))
                + " fallback="
                + str(fallback_total),
                file=sys.stderr,
                flush=True,
            )
    if _source_receipt() != source_before or _sha256(Path(__file__)) != result["tool_sha256"]:
        result["status"] = "FAILED_SOURCE_CHANGED"
        _persist(output, result)
        raise RuntimeError("source changed during instruction inventory")
    result["status"] = "COMPLETE"
    _persist(output, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--output", required=True)
    parser.add_argument("--no-performance-lock", action="store_true")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--limit", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve(strict=True)
    output = Path(args.output).expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with nullcontext() if args.no_performance_lock else _performance_lock():
            result = run(root, output, start_index=args.start_index, limit=args.limit)
    except (OSError, RuntimeError, ValueError) as exc:
        print("structured instruction inventory error: " + str(exc), file=sys.stderr)
        return 1
    print(
        "PCC_STRUCTURED_INVENTORY_COMPLETE files="
        + str(result["completed_files"])
        + " fallback="
        + str(result["fallback"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
