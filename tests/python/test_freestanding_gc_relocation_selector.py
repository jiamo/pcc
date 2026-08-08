from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from pcc.py_frontend import pipeline
from pcc.py_frontend.codegen.runtime_abi import FREESTANDING_GC_RUNTIME_GLOBALS


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_relocation_selector.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend4_select_relocation_pages",
    "pcc_gc_relocation_selector_add_candidate_node",
    "pcc_gc_relocation_selector_candidate_score",
    "pcc_gc_relocation_selector_evacuation_policy_accept",
    "pcc_gc_relocation_selector_evacuation_policy_defer_large",
    "pcc_gc_relocation_selector_large_page_policy_accept",
    "pcc_gc_relocation_selector_note_page_candidate",
    "pcc_gc_relocation_selector_select_page_objects",
    "pcc_gc_relocation_selector_zpage_head",
    "pcc_gc_select_relocation_set",
}
RAW_FUNCTION_IMPORTS = {
    "pcc_gc_backend",
    "pcc_gc_backend4_evacuation_page_add",
    "pcc_gc_backend4_evacuation_page_find",
    "pcc_gc_backend4_owner_remembered_slots",
    "pcc_gc_backend4_relocate_copy_supported_tag",
    "pcc_gc_backend4_relocation_set_add",
    "pcc_gc_backend4_relocation_set_find",
    "pcc_gc_config_ensure",
    "pcc_gc_object_known_size",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    "pcc_thread_safepoint",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend4_evacuation_candidate_bytes_count",
    "pcc_gc_backend4_evacuation_candidate_zpage_bytes_count",
    "pcc_gc_backend4_evacuation_candidates",
    "pcc_gc_backend4_large_object_deferred_bytes_count",
    "pcc_gc_backend4_large_object_defers",
    "pcc_gc_backend4_medium_page_candidate_bytes_count",
    "pcc_gc_backend4_medium_page_candidate_zpage_bytes_count",
    "pcc_gc_backend4_medium_page_candidates",
    "pcc_gc_backend4_small_page_candidate_bytes_count",
    "pcc_gc_backend4_small_page_candidate_zpage_bytes_count",
    "pcc_gc_backend4_small_page_candidates",
    "pcc_gc_backend4_zpage_head",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _literal_global_imports() -> set[str]:
    globals_: set[str] = set()
    tree = ast.parse(STRICT_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"global_addr", "global_load_ptr", "global_store_ptr"}:
            continue
        if not node.args:
            continue
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            globals_.add(value.value)
    return globals_


def test_relocation_selector_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_relocation_selector" in makefile
    assert "def _backend4_zpage_candidate_score(" not in managed
    assert "def _backend4_select_page_objects(" not in managed
    assert "def pcc_gc_select_relocation_set(" not in managed
    assert 'pcc_gc_select_relocation_set = extern(' in managed
    assert '_backend4_select_relocation_pages = extern(' in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_relocation_selector_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("relocation_selector_" + emitter + ".ll")
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

        source = tmp_path / "relocation_selector.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("relocation_selector_" + emitter + ".o")
    result = subprocess.run(
        ["clang", "-c", str(source), "-o", str(obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _literal_global_imports() == RAW_GLOBAL_IMPORTS
    assert RAW_GLOBAL_IMPORTS <= FREESTANDING_GC_RUNTIME_GLOBALS

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
    assert undefined == RAW_FUNCTION_IMPORTS | RAW_GLOBAL_IMPORTS

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


def test_relocation_selector_preserves_page_policy_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")

    assert "if size <= 4096:" in strict
    assert "if size <= 65536:" in strict
    assert "if size > 65536:" in strict
    assert "load_i32(page, 24) != 2" in strict
    assert "capacity - page_used" in strict
    assert "load_i64(page, 40)" in strict
    assert "load_i64(page, 48)" in strict
    assert "pcc_gc_backend4_owner_remembered_slots(obj)" in strict
    assert "flags | 32768" in strict
    assert "while pass_no < 2 and selected < budget:" in strict
    assert "pcc_thread_safepoint()" in strict
    assert "object_budget: int = load_i64(best_page, 32)" in strict


def test_production_archive_has_one_relocation_selector_owner(
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
        assert ":freestanding_gc_relocation_selector.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]
