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
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_relocation_payload.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_relocate_copy_payload",
    "pcc_gc_relocation_payload_copy_slots",
    "pcc_gc_relocation_payload_count_slot",
    "pcc_gc_relocation_payload_fail",
    "pcc_gc_relocation_payload_finish",
    "pcc_gc_relocation_payload_from_slot",
    "pcc_gc_relocation_payload_retarget_continuation_root_slots",
    "pcc_gc_relocation_payload_slot_pairs_dispose",
    "pcc_gc_relocation_payload_slot_pairs_prepare",
    "pcc_gc_relocation_payload_to_slot",
}
RAW_FUNCTION_IMPORTS = {
    "free",
    "malloc",
    "memmove",
    "memset",
    "pcc_capi_is_cext_type_tag",
    "pcc_gc_backend4_remap_heal_slot",
    "pcc_gc_backend4_remembered_set_retarget_slot",
    "pcc_gc_backend4_zpage_register_owner_payload_span",
    "pcc_gc_load_ptr",
    "pcc_gc_visit_object_slots",
    "py_incref",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_continuation_root_head",
    "pcc_gc_relocate_slot_pairs_ctx",
    "py_weakref_head",
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


def test_relocation_payload_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_relocation_payload" in makefile
    assert "def _relocate_copy_payload(" not in managed
    assert '_relocate_copy_payload = extern(' in managed
    assert "def _py_obj_visit_relocate_count_slot(" not in managed
    assert "def _py_obj_visit_relocate_from_slot(" not in managed
    assert "def _py_obj_visit_relocate_to_slot(" not in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_relocation_payload_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("relocation_payload_" + emitter + ".ll")
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

        source = tmp_path / "relocation_payload.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("relocation_payload_" + emitter + ".o")
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


def test_relocation_payload_copies_raw_storage_but_uses_shared_slot_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")

    for tag in (5, 6, 7, 8, 10, 12, 21, 27, 28, 29, 30):
        assert f"tag == {tag}" in strict
    assert "tag == 11 or tag >= 104" in strict
    assert "pcc_gc_visit_object_slots(" in strict
    assert "pcc_gc_backend4_remap_heal_slot(from_slot, 0)" in strict
    assert "pcc_gc_backend4_remembered_set_retarget_slot(" in strict
    assert "pcc_gc_backend4_zpage_register_owner_payload_span(" in strict
    assert "_retarget_continuation_root_slots(" in strict
    assert "_py_obj_visit_covered_slots" not in strict

    payload = strict.split("def pcc_gc_relocate_copy_payload", 1)[1]
    assert "py_incref(" not in payload
    assert "pcc_gc_backend4_remembered_set_retarget_slot(" not in payload


def test_production_archive_has_one_relocation_payload_owner(
    pcc_py_runtime_archive: Path,
) -> None:
    symbols_result = subprocess.run(
        ["nm", "-A", "-g", str(pcc_py_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    owners = [
        line
        for line in symbols_result.stdout.splitlines()
        if line.strip()
        and line.split()[-1].lstrip("_") == "pcc_gc_relocate_copy_payload"
        and " U " not in line
    ]
    assert len(owners) == 1, owners
    assert ":freestanding_gc_relocation_payload.o:" in owners[0]
    assert ":py_gc_backend.o:" not in owners[0]
