"""Behavior contracts for extracted frontend worker policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcc.py_frontend import pipeline
from pcc.py_frontend import pipeline_frontend_workers


def test_frontend_job_budget_and_overrides_match_the_facade(monkeypatch):
    assert pipeline_frontend_workers.frontend_jobs(111, "auto", 64) == 10
    assert pipeline_frontend_workers.frontend_jobs(3, "20", 1) == 3
    assert pipeline_frontend_workers.frontend_jobs(111, "off", 64) == 1
    assert pipeline_frontend_workers.frontend_jobs(1, "8", 64) == 1
    assert pipeline_frontend_workers.numeric_jobs_override("5") is True
    assert pipeline_frontend_workers.numeric_jobs_override("auto") is False

    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "3")
    assert pipeline._python_frontend_jobs(9) == 3


def test_compiled_native_auto_width_is_memory_bounded(monkeypatch) -> None:
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    assert pipeline_frontend_workers.compiled_native_auto_jobs(10) == 2
    assert pipeline_frontend_workers.compiled_native_auto_jobs(1) == 1
    # A stated budget never widens the compiled risk cap, only shrinks it.
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(64 * 1024**3))
    assert pipeline_frontend_workers.compiled_native_auto_jobs(10) == 2
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(4 * 1024**3))
    assert pipeline_frontend_workers.compiled_native_auto_jobs(10) == 1


def test_compiled_light_widths_use_their_measured_memory_class(monkeypatch) -> None:
    workers = pipeline_frontend_workers
    gib = 1024**3
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    assert workers.compiled_native_export_jobs(10) == 2
    assert workers.compiled_native_summary_jobs(10) == 2

    # The production envelope remains at the already-proven width two.
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(8 * gib))
    assert workers.compiled_native_export_jobs(10) == 2
    assert workers.compiled_native_summary_jobs(10) == 2
    assert workers.compiled_native_auto_jobs(10) == 2

    # A larger shared envelope spends only bytes above the 7 GiB
    # coordinator/headroom reserve on 512 MiB export workers. Codegen keeps
    # its independent width-two risk contract.
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(10 * gib))
    assert workers.compiled_native_export_jobs(10) == 6
    assert workers.compiled_native_summary_jobs(10) == 6
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(12 * gib))
    assert workers.compiled_native_export_jobs(10) == 10
    assert workers.compiled_native_summary_jobs(10) == 10
    assert workers.compiled_native_export_jobs(5) == 5
    assert workers.compiled_native_summary_jobs(5) == 5
    assert workers.compiled_native_auto_jobs(10) == 2

    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(4 * gib))
    assert workers.compiled_native_export_jobs(10) == 1
    assert workers.compiled_native_summary_jobs(10) == 1


def test_budget_jobs_unifies_cpu_memory_and_risk_cap() -> None:
    workers = pipeline_frontend_workers
    host_peak = workers.HOST_SOURCE_WORKER_PEAK_BYTES
    gib = 1024**3
    # cpu-bound: plenty of memory, few cores.
    assert workers.budget_jobs(4, 64 * gib, host_peak, 10) == 4
    # memory-bound: (8 GiB - 1 GiB reserve) // 2 GiB host peak = 3.
    assert workers.budget_jobs(10, 8 * gib, host_peak, 10) == 3
    # hard risk cap binds last.
    assert (
        workers.budget_jobs(
            10, 64 * gib, workers.COMPILED_SAFE_WORKER_PEAK_BYTES, 2
        )
        == 2
    )
    # unknown budget (0) falls back to cpu within the cap.
    assert workers.budget_jobs(6, 0, host_peak, 10) == 6
    # floor is one worker even when the budget is absurdly small.
    assert workers.budget_jobs(10, 1, host_peak, 10) == 1


def test_host_auto_jobs_derive_from_the_memory_budget(monkeypatch) -> None:
    workers = pipeline_frontend_workers
    monkeypatch.delenv("PCC_WORKER_TREE_BUDGET_BYTES", raising=False)
    assert workers.frontend_jobs(111, "auto", 64) == 10
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(8 * 1024**3))
    assert workers.frontend_jobs(111, "auto", 64) == 3
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(32 * 1024**3))
    assert workers.frontend_jobs(111, "auto", 64) == 10
    # numeric override stays authoritative regardless of the budget.
    monkeypatch.setenv("PCC_WORKER_TREE_BUDGET_BYTES", str(4 * 1024**3))
    assert workers.frontend_jobs(111, "6", 64) == 6


def test_worker_modes_and_magic_are_pure_policy(tmp_path: Path):
    assert pipeline_frontend_workers.worker_timing_enabled(" YES ") is True
    assert pipeline_frontend_workers.ast_wire_enabled("on") is True
    assert pipeline_frontend_workers.worker_env_prefix(
        timing_enabled=False
    ) == "PCC_PY_FRONTEND_JOBS=1"

    script = tmp_path / "worker"
    script.write_bytes(b"#!/bin/sh\n")
    assert pipeline_frontend_workers.is_native_worker_executable(
        str(script)
    ) is False
    executable = tmp_path / "pcc1"
    executable.write_bytes(b"\xcf\xfa\xed\xfe")
    assert pipeline_frontend_workers.is_native_worker_executable(
        str(executable)
    ) is True
    assert pipeline_frontend_workers.select_native_worker_executable(
        ("python3", str(script), str(executable)),
        native_predicate=pipeline_frontend_workers.is_native_worker_executable,
    ) == str(executable)


def test_chunking_is_balanced_stable_and_native_workers_are_one_module_each(
    tmp_path: Path,
):
    sources = []
    for index, size in enumerate((100, 50, 20, 10)):
        source = tmp_path / ("m" + str(index) + ".py")
        source.write_text("x" * size, encoding="utf-8")
        sources.append(str(source))
    chunks = pipeline_frontend_workers.codegen_chunks(sources, 2)
    assert sorted(index for chunk in chunks for index in chunk) == [0, 1, 2, 3]
    assert chunks == pipeline_frontend_workers.codegen_chunks(sources, 2)

    native_worker = tmp_path / "pcc1"
    native_worker.write_bytes(b"\xcf\xfa\xed\xfe")
    assert pipeline_frontend_workers.codegen_chunk_count(
        4,
        2,
        [str(native_worker)],
        native_predicate=pipeline_frontend_workers.is_native_worker_executable,
    ) == 4
    assert pipeline_frontend_workers.codegen_chunk_count(
        4,
        2,
        ["python3"],
        native_predicate=lambda _path: False,
    ) == 4

    assert pipeline_frontend_workers.codegen_chunk_count(
        111,
        2,
        ["python3"],
        native_predicate=lambda _path: False,
    ) == 8


def test_codegen_lanes_extract_oversized_sources_largest_first(tmp_path: Path):
    sizes = (20, 220_000, 210_000, 30)
    sources = []
    for index, size in enumerate(sizes):
        source = tmp_path / ("lane" + str(index) + ".py")
        source.write_text("x" * size, encoding="utf-8")
        sources.append(str(source))

    oversized, safe = (
        pipeline_frontend_workers.split_codegen_chunks_by_source_size(
            sources,
            [[0, 1], [2, 3]],
        )
    )

    assert oversized == [[1], [2]]
    assert safe == [[0], [3]]


def test_codegen_lanes_include_large_ast_sidecars_in_memory_weight(
    tmp_path: Path,
) -> None:
    sources = []
    ast_dir = tmp_path / "ast"
    ast_dir.mkdir()
    for index in range(3):
        source = tmp_path / ("sidecar" + str(index) + ".py")
        source.write_text("x = 1\n", encoding="utf-8")
        sources.append(str(source))
        (ast_dir / ("module_" + str(index) + ".json")).write_bytes(
            b"x" * (6_100_000 if index == 1 else 20)
        )

    oversized, safe = (
        pipeline_frontend_workers.split_codegen_chunks_by_source_size(
            sources,
            [[0, 1], [2]],
            sidecar_dir=str(ast_dir),
            sidecar_threshold_bytes=6_000_000,
        )
    )

    assert oversized == [[1]]
    assert safe == [[0], [2]]


def test_codegen_lane_exports_are_available_to_native_pcc1() -> None:
    from pcc.py_frontend.codegen.layer1_support import (
        _default_native_module_exports,
    )

    exports = _default_native_module_exports(
        "pcc.py_frontend.pipeline_frontend_workers"
    )
    assert exports is not None
    worker_exports = exports["pcc.py_frontend.pipeline_frontend_workers"]
    assert "split_codegen_chunks_by_source_size" in worker_exports
    assert "compiled_native_auto_jobs" in worker_exports
    assert "compiled_native_export_jobs" in worker_exports
    assert "compiled_native_summary_jobs" in worker_exports
    assert worker_exports["SOURCE_WORKER_AUTO_SAFE_JOBS"]["value"] == 2
    assert worker_exports["SOURCE_WORKER_AST_OVERSIZED_BYTES"]["value"] == 6_000_000



def test_parallel_frontend_imports_lane_policy_as_static_symbols() -> None:
    """Compiled pcc1 must not require attributes on a partial module object."""
    from pcc.py_frontend import pipeline_frontend_parallel

    assert (
        pipeline_frontend_parallel._split_codegen_chunks_by_source_size
        is pipeline_frontend_workers.split_codegen_chunks_by_source_size
    )
    assert (
        pipeline_frontend_parallel._SOURCE_WORKER_AUTO_SAFE_JOBS
        == pipeline_frontend_workers.SOURCE_WORKER_AUTO_SAFE_JOBS
    )
    assert (
        pipeline_frontend_parallel._compiled_native_export_jobs
        is pipeline_frontend_workers.compiled_native_export_jobs
    )
    assert (
        pipeline_frontend_parallel._compiled_native_summary_jobs
        is pipeline_frontend_workers.compiled_native_summary_jobs
    )


def test_worker_manifest_v4_round_trips_and_rejects_truncation(tmp_path: Path):
    manifest_path = tmp_path / "worker.manifest"
    pipeline_frontend_workers.write_worker_manifest(
        str(manifest_path),
        str(tmp_path / "result"),
        str(tmp_path / "ir"),
        str(tmp_path / "exports.json"),
        str(tmp_path / "ast"),
        ["/src/a.py", "/src/b.py"],
        ["pkg.a", "pkg.b"],
        [1],
        entry_module="pkg.a",
        sibling_inits=("pkg.b",),
        libpython_mode="off",
        ir_scaffold_mode="on",
        verbose=True,
        job_kind="export",
    )
    manifest = pipeline_frontend_workers.read_worker_manifest(str(manifest_path))
    assert manifest["job_kind"] == "export"
    assert manifest["src_paths"] == ["/src/a.py", "/src/b.py"]
    assert manifest["module_names"] == ["pkg.a", "pkg.b"]
    assert manifest["assigned_indices"] == [1]
    assert manifest["sibling_inits"] == ("pkg.b",)

    manifest_path.write_text(
        pipeline_frontend_workers.WORKER_MANIFEST_V4 + "\nonly-one-field\n",
        encoding="utf-8",
    )
    with pytest.raises(
        pipeline_frontend_workers.FrontendWorkerContractError,
        match="truncated or malformed",
    ):
        pipeline_frontend_workers.read_worker_manifest(str(manifest_path))


def test_worker_ir_error_and_shell_text_contracts(tmp_path: Path):
    ir_path = tmp_path / "module.ll"
    ir_path.write_text("define i32 @f() { ret i32 0 }\n", encoding="utf-8")
    assert "define i32" in pipeline_frontend_workers.read_worker_ir(
        str(ir_path), "pkg.module"
    )
    ir_path.write_text("", encoding="utf-8")
    with pytest.raises(
        pipeline_frontend_workers.FrontendWorkerContractError,
        match="empty LLVM IR",
    ):
        pipeline_frontend_workers.read_worker_ir(str(ir_path), "pkg.module")

    error_path = tmp_path / "result"
    pipeline_frontend_workers.write_worker_error(
        str(error_path), "first\tsecond\nthird"
    )
    assert error_path.read_text(encoding="utf-8") == "ERR\tfirst second third\n"
    assert pipeline_frontend_workers.shell_quote_arg("plain/path") == "plain/path"
    assert pipeline_frontend_workers.shell_quote_arg("a'b") == "'a'\"'\"'b'"
