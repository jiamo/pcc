#!/usr/bin/env python3
"""Benchmark runner: compares pcc vs cc -O0 vs cc -O2.

Measures compile time and execution time separately.

Usage:
    uv run python benchmarks/run_benchmarks.py
"""

import os
import subprocess
import sys
import tempfile
import time

BENCHMARKS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BENCHMARKS_DIR)

BENCH_FILES = [
    "fib35.c",
    "sieve_large.c",
    "matmul.c",
    "nbody.c",
]

CC = "cc"
WARMUP_RUNS = 1
TIMED_RUNS = 3


def find_cc():
    import shutil
    for candidate in ("cc", "clang", "gcc"):
        if shutil.which(candidate):
            return candidate
    raise RuntimeError("No system C compiler found")


def run_native(src_path, opt_level, tmpdir):
    """Compile and run with native cc. Returns (compile_time, exec_time, output)."""
    bin_path = os.path.join(tmpdir, "a.out")
    needs_math = False
    with open(src_path) as f:
        content = f.read()
        if "#include <math.h>" in content:
            needs_math = True

    cc = find_cc()
    compile_cmd = [cc, f"-O{opt_level}", "-o", bin_path, src_path]
    if needs_math:
        compile_cmd.append("-lm")

    # Warmup
    for _ in range(WARMUP_RUNS):
        subprocess.run(compile_cmd, capture_output=True, timeout=60)
        if os.path.exists(bin_path):
            subprocess.run([bin_path], capture_output=True, timeout=60)
            os.unlink(bin_path)

    # Timed compile
    compile_times = []
    for _ in range(TIMED_RUNS):
        if os.path.exists(bin_path):
            os.unlink(bin_path)
        t0 = time.perf_counter()
        r = subprocess.run(compile_cmd, capture_output=True, timeout=60)
        t1 = time.perf_counter()
        if r.returncode != 0:
            return None, None, f"compile failed: {r.stderr[:200]}"
        compile_times.append(t1 - t0)

    # Timed execute
    exec_times = []
    output = ""
    for _ in range(TIMED_RUNS):
        t0 = time.perf_counter()
        r = subprocess.run([bin_path], capture_output=True, text=True, timeout=120)
        t1 = time.perf_counter()
        exec_times.append(t1 - t0)
        output = r.stdout.strip()

    avg_compile = sum(compile_times) / len(compile_times)
    avg_exec = sum(exec_times) / len(exec_times)
    return avg_compile, avg_exec, output


def run_pcc(src_path, tmpdir):
    """Compile and run with pcc. Returns (compile_time, exec_time, output).

    pcc compile+execute are combined, so we measure total time.
    We also measure --system-link mode for separate compile/exec timing.
    """
    pcc_cmd = [
        sys.executable, "-m", "pcc",
        "--no-cache",
        src_path,
    ]

    needs_math = False
    with open(src_path) as f:
        if "#include <math.h>" in f.read():
            needs_math = True

    # Warmup
    for _ in range(WARMUP_RUNS):
        subprocess.run(pcc_cmd, capture_output=True, timeout=300, cwd=PROJECT_ROOT)

    # Timed total (compile + execute via MCJIT)
    total_times = []
    output = ""
    for _ in range(TIMED_RUNS):
        t0 = time.perf_counter()
        r = subprocess.run(
            pcc_cmd,
            capture_output=True, text=True, timeout=300,
            cwd=PROJECT_ROOT,
        )
        t1 = time.perf_counter()
        total_times.append(t1 - t0)
        output = r.stdout.strip()
        if r.returncode != 0 and not output:
            output = f"exit={r.returncode}"

    avg_total = sum(total_times) / len(total_times)

    # System-link mode for separate compile/exec measurement
    bin_path = os.path.join(tmpdir, "pcc_out")
    sl_cmd = [
        sys.executable, "-m", "pcc",
        "--no-cache",
        "--system-link",
        src_path,
    ]
    if needs_math:
        sl_cmd.extend(["--link-arg=-lm"])

    compile_times = []
    exec_times = []
    for _ in range(TIMED_RUNS):
        t0 = time.perf_counter()
        r = subprocess.run(
            sl_cmd,
            capture_output=True, text=True, timeout=300,
            cwd=PROJECT_ROOT,
        )
        t1 = time.perf_counter()
        compile_times.append(t1 - t0)

    avg_compile = sum(compile_times) / len(compile_times)
    return avg_total, avg_compile, output


def format_time(seconds):
    if seconds is None:
        return "N/A"
    if seconds < 0.001:
        return f"{seconds*1e6:.0f}us"
    if seconds < 1.0:
        return f"{seconds*1000:.1f}ms"
    return f"{seconds:.3f}s"


def main():
    print("=" * 80)
    print("PCC Benchmark Suite")
    print("=" * 80)
    print(f"Runs: {TIMED_RUNS} (averaged), Warmup: {WARMUP_RUNS}")

    cc = find_cc()
    cc_version = subprocess.run(
        [cc, "--version"], capture_output=True, text=True
    ).stdout.split("\n")[0]
    print(f"Native compiler: {cc} ({cc_version})")
    print()

    results = []

    for bench_file in BENCH_FILES:
        src_path = os.path.join(BENCHMARKS_DIR, bench_file)
        if not os.path.exists(src_path):
            print(f"  SKIP {bench_file} (not found)")
            continue

        print(f"Running {bench_file}...")

        with tempfile.TemporaryDirectory(prefix="pcc_bench_") as tmpdir:
            # Native -O0
            cc_o0_compile, cc_o0_exec, cc_o0_out = run_native(src_path, 0, tmpdir)
            # Native -O2
            cc_o2_compile, cc_o2_exec, cc_o2_out = run_native(src_path, 2, tmpdir)
            # pcc
            pcc_total, pcc_sl_total, pcc_out = run_pcc(src_path, tmpdir)

        results.append({
            "name": bench_file,
            "cc_o0_compile": cc_o0_compile,
            "cc_o0_exec": cc_o0_exec,
            "cc_o0_out": cc_o0_out,
            "cc_o2_compile": cc_o2_compile,
            "cc_o2_exec": cc_o2_exec,
            "cc_o2_out": cc_o2_out,
            "pcc_total": pcc_total,
            "pcc_sl_total": pcc_sl_total,
            "pcc_out": pcc_out,
        })

    # Print results table
    print()
    print("=" * 80)
    print("Results")
    print("=" * 80)
    print()

    # Compile time table
    header = f"{'Benchmark':<20} {'cc -O0':>12} {'cc -O2':>12} {'pcc(MCJIT)':>14} {'pcc(syslink)':>14}"
    print("Compile + Execute Time (total)")
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        cc_o0_total = (r["cc_o0_compile"] or 0) + (r["cc_o0_exec"] or 0)
        cc_o2_total = (r["cc_o2_compile"] or 0) + (r["cc_o2_exec"] or 0)
        print(
            f"{r['name']:<20} "
            f"{format_time(cc_o0_total):>12} "
            f"{format_time(cc_o2_total):>12} "
            f"{format_time(r['pcc_total']):>14} "
            f"{format_time(r['pcc_sl_total']):>14}"
        )
    print()

    # Execution time only (native)
    print("Execution Time Only (native compile excluded)")
    print("-" * 60)
    print(f"{'Benchmark':<20} {'cc -O0 exec':>15} {'cc -O2 exec':>15}")
    print("-" * 60)
    for r in results:
        print(
            f"{r['name']:<20} "
            f"{format_time(r['cc_o0_exec']):>15} "
            f"{format_time(r['cc_o2_exec']):>15}"
        )
    print()

    # Ratio table
    print("Slowdown vs cc -O0 (total time)")
    print("-" * 60)
    print(f"{'Benchmark':<20} {'pcc(MCJIT)/O0':>15} {'pcc(syslink)/O0':>17}")
    print("-" * 60)
    for r in results:
        cc_o0_total = (r["cc_o0_compile"] or 0) + (r["cc_o0_exec"] or 0)
        if cc_o0_total > 0:
            mcjit_ratio = (r["pcc_total"] or 0) / cc_o0_total
            sl_ratio = (r["pcc_sl_total"] or 0) / cc_o0_total
            print(
                f"{r['name']:<20} "
                f"{mcjit_ratio:>14.1f}x "
                f"{sl_ratio:>16.1f}x"
            )
    print()

    # Output correctness
    print("Output Correctness")
    print("-" * 60)
    for r in results:
        match = r["pcc_out"] == r["cc_o0_out"]
        status = "PASS" if match else "MISMATCH"
        print(f"  {r['name']:<20} {status}  (pcc={r['pcc_out']!r}, cc={r['cc_o0_out']!r})")
    print()


if __name__ == "__main__":
    main()
