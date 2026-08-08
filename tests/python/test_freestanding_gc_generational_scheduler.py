from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from pcc.py_frontend import pipeline


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_generational_scheduler.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
BARRIER_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_barrier_dispatcher.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_generational_promote_frame_roots",
    "pcc_gc_generational_promote_tls_exception_root",
    "pcc_gc_generational_step",
}
RAW_FUNCTION_IMPORTS = {
    "pcc_gc_backend3_drain_remembered_owners",
    "pcc_gc_backend3_young_link_head",
    "pcc_gc_backend3_young_list_head",
    "pcc_gc_backend3_young_unlink",
    "pcc_gc_forwarding_find",
    "pcc_gc_generational_oldify_copy",
    "pcc_gc_generational_promote_young_if_known",
    "pcc_gc_object_node_is_active",
    "pcc_gc_trace_referents_for_promotion",
    "pcc_gc_visit_registered_root_slots",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    "pcc_thread_safepoint",
    "py_decref",
    "py_incref",
    "py_tls_exc_get",
    "py_tls_exc_set",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _export_body(source: str, symbol: str) -> str:
    return source.split(f'@c_abi_export("{symbol}")', 1)[1].split(
        "\n@c_abi_export", 1
    )[0]


def test_generational_scheduler_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    barrier = BARRIER_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_generational_scheduler" in makefile
    assert "def _promote_frame_roots(" not in managed
    assert "def _promote_tls_exception_root(" not in managed
    assert "def _step_generational_promotion(" not in managed
    assert '_step_generational_promotion = extern(' in managed
    assert "_step_generational_promotion(1024, 0)" in managed
    assert "pcc_gc_generational_step(budget, 1)" in barrier


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_generational_scheduler_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("generational_scheduler_" + emitter + ".ll")
    pipeline.compile_python(
        str(STRICT_SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        libpython_mode="off",
        python_library=True,
    )
    source = llvm_ir
    if emitter == "self":
        from pcc.backend.self_backend_dispatch import emit_self_asm

        source = tmp_path / "generational_scheduler.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("generational_scheduler_" + emitter + ".o")
    result = subprocess.run(
        ["clang", "-c", str(source), "-o", str(obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    undefined_result = subprocess.run(
        ["nm", "-u", str(obj)], capture_output=True, text=True, timeout=30
    )
    assert undefined_result.returncode == 0, (
        undefined_result.stdout + undefined_result.stderr
    )
    undefined = {
        line.split()[-1].lstrip("_")
        for line in undefined_result.stdout.splitlines()
        if line.strip()
    }
    assert undefined == RAW_FUNCTION_IMPORTS

    symbols_result = subprocess.run(
        ["nm", "-g", str(obj)], capture_output=True, text=True, timeout=30
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    defined = {
        line.split()[-1].lstrip("_")
        for line in symbols_result.stdout.splitlines()
        if line.strip() and " U " not in line
    }
    assert defined == OWNED_SYMBOLS


def test_generational_scheduler_preserves_root_order_budget_and_retry_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    tls = _export_body(strict, "pcc_gc_generational_promote_tls_exception_root")
    step = _export_body(strict, "pcc_gc_generational_step")

    assert tls.index("py_incref(oldified)") < tls.index("py_tls_exc_set(oldified)")
    assert tls.index("py_tls_exc_set(oldified)") < tls.index(
        "py_decref(current)"
    )
    assert "pcc_gc_generational_promote_young_if_known(current)" in tls

    assert step.index("pcc_py_gc_minor_graph_lock()") < step.index(
        "pcc_gc_generational_promote_frame_roots(remaining_budget)"
    )
    assert step.index("pcc_gc_generational_promote_frame_roots") < step.index(
        "pcc_gc_generational_promote_tls_exception_root()"
    )
    assert step.index("pcc_gc_generational_promote_tls_exception_root") < step.index(
        "pcc_gc_backend3_drain_remembered_owners"
    )
    assert "local_processed < remaining_budget" in step
    assert "pcc_gc_backend3_young_unlink(node)" in step
    assert "pcc_gc_backend3_young_link_head(node)" in step
    assert "break" in step
    assert step.rindex("pcc_py_gc_minor_graph_unlock()") < step.rindex(
        "return local_processed"
    )


def test_production_archive_has_one_generational_scheduler_owner(
    pcc_py_runtime_archive: Path,
) -> None:
    symbols_result = subprocess.run(
        ["nm", "-A", "-g", str(pcc_py_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    for symbol in OWNED_SYMBOLS:
        owners = [
            line
            for line in symbols_result.stdout.splitlines()
            if line.strip()
            and line.split()[-1].lstrip("_") == symbol
            and " U " not in line
        ]
        assert len(owners) == 1, (symbol, owners)
        assert ":freestanding_gc_generational_scheduler.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]
