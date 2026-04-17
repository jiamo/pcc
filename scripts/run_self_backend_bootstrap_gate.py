#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass


SMOKE_BENCHMARKS: tuple[tuple[str, str, str], ...] = (
    ("print_int", "print(123)\n", "123\n"),
    (
        "function_call",
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "print(add(10, 32))\n",
        "42\n",
    ),
    (
        "two_functions",
        "def mul(a: int, b: int) -> int:\n"
        "    return a * b\n\n"
        "def main() -> None:\n"
        "    print(mul(6, 7))\n\n"
        "main()\n",
        "42\n",
    ),
)


@dataclass(frozen=True)
class BootstrapResult:
    backend: str
    stage: int
    out_dir: str
    bin_path: str
    returncode: int
    elapsed_seconds: float
    size_bytes: int | None
    help_returncode: int | None
    help_elapsed_seconds: float | None
    smoke_compile_returncode: int | None
    smoke_compile_seconds: float | None
    smoke_run_returncode: int | None
    smoke_run_seconds: float | None
    benchmark_compile_times: tuple[tuple[str, float], ...]
    benchmark_run_times: tuple[tuple[str, float], ...]
    links_libpython: bool | None
    failure_hint: str | None


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _supported_host() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def _stage_bin(out_dir: str, stage: int) -> str:
    return os.path.join(out_dir, f"pcc{stage}")


def _file_size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _links_libpython(path: str) -> bool | None:
    if not os.path.exists(path):
        return None
    if sys.platform == "darwin":
        cmd = ["otool", "-L", path]
    else:
        cmd = ["ldd", path]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "") + (result.stderr or "")
    return "libpython" in text or "Python.framework" in text


def _help_smoke(path: str) -> tuple[int | None, float | None]:
    if not os.path.exists(path):
        return None, None
    try:
        start = time.monotonic()
        result = subprocess.run(
            [path, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        elapsed = time.monotonic() - start
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return result.returncode, elapsed


def _geomean(values: list[float]) -> float | None:
    if not values:
        return None
    product = 1.0
    for value in values:
        if value <= 0:
            continue
        product *= value
    return product ** (1.0 / len(values))


def _benchmark_smoke(
    path: str,
    backend: str,
) -> tuple[
    int | None,
    float | None,
    int | None,
    float | None,
    tuple[tuple[str, float], ...],
    tuple[tuple[str, float], ...],
]:
    if not os.path.exists(path):
        return None, None, None, None, (), ()
    with tempfile.TemporaryDirectory(
        prefix=f"pcc_bootstrap_{backend}_smoke_",
    ) as tmp:
        compile_times: list[tuple[str, float]] = []
        run_times: list[tuple[str, float]] = []
        for name, source, expected_stdout in SMOKE_BENCHMARKS:
            src = os.path.join(tmp, name + ".py")
            out = os.path.join(tmp, name + ".out")
            with open(src, "w", encoding="utf-8") as f:
                f.write(source)
            compile_cmd = [path, "--backend", backend, src, "-o", out]
            try:
                start = time.monotonic()
                build = subprocess.run(
                    compile_cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                compile_elapsed = time.monotonic() - start
            except (OSError, subprocess.TimeoutExpired):
                return None, None, None, None, tuple(compile_times), tuple(run_times)
            compile_times.append((name, compile_elapsed))
            if build.returncode != 0:
                return (
                    build.returncode,
                    _geomean([value for _name, value in compile_times]),
                    None,
                    None,
                    tuple(compile_times),
                    tuple(run_times),
                )
            try:
                start = time.monotonic()
                run = subprocess.run(
                    [out],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                run_elapsed = time.monotonic() - start
            except (OSError, subprocess.TimeoutExpired):
                return (
                    build.returncode,
                    _geomean([value for _name, value in compile_times]),
                    None,
                    None,
                    tuple(compile_times),
                    tuple(run_times),
                )
            run_times.append((name, run_elapsed))
            if run.returncode != 0 or run.stdout != expected_stdout:
                return (
                    build.returncode,
                    _geomean([value for _name, value in compile_times]),
                    1 if run.returncode == 0 else run.returncode,
                    _geomean([value for _name, value in run_times]),
                    tuple(compile_times),
                    tuple(run_times),
                )
        return (
            0,
            _geomean([value for _name, value in compile_times]),
            0,
            _geomean([value for _name, value in run_times]),
            tuple(compile_times),
            tuple(run_times),
        )


def _failure_hint(text: str) -> str | None:
    patterns = (
        r"undefined symbols?[^\n]*(?:\n[^\n]*)?",
        r"Undefined symbols?[^\n]*(?:\n[^\n]*)?",
        r"self backend[^\n]*",
        r"unsupported[^\n]*",
        r"PyPipelineError[^\n]*",
        r"Error: [^\n]*",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return lines[-1][:240]
    return None


def _run_bootstrap(
    *,
    backend: str,
    stage: int,
    timeout_seconds: int,
    dry_run: bool,
) -> BootstrapResult:
    repo = _repo_root()
    out_dir = os.path.join(repo, "build", f"bootstrap-{backend}")
    bin_path = _stage_bin(out_dir, stage)
    cmd = [
        "bash",
        os.path.join(repo, "scripts", "bootstrap.sh"),
        "--out-dir",
        out_dir,
        "--backend",
        backend,
        "--stage",
        str(stage),
    ]
    print("\n== bootstrap backend=" + backend + " stage=" + str(stage), flush=True)
    print("+ " + " ".join(cmd), flush=True)
    if dry_run:
        return BootstrapResult(
            backend=backend,
            stage=stage,
            out_dir=out_dir,
            bin_path=bin_path,
            returncode=0,
            elapsed_seconds=0.0,
            size_bytes=None,
            help_returncode=None,
            help_elapsed_seconds=None,
            smoke_compile_returncode=None,
            smoke_compile_seconds=None,
            smoke_run_returncode=None,
            smoke_run_seconds=None,
            benchmark_compile_times=(),
            benchmark_run_times=(),
            links_libpython=None,
            failure_hint=None,
        )

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=repo,
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        returncode = result.returncode
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        output = (exc.stdout or "") + (exc.stderr or "")
    elapsed = time.monotonic() - start

    if returncode == 0:
        help_code, help_elapsed = _help_smoke(bin_path)
        (
            smoke_compile_code,
            smoke_compile_elapsed,
            smoke_run_code,
            smoke_run_elapsed,
            benchmark_compile_times,
            benchmark_run_times,
        ) = _benchmark_smoke(bin_path, backend)
    else:
        help_code = None
        help_elapsed = None
        smoke_compile_code = None
        smoke_compile_elapsed = None
        smoke_run_code = None
        smoke_run_elapsed = None
        benchmark_compile_times = ()
        benchmark_run_times = ()
    links_libpython = (
        _links_libpython(bin_path) if os.path.exists(bin_path) else None
    )
    hint = None
    if returncode != 0:
        hint = _failure_hint(output)
    elif help_code not in (None, 0):
        hint = f"{os.path.basename(bin_path)} --help exited {help_code}"
    elif smoke_compile_code not in (None, 0):
        hint = f"{os.path.basename(bin_path)} toy compile exited {smoke_compile_code}"
    elif smoke_run_code not in (None, 0):
        hint = f"{os.path.basename(bin_path)} toy run exited {smoke_run_code}"

    return BootstrapResult(
        backend=backend,
        stage=stage,
        out_dir=out_dir,
        bin_path=bin_path,
        returncode=returncode,
        elapsed_seconds=elapsed,
        size_bytes=_file_size(bin_path),
        help_returncode=help_code,
        help_elapsed_seconds=help_elapsed,
        smoke_compile_returncode=smoke_compile_code,
        smoke_compile_seconds=smoke_compile_elapsed,
        smoke_run_returncode=smoke_run_code,
        smoke_run_seconds=smoke_run_elapsed,
        benchmark_compile_times=benchmark_compile_times,
        benchmark_run_times=benchmark_run_times,
        links_libpython=links_libpython,
        failure_hint=hint,
    )


def _worst_benchmark(times: tuple[tuple[str, float], ...]) -> str:
    if not times:
        return "n/a"
    name, value = max(times, key=lambda item: item[1])
    return f"{name}:{value:.3f}s"


def _print_result(result: BootstrapResult) -> None:
    help_code = (
        result.help_returncode
        if result.help_returncode is not None
        else "n/a"
    )
    smoke_compile_code = (
        result.smoke_compile_returncode
        if result.smoke_compile_returncode is not None
        else "n/a"
    )
    smoke_run_code = (
        result.smoke_run_returncode
        if result.smoke_run_returncode is not None
        else "n/a"
    )
    libpython = (
        result.links_libpython
        if result.links_libpython is not None
        else "n/a"
    )
    print(
        "result "
        f"backend={result.backend} "
        f"stage={result.stage} "
        f"exit={result.returncode} "
        f"elapsed={result.elapsed_seconds:.1f}s "
        f"size={result.size_bytes if result.size_bytes is not None else 'n/a'} "
        f"help={help_code} "
        f"help_elapsed={_fmt_seconds(result.help_elapsed_seconds)} "
        f"smoke_compile={smoke_compile_code} "
        f"smoke_compile_geomean={_fmt_seconds(result.smoke_compile_seconds)} "
        f"smoke_compile_worst={_worst_benchmark(result.benchmark_compile_times)} "
        f"smoke_run={smoke_run_code} "
        f"smoke_run_geomean={_fmt_seconds(result.smoke_run_seconds)} "
        f"smoke_run_worst={_worst_benchmark(result.benchmark_run_times)} "
        f"libpython={libpython}",
        flush=True,
    )
    if result.failure_hint:
        print("failure_hint=" + result.failure_hint, flush=True)


def _selected_backends(value: str) -> tuple[str, ...]:
    if value == "both":
        return ("llvm", "self")
    return (value,)


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}s"


def _ratio(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    if baseline <= 0:
        return None
    return value / baseline


def _check_ratio(
    *,
    label: str,
    value: float | None,
    baseline: float | None,
    threshold: float,
) -> bool:
    ratio = _ratio(value, baseline)
    if ratio is None:
        print(f"{label}_ratio=n/a", flush=True)
        return True
    print(f"{label}_ratio self/llvm={ratio:.3f}", flush=True)
    if ratio > threshold:
        print(
            f"FAIL {label} ratio {ratio:.3f} exceeds threshold {threshold:.3f}",
            file=sys.stderr,
        )
        return False
    return True


def _check_performance_thresholds(
    results: list[BootstrapResult],
    *,
    bootstrap_threshold: float,
    help_threshold: float,
    smoke_compile_threshold: float,
    smoke_run_threshold: float,
) -> bool:
    by_backend = {result.backend: result for result in results}
    llvm = by_backend.get("llvm")
    self_result = by_backend.get("self")
    if llvm is None or self_result is None:
        return True
    ok = True
    ok = _check_ratio(
        label="bootstrap_elapsed",
        value=self_result.elapsed_seconds,
        baseline=llvm.elapsed_seconds,
        threshold=bootstrap_threshold,
    ) and ok
    ok = _check_ratio(
        label="help_elapsed",
        value=self_result.help_elapsed_seconds,
        baseline=llvm.help_elapsed_seconds,
        threshold=help_threshold,
    ) and ok
    ok = _check_ratio(
        label="smoke_compile_elapsed",
        value=self_result.smoke_compile_seconds,
        baseline=llvm.smoke_compile_seconds,
        threshold=smoke_compile_threshold,
    ) and ok
    ok = _check_ratio(
        label="smoke_run_elapsed",
        value=self_result.smoke_run_seconds,
        baseline=llvm.smoke_run_seconds,
        threshold=smoke_run_threshold,
    ) and ok
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the supported-host Python bootstrap gate with LLVM and/or "
            "self native emission."
        )
    )
    parser.add_argument(
        "--backend",
        choices=("llvm", "self", "both"),
        default="both",
        help="backend selection to run; default: both",
    )
    parser.add_argument(
        "--stage",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="bootstrap stage limit; default: 1",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="per-backend bootstrap timeout in seconds; default: 900",
    )
    parser.add_argument(
        "--allow-non-supported-host",
        action="store_true",
        help="run even when the host is not the supported macOS arm64 target",
    )
    parser.add_argument(
        "--max-bootstrap-ratio",
        type=float,
        default=2.0,
        help="maximum allowed self/LLVM bootstrap wall-time ratio; default: 2.0",
    )
    parser.add_argument(
        "--max-help-ratio",
        type=float,
        default=2.0,
        help=(
            "maximum allowed self/LLVM pcc --help latency ratio; "
            "default: 2.0"
        ),
    )
    parser.add_argument(
        "--max-smoke-compile-ratio",
        type=float,
        default=2.0,
        help=(
            "maximum allowed self/LLVM toy compile latency ratio; "
            "default: 2.0"
        ),
    )
    parser.add_argument(
        "--max-smoke-run-ratio",
        type=float,
        default=2.0,
        help=(
            "maximum allowed self/LLVM toy executable runtime ratio; "
            "default: 2.0"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print commands without executing them",
    )
    args = parser.parse_args(argv)

    if not args.allow_non_supported_host and not _supported_host():
        print(
            "self-backed bootstrap default gate is defined for the supported "
            "macOS arm64 host; pass --allow-non-supported-host to override",
            file=sys.stderr,
        )
        return 2

    print("self backend bootstrap gate", flush=True)
    print(f"host={platform.system()} {platform.machine()}", flush=True)
    results = []
    for backend in _selected_backends(args.backend):
        result = _run_bootstrap(
            backend=backend,
            stage=args.stage,
            timeout_seconds=args.timeout,
            dry_run=args.dry_run,
        )
        results.append(result)
        _print_result(result)

    if len(results) == 2:
        first, second = results
        if first.size_bytes and second.size_bytes:
            ratio = second.size_bytes / first.size_bytes
            print(
                f"size_ratio {second.backend}/{first.backend}={ratio:.3f}",
                flush=True,
            )
        if not _check_performance_thresholds(
            results,
            bootstrap_threshold=args.max_bootstrap_ratio,
            help_threshold=args.max_help_ratio,
            smoke_compile_threshold=args.max_smoke_compile_ratio,
            smoke_run_threshold=args.max_smoke_run_ratio,
        ):
            return 1

    for result in results:
        if result.returncode != 0:
            return result.returncode
        if result.help_returncode not in (None, 0):
            return 1
        if result.smoke_compile_returncode not in (None, 0):
            return 1
        if result.smoke_run_returncode not in (None, 0):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
