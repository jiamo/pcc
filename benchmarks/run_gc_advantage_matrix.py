"""Compile and run the five-GC advantage workload matrix.

The runner emits:

* `gc_advantage_rows.json` with per-run medians and raw samples
* `gc_advantage_summary.md` with a compact human-readable table

The matrix is deliberately claim-aware: every case has a target backend and a
target metric, but the runner reports the measured winner instead of forcing
the claim to pass.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAM = REPO_ROOT / "benchmarks" / "python" / "gc_advantage_matrix.py"


@dataclass(frozen=True)
class Case:
    name: str
    target_gc: int
    target_metric: str
    mode: str
    n: int
    rounds: int
    inner: int
    collect_every: int
    claim: str


CASES = (
    Case(
        name="gc0_refcount_steady_churn",
        target_gc=0,
        target_metric="elapsed_us",
        mode="list_churn",
        n=2048,
        rounds=250,
        inner=3,
        collect_every=0,
        claim="Immediate refcount reclamation wins the cycle-free allocation churn baseline.",
    ),
    Case(
        name="gc1_incremental_explicit_churn",
        target_gc=1,
        target_metric="elapsed_us",
        mode="node_churn",
        n=2048,
        rounds=90,
        inner=5,
        collect_every=10,
        claim="Incremental tracing can beat backend #0 on explicit-collection object churn.",
    ),
    Case(
        name="gc2_cms_heap_under_high_collect_churn",
        target_gc=2,
        target_metric="heap_bytes",
        mode="node_churn",
        n=2048,
        rounds=200,
        inner=8,
        collect_every=1,
        claim="The CMS backend keeps the smallest in-use heap under high-frequency explicit collection of pointer-bearing node churn.",
    ),
    Case(
        name="gc3_generational_high_frequency_collect",
        target_gc=3,
        target_metric="elapsed_us",
        mode="node_churn",
        n=2048,
        rounds=350,
        inner=3,
        collect_every=1,
        claim="The generational backend wins throughput when high-frequency explicit collections repeatedly revisit a stable live set.",
    ),
    Case(
        name="gc4_colored_low_total_pause",
        target_gc=4,
        target_metric="pause_sum_us",
        mode="node_churn",
        n=1024,
        rounds=350,
        inner=3,
        collect_every=50,
        claim="The colored-relocating backend can minimize total GC pause time on sparse explicit collections.",
    ),
)


METRICS = (
    "elapsed_us",
    "max_pause_us",
    "pause_count",
    "pause_sum_us",
    "work_steps",
    "rss_bytes",
    "heap_bytes",
    "heap_capacity_bytes",
    "reloc_forwards",
    "reloc_barriers",
    "evacuated_bytes",
    "zpage_count",
    "zpage_capacity_bytes",
    "zpage_used_bytes",
    "zpage_allocated_bytes",
    "zpage_reclaimable_gap_bytes",
    "zpage_span_bytes",
    "zpage_free_pages",
    "zpage_free_capacity_bytes",
    "zpage_free_span_bytes",
)


def _run(
    cmd: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _parse_kv(stdout: str) -> dict[str, int | str]:
    row: dict[str, int | str] = {}
    for line in stdout.splitlines():
        if "," not in line:
            continue
        key, value = line.strip().split(",", 1)
        try:
            row[key] = int(value)
        except ValueError:
            row[key] = value
    return row


def _median(values: list[int]) -> int:
    return int(statistics.median(values))


def _mib(value: int | float) -> str:
    return f"{float(value) / (1024 * 1024):.2f}"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def _compile(outdir: Path, timeout: int) -> Path:
    exe = outdir / "gc_advantage_matrix.out"
    cmd = [
        "uv",
        "run",
        "pcc",
        "--backend",
        "self",
        "--python-libpython=off",
        "--ir-scaffold=on",
        str(PROGRAM),
        "-o",
        str(exe),
    ]
    started = time.perf_counter()
    result = _run(cmd, timeout=timeout, env=_clean_env())
    if result.returncode != 0:
        raise RuntimeError(
            "compile failed\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    print(f"compiled {exe} in {time.perf_counter() - started:.2f}s")
    return exe


def _run_case(
    exe: Path,
    case: Case,
    backend: int,
    reps: int,
    timeout: int,
) -> dict[str, Any]:
    samples: list[dict[str, int | str]] = []
    for _ in range(reps):
        env = _clean_env()
        env["PCC_GC_BACKEND"] = str(backend)
        cmd = [
            str(exe),
            case.mode,
            str(case.n),
            str(case.rounds),
            str(case.inner),
            str(case.collect_every),
        ]
        result = _run(cmd, timeout=timeout, env=env)
        if result.returncode != 0:
            raise RuntimeError(
                f"{case.name} backend {backend} failed with {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        samples.append(_parse_kv(result.stdout))

    medians: dict[str, int] = {}
    for metric in METRICS:
        vals = [int(sample.get(metric, 0)) for sample in samples]
        medians[metric] = _median(vals)

    return {
        "case": case.name,
        "target_gc": case.target_gc,
        "target_metric": case.target_metric,
        "backend": backend,
        "mode": case.mode,
        "n": case.n,
        "rounds": case.rounds,
        "inner": case.inner,
        "collect_every": case.collect_every,
        "claim": case.claim,
        "medians": medians,
        "samples": samples,
    }


def _summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for case in CASES:
        case_rows = [row for row in rows if row["case"] == case.name]
        target = next(row for row in case_rows if row["backend"] == case.target_gc)
        metric = case.target_metric
        winner = min(case_rows, key=lambda row: row["medians"][metric])
        gc0 = next(row for row in case_rows if row["backend"] == 0)
        gc3 = next(row for row in case_rows if row["backend"] == 3)
        summary.append(
            {
                "case": case.name,
                "target_gc": case.target_gc,
                "target_metric": metric,
                "winner_gc": winner["backend"],
                "claim": case.claim,
                "target_value": target["medians"][metric],
                "winner_value": winner["medians"][metric],
                "target_vs_gc0": (
                    target["medians"][metric] / gc0["medians"][metric]
                    if gc0["medians"][metric] != 0
                    else None
                ),
                "target_vs_gc3": (
                    target["medians"][metric] / gc3["medians"][metric]
                    if gc3["medians"][metric] != 0
                    else None
                ),
            }
        )
    return summary


def _write_markdown(
    outdir: Path,
    rows: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    reps: int,
) -> None:
    lines = [
        "# GC Advantage Matrix",
        "",
        f"Repetitions per cell: {reps}. Values are medians.",
        "",
        "## Claim Summary",
        "",
        "| workload | target GC | target metric | measured winner | target value | winner value | target/gc0 | target/gc3 |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        target_gc0 = row["target_vs_gc0"]
        target_gc3 = row["target_vs_gc3"]
        lines.append(
            "| {case} | {target_gc} | {metric} | {winner_gc} | {target_value} | "
            "{winner_value} | {gc0} | {gc3} |".format(
                case=row["case"],
                target_gc=row["target_gc"],
                metric=row["target_metric"],
                winner_gc=row["winner_gc"],
                target_value=row["target_value"],
                winner_value=row["winner_value"],
                gc0="" if target_gc0 is None else f"{target_gc0:.3f}",
                gc3="" if target_gc3 is None else f"{target_gc3:.3f}",
            )
        )

    lines.extend(
        [
            "",
            "## Raw Median Table",
            "",
            "| workload | GC | elapsed us | max pause us | pause count | work steps | RSS MiB | heap MiB | heap cap MiB | reloc | zcap MiB | zspan MiB | zfree MiB |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        med = row["medians"]
        lines.append(
            "| {case} | {gc} | {elapsed} | {pause} | {pause_count} | "
            "{work_steps} | {rss} | {heap} | {heapcap} | {reloc} | {zcap} | "
            "{zspan} | {zfree} |".format(
                case=row["case"],
                gc=row["backend"],
                elapsed=med["elapsed_us"],
                pause=med["max_pause_us"],
                pause_count=med["pause_count"],
                work_steps=med["work_steps"],
                rss=_mib(med["rss_bytes"]),
                heap=_mib(med["heap_bytes"]),
                heapcap=_mib(med["heap_capacity_bytes"]),
                reloc=med["reloc_forwards"],
                zcap=_mib(med["zpage_capacity_bytes"]),
                zspan=_mib(med["zpage_span_bytes"]),
                zfree=_mib(med["zpage_free_span_bytes"]),
            )
        )
    (outdir / "gc_advantage_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--outdir",
        default="/tmp/pcc-gc-advantage-matrix",
        help="directory for compiled binary and result files",
    )
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--compile-timeout", type=int, default=420)
    parser.add_argument("--run-timeout", type=int, default=90)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    exe = _compile(outdir, args.compile_timeout)

    rows: list[dict[str, Any]] = []
    for case in CASES:
        for backend in range(5):
            print(f"running {case.name} gc{backend}")
            rows.append(_run_case(exe, case, backend, args.reps, args.run_timeout))

    summary = _summarize(rows)
    (outdir / "gc_advantage_rows.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (outdir / "gc_advantage_claims.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_markdown(outdir, rows, summary, args.reps)
    print(f"wrote {outdir / 'gc_advantage_summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
