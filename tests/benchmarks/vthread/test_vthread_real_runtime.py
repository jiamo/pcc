"""Focused production-runtime coverage for the manual one-million gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).absolute().parents[3]
RUNNER = REPO_ROOT / "scripts" / "run_vthread_1m_gate.py"
SPEC = importlib.util.spec_from_file_location("run_vthread_1m_gate", RUNNER)
assert SPEC is not None and SPEC.loader is not None
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def test_one_million_requires_explicit_manual_gate() -> None:
    assert R.manual_gate_enabled({}) is False
    assert R.manual_gate_enabled({R.MANUAL_ENV: "0"}) is False
    assert R.manual_gate_enabled({R.MANUAL_ENV: "1"}) is True


def test_gc3_malloc_ownership_is_explicit_after_minor_block_address_scan() -> None:
    c_obj = (REPO_ROOT / "pcc" / "py_runtime" / "src" / "py_obj.c").read_text(
        encoding="utf-8"
    )
    c_gc = (
        REPO_ROOT / "pcc" / "py_runtime" / "src" / "py_gc_backend.c"
    ).read_text(encoding="utf-8")
    py_gc = (
        REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_gc_backend.py"
    ).read_text(encoding="utf-8")

    alloc = c_obj.split("PyObject *pcc_gc_alloc", 1)[1].split(
        "PyObject *pcc_gc_retain", 1
    )[0]
    assert "PY_FLAG_GC_MALLOC_ALLOC" in alloc

    oldify = c_gc.split("pcc_gc_generational_oldify_copy", 1)[1].split(
        "static void pcc_gc_promote_owner_referents", 1
    )[0]
    assert ") | PY_FLAG_GC_OLD | PY_FLAG_GC_MALLOC_ALLOC;" in oldify
    free_path = c_gc.split("void pcc_gc_free_object_memory", 1)[1].split(
        "void pcc_gc_note_load", 1
    )[0]
    assert free_path.index("pcc_gc_minor_block_containing_unlocked") < free_path.index(
        "Only an explicit allocation-origin bit authorizes system free()."
    )
    assert "if ((flags & PY_FLAG_GC_MALLOC_ALLOC) == 0)" in free_path

    py_oldify = py_gc.split("def _generational_oldify_copy", 1)[1].split(
        "def _promote_young", 1
    )[0]
    assert "(new_flags & ~(128 | 4096 | 512 | 2048 | 262144)) | 256 | 262144" in py_oldify


def test_small_production_runtime_matrix_is_real_and_balanced() -> None:
    manifest = R.run_gate(
        n=2_000,
        backends=(0, 1, 2, 3, 4),
        timer_n=200,
        io_n=20,
        build_timeout=240,
        backend_timeout=60,
    )
    assert manifest["mode"] == "real-runtime"
    assert manifest["status"] == "MEASURED"
    assert len(manifest["source_sha256"]) == 64
    assert len(manifest["runtime_archive_sha256"]) == 64
    assert manifest["backends"] == [0, 1, 2, 3, 4]
    assert len(manifest["results"]) == 5
    for backend, result in enumerate(manifest["results"]):
        assert result["backend"] == backend
        assert result["n"] == 2_000
        assert result["completed"] == 2_000
        assert result["scheduler_roots_final"] == 0
        assert result["ready_final"] == 0
        assert result["timer_final"] == 0
        assert result["io_final"] == 0
        assert result["peak_rss_bytes"] > 0
        assert result["throughput_vthreads_per_sec"] > 0
        assert result["resume_mean_ns"] > 0
        assert result["gc_pause_count"] >= 2
