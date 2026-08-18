#!/usr/bin/env python3
"""Run one source-bound long-running workload across production GC0..4.

The default 100k-round churn run is manual-only and requires
``PCC_GC_LONGRUN=1``. Smaller runs exercise the manifest and measurement
contract without making a long-running performance claim.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import resource
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Any, Iterable


REPO_ROOT = Path(__file__).absolute().parents[1]
WORKLOAD = REPO_ROOT / "benchmarks" / "python" / "longrun_churn.py"
DEFAULT_ROUNDS = 100_000
MANUAL_ENV = "PCC_GC_LONGRUN"
SCHEMA_VERSION = 2
MAX_PERSISTED_SAMPLES = 25
MIN_THROUGHPUT_OPS_PER_SEC = 240_000
MAX_PEAK_RSS_BYTES = 10_000_000
MAX_STEADY_DRIFT_BYTES_PER_MILLION_OPS = 65_536
MAX_CPU_US_PER_MILLION_OPS = 5_000_000
MAX_ALLOCATOR_FRAGMENTATION_BYTES = 10_000_000
MAX_PAUSE_US = 10_000
GC4_RETAINED_ZPAGE_GAP_LIMIT = 504_992
FIELD_NAMES = (
    "elapsed_ms",
    "rss_bytes",
    "peak_rss_bytes",
    "pause_count",
    "pause_sum_us",
    "pause_max_us",
    "pause_lt_100us",
    "pause_lt_1ms",
    "pause_lt_10ms",
    "pause_ge_10ms",
    "ops",
    "heap_in_use_bytes",
    "heap_capacity_bytes",
    "zpage_capacity_bytes",
    "zpage_used_bytes",
    "zpage_span_bytes",
    "zpage_free_capacity_bytes",
)


class GCLongrunGateError(RuntimeError):
    pass


def _source_files() -> tuple[Path, ...]:
    files = [WORKLOAD, Path(__file__).absolute(), REPO_ROOT / "pyproject.toml"]
    for path in (REPO_ROOT / "pcc").rglob("*"):
        if not path.is_file():
            continue
        if any(part in {"__pycache__", "build", "build_py", "build_pcc"} for part in path.parts):
            continue
        if path.suffix in {".a", ".o", ".ll", ".pyc"}:
            continue
        files.append(path)
    return tuple(sorted(set(files)))


def source_digest(paths: Iterable[Path] | None = None) -> str:
    digest = hashlib.sha256()
    for path in paths or _source_files():
        relative = path.relative_to(REPO_ROOT)
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def manual_gate_enabled(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(MANUAL_ENV) == "1"


def _cache_root() -> Path:
    configured = os.environ.get("PCC_GC_LONGRUN_CACHE")
    if configured:
        return Path(configured).expanduser().absolute()
    return Path.home() / ".cache" / "pcc" / "gc-longrun"


def _run_checked(
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    child_env = os.environ.copy() if env is None else env.copy()
    child_env.pop("LC_ALL", None)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise GCLongrunGateError(
            f"command timed out after {timeout}s: {' '.join(command)}"
        ) from exc
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if result.returncode != 0:
        raise GCLongrunGateError(
            f"command failed rc={result.returncode}: {' '.join(command)}\n"
            f"{stdout[-2000:]}\n{stderr[-4000:]}"
        )
    return result


def build_workload(*, timeout: int) -> tuple[Path, str, str]:
    digest = source_digest()
    build_root = _cache_root() / digest[:20]
    executable = build_root / "longrun_churn"
    if not executable.exists():
        build_root.mkdir(parents=True, exist_ok=True)
        print(f"[gc-longrun] build source={digest[:20]}", file=sys.stderr, flush=True)
        _run_checked(
            [
                sys.executable,
                "-m",
                "pcc",
                "--python-libpython=off",
                "--ir-scaffold=on",
                "--backend",
                "self",
                str(WORKLOAD),
                "-o",
                str(executable),
            ],
            timeout=timeout,
        )
    binary_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    return executable, digest, binary_digest


def parse_samples(stdout: str, *, backend: int) -> tuple[list[dict[str, int]], int]:
    lines = [line for line in stdout.splitlines() if line]
    if not lines or not lines[-1].startswith("done,"):
        raise GCLongrunGateError(f"backend {backend} did not emit done marker")
    try:
        completed_ops = int(lines[-1].split(",", 1)[1])
    except (IndexError, ValueError) as exc:
        raise GCLongrunGateError(f"backend {backend} invalid done marker") from exc
    samples: list[dict[str, int]] = []
    for line in lines[:-1]:
        parts = line.split(",")
        if len(parts) != len(FIELD_NAMES):
            raise GCLongrunGateError(
                f"backend {backend} expected {len(FIELD_NAMES)} fields, got {len(parts)}"
            )
        try:
            sample = dict(zip(FIELD_NAMES, (int(value) for value in parts), strict=True))
        except ValueError as exc:
            raise GCLongrunGateError(f"backend {backend} non-integer sample") from exc
        if sum(sample[name] for name in FIELD_NAMES[6:10]) != sample["pause_count"]:
            raise GCLongrunGateError(f"backend {backend} pause histogram mismatch")
        if sample["rss_bytes"] <= 0 or sample["peak_rss_bytes"] <= 0:
            raise GCLongrunGateError(f"backend {backend} missing RSS")
        if sample["heap_capacity_bytes"] < sample["heap_in_use_bytes"]:
            raise GCLongrunGateError(f"backend {backend} invalid heap capacity")
        if sample["zpage_span_bytes"] < sample["zpage_used_bytes"]:
            raise GCLongrunGateError(f"backend {backend} invalid zpage span")
        samples.append(sample)
    if len(samples) < 3:
        raise GCLongrunGateError(f"backend {backend} emitted only {len(samples)} samples")
    if [s["ops"] for s in samples] != sorted(s["ops"] for s in samples):
        raise GCLongrunGateError(f"backend {backend} ops are not monotonic")
    if completed_ops < samples[-1]["ops"]:
        raise GCLongrunGateError(f"backend {backend} completed ops regressed")
    return samples, completed_ops


def _summarize(samples: list[dict[str, int]], completed_ops: int) -> dict[str, Any]:
    final = samples[-1]
    tail_count = max(3, min(25, len(samples) // 5))
    tail = samples[-tail_count:]
    elapsed_ms = max(1, final["elapsed_ms"])
    allocator_gap = final["heap_capacity_bytes"] - final["heap_in_use_bytes"]
    zpage_gap = final["zpage_span_bytes"] - final["zpage_used_bytes"]
    tail_ops = max(1, tail[-1]["ops"] - tail[0]["ops"])
    return {
        "completed_ops": completed_ops,
        "elapsed_ms": elapsed_ms,
        "throughput_ops_per_sec": completed_ops * 1000 // elapsed_ms,
        "rss_initial_bytes": samples[0]["rss_bytes"],
        "rss_peak_bytes": max(s["peak_rss_bytes"] for s in samples),
        "steady_tail_samples": tail_count,
        "steady_rss_median_bytes": int(median(s["rss_bytes"] for s in tail)),
        "steady_rss_min_bytes": min(s["rss_bytes"] for s in tail),
        "steady_rss_max_bytes": max(s["rss_bytes"] for s in tail),
        "steady_rss_drift_bytes": tail[-1]["rss_bytes"] - tail[0]["rss_bytes"],
        "steady_rss_drift_bytes_per_million_ops": (
            (tail[-1]["rss_bytes"] - tail[0]["rss_bytes"]) * 1_000_000 // tail_ops
        ),
        "pause_count": final["pause_count"],
        "pause_sum_us": final["pause_sum_us"],
        "pause_max_us": final["pause_max_us"],
        "pause_histogram": {
            "lt_100us": final["pause_lt_100us"],
            "lt_1ms": final["pause_lt_1ms"],
            "lt_10ms": final["pause_lt_10ms"],
            "ge_10ms": final["pause_ge_10ms"],
        },
        "heap_in_use_bytes": final["heap_in_use_bytes"],
        "heap_capacity_bytes": final["heap_capacity_bytes"],
        "allocator_fragmentation_bytes": allocator_gap,
        "allocator_fragmentation_per_mille": (
            allocator_gap * 1000 // max(1, final["heap_capacity_bytes"])
        ),
        "zpage_capacity_bytes": final["zpage_capacity_bytes"],
        "zpage_used_bytes": final["zpage_used_bytes"],
        "zpage_span_bytes": final["zpage_span_bytes"],
        "zpage_retained_gap_bytes": zpage_gap,
        "zpage_free_capacity_bytes": final["zpage_free_capacity_bytes"],
    }


def _persisted_samples(samples: list[dict[str, int]]) -> list[dict[str, int]]:
    if len(samples) <= MAX_PERSISTED_SAMPLES:
        return samples
    last = len(samples) - 1
    indexes = [
        index * last // (MAX_PERSISTED_SAMPLES - 1)
        for index in range(MAX_PERSISTED_SAMPLES)
    ]
    return [samples[index] for index in indexes]


def _child_cpu_usage_us() -> tuple[int, int]:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return int(usage.ru_utime * 1_000_000), int(usage.ru_stime * 1_000_000)


def run_backend(
    executable: Path,
    *,
    backend: int,
    rounds: int,
    timeout: int,
    repeat_index: int = 1,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["PCC_GC_BACKEND"] = str(backend)
    print(
        f"[gc-longrun] backend={backend} repeat={repeat_index} "
        f"rounds={rounds} start",
        file=sys.stderr,
        flush=True,
    )
    user_before_us, system_before_us = _child_cpu_usage_us()
    result = _run_checked([str(executable), str(rounds)], timeout=timeout, env=env)
    user_after_us, system_after_us = _child_cpu_usage_us()
    samples, completed_ops = parse_samples(result.stdout, backend=backend)
    summary = _summarize(samples, completed_ops)
    user_cpu_us = max(0, user_after_us - user_before_us)
    system_cpu_us = max(0, system_after_us - system_before_us)
    total_cpu_us = user_cpu_us + system_cpu_us
    summary["process_user_cpu_us"] = user_cpu_us
    summary["process_system_cpu_us"] = system_cpu_us
    summary["process_total_cpu_us"] = total_cpu_us
    summary["cpu_us_per_million_ops"] = (
        total_cpu_us * 1_000_000 // max(1, completed_ops)
    )
    print(
        f"[gc-longrun] backend={backend} repeat={repeat_index} "
        f"done elapsed_ms={summary['elapsed_ms']} "
        f"cpu_us={summary['process_total_cpu_us']} "
        f"rss_peak={summary['rss_peak_bytes']}",
        file=sys.stderr,
        flush=True,
    )
    persisted = _persisted_samples(samples)
    return {
        "backend": backend,
        "repeat_index": repeat_index,
        "summary": summary,
        "samples_total": len(samples),
        "samples_persisted": len(persisted),
        "samples": persisted,
    }


def _aggregate_results(
    results: list[dict[str, Any]],
    backends: tuple[int, ...],
) -> list[dict[str, int]]:
    aggregates: list[dict[str, int]] = []
    for backend in backends:
        rows = [row["summary"] for row in results if row["backend"] == backend]
        aggregates.append(
            {
                "backend": backend,
                "repeats": len(rows),
                "median_throughput_ops_per_sec": int(
                    median(row["throughput_ops_per_sec"] for row in rows)
                ),
                "median_cpu_us_per_million_ops": int(
                    median(row["cpu_us_per_million_ops"] for row in rows)
                ),
                "max_peak_rss_bytes": max(row["rss_peak_bytes"] for row in rows),
                "max_positive_steady_rss_drift_bytes_per_million_ops": max(
                    max(0, row["steady_rss_drift_bytes_per_million_ops"])
                    for row in rows
                ),
                "max_pause_us": max(row["pause_max_us"] for row in rows),
                "max_allocator_fragmentation_bytes": max(
                    row["allocator_fragmentation_bytes"] for row in rows
                ),
                "max_zpage_retained_gap_bytes": max(
                    row["zpage_retained_gap_bytes"] for row in rows
                ),
            }
        )
    return aggregates


def evaluate_resource_budget(
    aggregates: list[dict[str, int]],
) -> dict[str, Any]:
    violations: list[str] = []
    for row in aggregates:
        backend = row["backend"]
        checks = (
            (
                "median_throughput_ops_per_sec",
                row["median_throughput_ops_per_sec"] >= MIN_THROUGHPUT_OPS_PER_SEC,
            ),
            ("max_peak_rss_bytes", row["max_peak_rss_bytes"] <= MAX_PEAK_RSS_BYTES),
            (
                "max_positive_steady_rss_drift_bytes_per_million_ops",
                row["max_positive_steady_rss_drift_bytes_per_million_ops"]
                <= MAX_STEADY_DRIFT_BYTES_PER_MILLION_OPS,
            ),
            (
                "median_cpu_us_per_million_ops",
                row["median_cpu_us_per_million_ops"]
                <= MAX_CPU_US_PER_MILLION_OPS,
            ),
            ("max_pause_us", row["max_pause_us"] < MAX_PAUSE_US),
            (
                "max_allocator_fragmentation_bytes",
                row["max_allocator_fragmentation_bytes"]
                <= MAX_ALLOCATOR_FRAGMENTATION_BYTES,
            ),
        )
        for metric, passed in checks:
            if not passed:
                violations.append(f"gc{backend}:{metric}={row[metric]}")
        if (
            backend == 4
            and row["max_zpage_retained_gap_bytes"]
            > GC4_RETAINED_ZPAGE_GAP_LIMIT
        ):
            violations.append(
                "gc4:max_zpage_retained_gap_bytes="
                + str(row["max_zpage_retained_gap_bytes"])
            )
    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "thresholds": {
            "min_median_throughput_ops_per_sec": MIN_THROUGHPUT_OPS_PER_SEC,
            "max_peak_rss_bytes": MAX_PEAK_RSS_BYTES,
            "max_positive_steady_rss_drift_bytes_per_million_ops": (
                MAX_STEADY_DRIFT_BYTES_PER_MILLION_OPS
            ),
            "max_median_cpu_us_per_million_ops": MAX_CPU_US_PER_MILLION_OPS,
            "max_allocator_fragmentation_bytes": (
                MAX_ALLOCATOR_FRAGMENTATION_BYTES
            ),
            "max_pause_us_exclusive": MAX_PAUSE_US,
            "gc4_max_zpage_retained_gap_bytes": GC4_RETAINED_ZPAGE_GAP_LIMIT,
        },
    }


def run_gate(
    *,
    rounds: int,
    backends: tuple[int, ...],
    build_timeout: int,
    backend_timeout: int,
    repeats: int = 1,
) -> dict[str, Any]:
    if rounds >= DEFAULT_ROUNDS and not manual_gate_enabled():
        raise GCLongrunGateError(
            f"rounds={rounds} is manual-only; set {MANUAL_ENV}=1"
        )
    if rounds < 600:
        raise GCLongrunGateError("rounds must be at least 600 for three samples")
    if not backends or any(backend < 0 or backend > 4 for backend in backends):
        raise GCLongrunGateError(f"invalid backend set: {backends}")
    if repeats < 1 or repeats > 10:
        raise GCLongrunGateError("repeats must be between 1 and 10")
    executable, digest, binary_digest = build_workload(timeout=build_timeout)
    results = []
    for repeat_index in range(1, repeats + 1):
        for backend in backends:
            results.append(
                run_backend(
                    executable,
                    backend=backend,
                    rounds=rounds,
                    timeout=backend_timeout,
                    repeat_index=repeat_index,
                )
            )
    aggregates = _aggregate_results(results, backends)
    return {
        "schema_version": SCHEMA_VERSION,
        "harness": "pcc-gc-longrun-churn",
        "mode": "strict-no-libpython-self-backend",
        "status": "MEASURED",
        "measured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha256": digest,
        "binary_sha256": binary_digest,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_driver": platform.python_version(),
        "rounds": rounds,
        "repeats": repeats,
        "live_set": 2048,
        "sample_every_rounds": 200,
        "backends": list(backends),
        "results": results,
        "aggregates": aggregates,
        "resource_budget": evaluate_resource_budget(aggregates),
        "claim_boundary": (
            "Current-machine, source-bound, strict no-libpython/self-backend "
            "steady-live-set churn profile. Pause buckets are runtime telemetry; "
            "RSS is sampled in process; malloc and zpage fragmentation axes are "
            "reported separately. This is a profile, not a collector ranking."
        ),
    }


def _parse_backends(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split(",") if part)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid backend list: {value}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--backends", type=_parse_backends, default=(0, 1, 2, 3, 4))
    parser.add_argument("--build-timeout", type=int, default=240)
    parser.add_argument("--backend-timeout", type=int, default=180)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--enforce-resource-budget", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = run_gate(
            rounds=args.rounds,
            backends=args.backends,
            build_timeout=args.build_timeout,
            backend_timeout=args.backend_timeout,
            repeats=args.repeats,
        )
    except (OSError, GCLongrunGateError) as exc:
        print(f"gc longrun gate failed: {exc}", file=sys.stderr)
        return 2
    blob = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(blob, encoding="utf-8")
    if not args.quiet:
        sys.stdout.write(blob)
    if (
        args.enforce_resource_budget
        and manifest["resource_budget"]["status"] != "PASS"
    ):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
