#!/usr/bin/env python3
"""Benchmark pcc against clang/cc at O1/O2/O3.

Measures compile time and execution time separately using standalone binaries.

Usage:
    env -u LC_ALL uv run python benchmarks/run_benchmarks.py
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


BENCHMARKS_DIR = Path(__file__).resolve().parent / "c"
PROJECT_ROOT = BENCHMARKS_DIR.parents[1]
DEFAULT_BENCHES = sorted(path.name for path in BENCHMARKS_DIR.glob("*.c"))
DEFAULT_OPT_LEVELS = (1, 2, 3)


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def find_clang() -> str:
    for candidate in ("clang", "cc", "gcc"):
        path = shutil.which(candidate)
        if path:
            return path
    raise RuntimeError("No system C compiler found")


def needs_math_lib(src_path: Path) -> bool:
    return "#include <math.h>" in src_path.read_text()


def timed_run(cmd, *, cwd=None, timeout=300, env=None, text=True):
    t0 = time.perf_counter()
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=text,
        timeout=timeout,
        env=env,
    )
    elapsed = time.perf_counter() - t0
    return elapsed, result


def avg(values):
    return statistics.fmean(values) if values else None


def geometric_mean(values):
    clean = [value for value in values if value and value > 0]
    if not clean:
        return None
    return math.exp(statistics.fmean(math.log(value) for value in clean))


def format_seconds(value):
    if value is None:
        return "N/A"
    if value < 0.001:
        return f"{value * 1e6:.0f}us"
    if value < 1:
        return f"{value * 1e3:.1f}ms"
    return f"{value:.3f}s"


def format_ratio(lhs, rhs):
    if lhs is None or rhs in (None, 0):
        return "N/A"
    return f"{lhs / rhs:.2f}x"


def format_error(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        cmd = exc.cmd[0] if isinstance(exc.cmd, list) and exc.cmd else exc.cmd
        return f"timeout after {exc.timeout}s: {cmd}"
    return str(exc)


def classify_ratio(value, tie_band=0.05):
    if value is None:
        return "error"
    if value <= 1.0 - tie_band:
        return "faster"
    if value >= 1.0 + tie_band:
        return "slower"
    return "tied"


def build_native(src_path: Path, opt_level: int, workdir: Path, runs: int):
    cc = find_clang()
    bin_path = workdir / f"clang_O{opt_level}.out"
    cmd = [cc, f"-O{opt_level}", "-o", str(bin_path), str(src_path)]
    if needs_math_lib(src_path):
        cmd.append("-lm")

    compile_times = []
    for _ in range(runs):
        bin_path.unlink(missing_ok=True)
        elapsed, result = timed_run(cmd, env=clean_env(), timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"native compile failed for {src_path.name} O{opt_level}: "
                f"{result.stderr or result.stdout}"
            )
        compile_times.append(elapsed)

    return bin_path, avg(compile_times)


def build_pcc(src_path: Path, opt_level: int, workdir: Path, runs: int):
    cc = find_clang()
    obj_path = workdir / f"pcc_O{opt_level}.o"
    bin_path = workdir / f"pcc_O{opt_level}.out"
    compile_cmd = [
        sys.executable,
        "-m",
        "pcc",
        "--no-cache",
        "-O",
        str(opt_level),
        "--emit-obj",
        str(obj_path),
        str(src_path),
    ]
    link_cmd = [cc, str(obj_path), "-o", str(bin_path)]
    if needs_math_lib(src_path):
        link_cmd.append("-lm")

    compile_times = []
    for _ in range(runs):
        obj_path.unlink(missing_ok=True)
        bin_path.unlink(missing_ok=True)
        compile_elapsed, compile_result = timed_run(
            compile_cmd,
            cwd=PROJECT_ROOT,
            env=clean_env(),
            timeout=300,
        )
        if compile_result.returncode != 0:
            raise RuntimeError(
                f"pcc compile failed for {src_path.name} O{opt_level}: "
                f"{compile_result.stderr or compile_result.stdout}"
            )
        link_elapsed, link_result = timed_run(
            link_cmd,
            cwd=PROJECT_ROOT,
            env=clean_env(),
            timeout=120,
        )
        if link_result.returncode != 0:
            raise RuntimeError(
                f"link failed for {src_path.name} O{opt_level}: "
                f"{link_result.stderr or link_result.stdout}"
            )
        compile_times.append(compile_elapsed + link_elapsed)

    return bin_path, avg(compile_times)


def benchmark_binary(bin_path: Path, runs: int):
    exec_times = []
    stdout = None
    stderr = None
    returncode = None
    for _ in range(runs):
        elapsed, result = timed_run(
            [str(bin_path)],
            env=clean_env(),
            timeout=300,
        )
        exec_times.append(elapsed)
        stdout = result.stdout
        stderr = result.stderr
        returncode = result.returncode
    return {
        "exec_time_s": avg(exec_times),
        "stdout": stdout or "",
        "stderr": stderr or "",
        "returncode": returncode,
    }


def benchmark_source(src_path: Path, opt_levels: tuple[int, ...], runs: int):
    result = {"name": src_path.name, "compiler": find_clang(), "levels": {}}
    with tempfile.TemporaryDirectory(prefix="pcc_bench_") as tmpdir:
        workdir = Path(tmpdir)

        for opt_level in opt_levels:
            level = {"ok": False}
            try:
                native_bin, native_compile = build_native(src_path, opt_level, workdir, runs)
                native_exec = benchmark_binary(native_bin, runs)

                pcc_bin, pcc_compile = build_pcc(src_path, opt_level, workdir, runs)
                pcc_exec = benchmark_binary(pcc_bin, runs)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                level["error"] = format_error(exc)
                result["levels"][opt_level] = level
                continue

            level.update(
                {
                    "ok": True,
                    "native": {
                        "compile_time_s": native_compile,
                        **native_exec,
                    },
                    "pcc": {
                        "compile_time_s": pcc_compile,
                        **pcc_exec,
                    },
                    "outputs_match": (
                        pcc_exec["stdout"] == native_exec["stdout"]
                        and pcc_exec["returncode"] == native_exec["returncode"]
                    ),
                }
            )
            result["levels"][opt_level] = level

    return result


def print_compile_table(results, opt_levels):
    print("Compile Time")
    for opt_level in opt_levels:
        print(f"O{opt_level}")
        print(
            f"{'Benchmark':<16} {'clang':>12} {'pcc':>12} {'pcc/clang':>12}"
        )
        for result in results:
            level = result["levels"][opt_level]
            if not level.get("ok"):
                print(
                    f"{result['name']:<16} "
                    f"{'ERROR':>12} "
                    f"{'ERROR':>12} "
                    f"{'N/A':>12}"
                )
                continue
            native = level["native"]["compile_time_s"]
            pcc = level["pcc"]["compile_time_s"]
            print(
                f"{result['name']:<16} "
                f"{format_seconds(native):>12} "
                f"{format_seconds(pcc):>12} "
                f"{format_ratio(pcc, native):>12}"
            )
        print()


def print_exec_table(results, opt_levels):
    print("Execution Time")
    for opt_level in opt_levels:
        print(f"O{opt_level}")
        print(
            f"{'Benchmark':<16} {'clang':>12} {'pcc':>12} {'pcc/clang':>12} {'match':>8}"
        )
        for result in results:
            level = result["levels"][opt_level]
            if not level.get("ok"):
                print(
                    f"{result['name']:<16} "
                    f"{'ERROR':>12} "
                    f"{'ERROR':>12} "
                    f"{'N/A':>12} "
                    f"{'error':>8}"
                )
                continue
            native = level["native"]["exec_time_s"]
            pcc = level["pcc"]["exec_time_s"]
            print(
                f"{result['name']:<16} "
                f"{format_seconds(native):>12} "
                f"{format_seconds(pcc):>12} "
                f"{format_ratio(pcc, native):>12} "
                f"{str(level['outputs_match']):>8}"
            )
        print()


def print_total_table(results, opt_levels):
    print("Compile + Execute Total")
    for opt_level in opt_levels:
        print(f"O{opt_level}")
        print(
            f"{'Benchmark':<16} {'clang':>12} {'pcc':>12} {'pcc/clang':>12}"
        )
        for result in results:
            level = result["levels"][opt_level]
            if not level.get("ok"):
                print(
                    f"{result['name']:<16} "
                    f"{'ERROR':>12} "
                    f"{'ERROR':>12} "
                    f"{'N/A':>12}"
                )
                continue
            native = (
                level["native"]["compile_time_s"] + level["native"]["exec_time_s"]
            )
            pcc = level["pcc"]["compile_time_s"] + level["pcc"]["exec_time_s"]
            print(
                f"{result['name']:<16} "
                f"{format_seconds(native):>12} "
                f"{format_seconds(pcc):>12} "
                f"{format_ratio(pcc, native):>12}"
            )
        print()


def print_summary(results, opt_levels):
    print("Summary")
    for opt_level in opt_levels:
        compile_ratios = []
        exec_ratios = []
        total_ratios = []
        exec_counts = {"faster": 0, "tied": 0, "slower": 0}
        match_count = 0
        ok_count = 0

        for result in results:
            level = result["levels"][opt_level]
            if not level.get("ok"):
                continue
            native_compile = level["native"]["compile_time_s"]
            native_exec = level["native"]["exec_time_s"]
            pcc_compile = level["pcc"]["compile_time_s"]
            pcc_exec = level["pcc"]["exec_time_s"]

            compile_ratio = pcc_compile / native_compile
            exec_ratio = pcc_exec / native_exec
            total_ratio = (pcc_compile + pcc_exec) / (native_compile + native_exec)

            compile_ratios.append(compile_ratio)
            exec_ratios.append(exec_ratio)
            total_ratios.append(total_ratio)
            exec_counts[classify_ratio(exec_ratio)] += 1
            ok_count += 1
            if level["outputs_match"]:
                match_count += 1

        print(
            f"  O{opt_level}: "
            f"compile geomean={format_ratio(geometric_mean(compile_ratios), 1.0)} "
            f"exec geomean={format_ratio(geometric_mean(exec_ratios), 1.0)} "
            f"total geomean={format_ratio(geometric_mean(total_ratios), 1.0)} "
            f"exec faster={exec_counts['faster']} "
            f"tied={exec_counts['tied']} slower={exec_counts['slower']} "
            f"matched={match_count}/{ok_count} "
            f"completed={ok_count}/{len(results)}"
        )
    print()


def print_pcc_opt_delta_summary(results, opt_levels):
    if len(opt_levels) < 2:
        return

    baseline_opt = opt_levels[0]
    print("PCC Opt-Level Deltas")
    for opt_level in opt_levels[1:]:
        compile_ratios = []
        exec_ratios = []
        total_ratios = []
        ok_count = 0

        for result in results:
            baseline = result["levels"].get(baseline_opt)
            current = result["levels"].get(opt_level)
            if not baseline or not baseline.get("ok") or not current or not current.get("ok"):
                continue

            baseline_compile = baseline["pcc"]["compile_time_s"]
            baseline_exec = baseline["pcc"]["exec_time_s"]
            current_compile = current["pcc"]["compile_time_s"]
            current_exec = current["pcc"]["exec_time_s"]

            compile_ratios.append(current_compile / baseline_compile)
            exec_ratios.append(current_exec / baseline_exec)
            total_ratios.append(
                (current_compile + current_exec) / (baseline_compile + baseline_exec)
            )
            ok_count += 1

        print(
            f"  pcc O{opt_level}/O{baseline_opt}: "
            f"compile geomean={format_ratio(geometric_mean(compile_ratios), 1.0)} "
            f"exec geomean={format_ratio(geometric_mean(exec_ratios), 1.0)} "
            f"total geomean={format_ratio(geometric_mean(total_ratios), 1.0)} "
            f"completed={ok_count}/{len(results)}"
        )
    print()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bench",
        action="append",
        dest="benches",
        help="Benchmark filename under benchmarks/ (repeatable). Defaults to all.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Timed runs per compile/execute measurement.",
    )
    parser.add_argument(
        "--opt-level",
        action="append",
        dest="opt_levels",
        type=int,
        choices=(0, 1, 2, 3),
        help="Optimization level to benchmark. Repeat for multiple levels.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    benches = args.benches or DEFAULT_BENCHES
    opt_levels = tuple(args.opt_levels or DEFAULT_OPT_LEVELS)

    compiler = find_clang()
    compiler_version = subprocess.run(
        [compiler, "--version"],
        capture_output=True,
        text=True,
        env=clean_env(),
    ).stdout.splitlines()[0]

    print("=" * 80)
    print("PCC vs Clang Benchmarks")
    print("=" * 80)
    print(f"Compiler: {compiler_version}")
    print(f"Benchmarks: {len(benches)}")
    print(f"Runs per measurement: {args.runs}")
    print(f"Optimization levels: {', '.join(f'O{level}' for level in opt_levels)}")
    print()

    results = []
    for bench_name in benches:
        src_path = BENCHMARKS_DIR / bench_name
        if not src_path.is_file():
            raise FileNotFoundError(f"Benchmark source not found: {src_path}")
        print(f"Running {bench_name} ...")
        results.append(benchmark_source(src_path, opt_levels, args.runs))

    print()
    print_compile_table(results, opt_levels)
    print_exec_table(results, opt_levels)
    print_total_table(results, opt_levels)
    print_summary(results, opt_levels)
    print_pcc_opt_delta_summary(results, opt_levels)


if __name__ == "__main__":
    main()
