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
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_forwarding_identity.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend4_forwarding_entries",
    "pcc_gc_backend4_slot_needs_resolve",
    "pcc_gc_backend4_stable_id_entries",
    "pcc_gc_forwarding_find",
    "pcc_gc_forwarding_clear_all",
    "pcc_gc_forwarding_identity_graph_lock",
    "pcc_gc_forwarding_identity_graph_unlock",
    "pcc_gc_forwarding_list_head",
    "pcc_gc_forwarding_set_head",
    "pcc_gc_forwarding_target_exists",
    "pcc_gc_forwarding_target_attach_prepared",
    "pcc_gc_forwarding_target_find",
    "pcc_gc_forwarding_target_prepare",
    "pcc_gc_forwarding_target_unlink",
    "pcc_gc_forwarding_unlink_main",
    "pcc_gc_forwarding_zpage_node_for_owner",
    "pcc_gc_identity_assign",
    "pcc_gc_identity_clear_all",
    "pcc_gc_identity_ensure",
    "pcc_gc_identity_find",
    "pcc_gc_identity_list_head",
    "pcc_gc_identity_remove",
    "pcc_gc_identity_set_head",
    "pcc_gc_install_forwarding",
    "pcc_gc_install_forwarding_unlocked",
    "pcc_gc_note_relocation_read",
    "pcc_gc_note_relocation_read_unlocked",
    "pcc_gc_object_id",
}
RAW_FUNCTION_IMPORTS = {
    "free",
    "malloc",
    "pcc_gc_config_ensure",
    "pcc_gc_forwarding_index_clear",
    "pcc_gc_forwarding_index_find",
    "pcc_gc_forwarding_index_insert",
    "pcc_gc_forwarding_index_remove",
    "pcc_gc_forwarding_target_index_clear",
    "pcc_gc_forwarding_target_index_find",
    "pcc_gc_forwarding_target_index_insert",
    "pcc_gc_forwarding_target_index_remove",
    "pcc_gc_forwarding_target_index_upsert",
    "pcc_gc_identity_index_clear",
    "pcc_gc_identity_index_find",
    "pcc_gc_identity_index_insert",
    "pcc_gc_identity_index_remove",
    "pcc_gc_object_index_find",
    "pcc_gc_object_is_known_no_lock",
    "pcc_gc_zpage_owner_index_find",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    "py_decref",
    "py_incref",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend_selected",
    "pcc_gc_forwarding_head",
    "pcc_gc_forwarding_population",
    "pcc_gc_identity_head",
    "pcc_gc_next_object_id",
    "pcc_gc_relocation_barrier_forwards",
    "pcc_gc_relocation_forwards",
    "pcc_gc_relocation_pin_rejects",
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


def _export_body(source: str, symbol: str) -> str:
    return source.split(f'@c_abi_export("{symbol}")', 1)[1].split(
        "\n@c_abi_export", 1
    )[0]


def test_forwarding_identity_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_forwarding_identity" in makefile
    for old_name in (
        "_forwarding_head",
        "_forwarding_find",
        "_forwarding_target_exists",
        "_forwarding_target_unlink",
        "_forwarding_unlink_main",
        "_forwarding_clear_all",
        "_identity_head",
        "_identity_ensure",
        "_identity_remove",
        "_identity_clear_all",
        "_install_forwarding_unlocked",
        "_note_relocation_read_unlocked",
    ):
        assert f"def {old_name}(" not in managed
    assert '_forwarding_find = extern("pcc_gc_forwarding_find"' in managed
    assert '_install_forwarding_unlocked = extern(' in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_forwarding_identity_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("forwarding_identity_" + emitter + ".ll")
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

        source = tmp_path / "forwarding_identity.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("forwarding_identity_" + emitter + ".o")
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


def test_forwarding_identity_preserves_locking_and_safe_lookup_order() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")

    slot = _export_body(strict, "pcc_gc_backend4_slot_needs_resolve")
    assert slot.index("pcc_gc_forwarding_find(value)") < slot.index(
        "pcc_gc_object_is_known_no_lock(value)"
    )
    assert slot.index("pcc_gc_object_is_known_no_lock(value)") < slot.index(
        "load_i32(value, 12)"
    )

    install = _export_body(strict, "pcc_gc_install_forwarding")
    assert install.index("_graph_lock()") < install.index(
        "pcc_gc_install_forwarding_unlocked(from_obj, to_obj)"
    )
    assert install.index("pcc_gc_install_forwarding_unlocked(from_obj, to_obj)") < (
        install.index("_graph_unlock()")
    )

    read = _export_body(strict, "pcc_gc_note_relocation_read")
    assert read.index("pcc_gc_object_is_known_no_lock(obj)") < read.index(
        "load_i32(obj, 12)"
    )
    assert read.index("_graph_lock()") < read.index(
        "_note_relocation_read_unlocked(obj)"
    )
    assert read.index("_note_relocation_read_unlocked(obj)") < read.index(
        "_graph_unlock()"
    )


def test_production_archive_has_one_forwarding_identity_owner(
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
        assert ":freestanding_gc_forwarding_identity.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]
