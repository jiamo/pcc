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
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_relocation_copy.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend4_relocate_copy_unlocked",
    "pcc_gc_relocate_copy",
}
RAW_FUNCTION_IMPORTS = {
    "memmove",
    "pcc_gc_alloc",
    "pcc_gc_backend",
    "pcc_gc_backend4_evacuation_page_remove",
    "pcc_gc_backend4_relocate_copy_supported_tag",
    "pcc_gc_backend4_relocation_set_contains_page",
    "pcc_gc_backend4_relocation_set_find",
    "pcc_gc_backend4_relocation_set_remove",
    "pcc_gc_backend4_zpage_page_for_owner",
    "pcc_gc_backend4_zpage_remove",
    "pcc_gc_config_ensure",
    "pcc_gc_forwarding_find",
    "pcc_gc_install_forwarding_unlocked",
    "pcc_gc_memoryview_refresh_owned_buffer",
    "pcc_gc_object_known_size",
    "pcc_gc_relocate_copy_payload",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    "py_decref",
}
RAW_GLOBAL_IMPORTS = {"pcc_gc_backend4_evacuated_bytes_count"}


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


def test_relocation_copy_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_relocation_copy" in makefile
    assert "def _relocate_copy_unlocked(" not in managed
    assert 'pcc_gc_relocate_copy = extern(' in managed
    assert '_backend4_relocate_copy_unlocked = extern(' in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_relocation_copy_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("relocation_copy_" + emitter + ".ll")
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

        source = tmp_path / "relocation_copy.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("relocation_copy_" + emitter + ".o")
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


def test_relocation_copy_preserves_transaction_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    body = strict.split("def pcc_gc_backend4_relocate_copy_unlocked", 1)[1].split(
        '@c_abi_export("pcc_gc_relocate_copy")', 1
    )[0]

    assert "pcc_gc_backend() != 4" in body
    assert "pcc_gc_forwarding_find(from_obj)" in body
    assert "pcc_gc_backend4_relocation_set_find(from_obj)" in body
    assert "pcc_gc_backend4_relocate_copy_supported_tag(tag)" in body
    assert "known_size <= 0 or size > known_size" in body
    assert "to_residency: int = load_i32(to_obj, 12) & 331776" in body
    assert "memmove(to_obj, from_obj, size)" in body
    assert "pcc_gc_relocate_copy_payload(from_obj, to_obj, tag, size)" in body
    assert "pcc_gc_install_forwarding_unlocked(from_obj, to_obj)" in body
    assert "store_i64(to_obj, 0, load_i64(to_obj, 0) + outstanding)" in body
    assert "store_i32(from_obj, 12, load_i32(from_obj, 12) | 1)" in body
    assert "pcc_gc_backend4_relocation_set_remove(from_obj)" in body
    assert "pcc_gc_backend4_zpage_remove(from_obj)" in body


def test_production_archive_has_one_relocation_copy_owner(
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
        assert ":freestanding_gc_relocation_copy.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]
