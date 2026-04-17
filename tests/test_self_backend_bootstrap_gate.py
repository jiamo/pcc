from __future__ import annotations

from scripts.run_self_backend_bootstrap_gate import (
    BootstrapResult,
    _check_performance_thresholds,
)


def _result(
    backend: str,
    *,
    elapsed: float,
    help_elapsed: float,
    smoke_compile: float,
    smoke_run: float,
) -> BootstrapResult:
    return BootstrapResult(
        backend=backend,
        stage=3,
        out_dir="/tmp/out",
        bin_path="/tmp/out/pcc3",
        returncode=0,
        elapsed_seconds=elapsed,
        size_bytes=1,
        help_returncode=0,
        help_elapsed_seconds=help_elapsed,
        smoke_compile_returncode=0,
        smoke_compile_seconds=smoke_compile,
        smoke_run_returncode=0,
        smoke_run_seconds=smoke_run,
        benchmark_compile_times=(("case", smoke_compile),),
        benchmark_run_times=(("case", smoke_run),),
        links_libpython=True,
        failure_hint=None,
    )


def test_self_backend_bootstrap_gate_accepts_ratios_within_threshold():
    results = [
        _result(
            "llvm",
            elapsed=10.0,
            help_elapsed=1.0,
            smoke_compile=2.0,
            smoke_run=1.0,
        ),
        _result(
            "self",
            elapsed=19.0,
            help_elapsed=1.9,
            smoke_compile=3.9,
            smoke_run=1.9,
        ),
    ]

    assert _check_performance_thresholds(
        results,
        bootstrap_threshold=2.0,
        help_threshold=2.0,
        smoke_compile_threshold=2.0,
        smoke_run_threshold=2.0,
    )


def test_self_backend_bootstrap_gate_rejects_ratio_above_threshold():
    results = [
        _result(
            "llvm",
            elapsed=10.0,
            help_elapsed=1.0,
            smoke_compile=2.0,
            smoke_run=1.0,
        ),
        _result(
            "self",
            elapsed=21.0,
            help_elapsed=1.0,
            smoke_compile=2.0,
            smoke_run=1.0,
        ),
    ]

    assert not _check_performance_thresholds(
        results,
        bootstrap_threshold=2.0,
        help_threshold=2.0,
        smoke_compile_threshold=2.0,
        smoke_run_threshold=2.0,
    )
