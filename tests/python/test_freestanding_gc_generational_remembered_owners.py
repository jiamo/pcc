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
STRICT_SOURCE = (
    RUNTIME_DIR / "py" / "freestanding_gc_generational_remembered_owners.py"
)
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend3_clear_remembered_owners",
    "pcc_gc_backend3_drain_remembered_owners",
    "pcc_gc_backend3_remember_owner",
    "pcc_gc_backend3_remembered_owner_list_head",
    "pcc_gc_backend3_remembered_owner_list_set_head",
    "pcc_gc_backend3_scan_remembered_owners",
}
RAW_FUNCTION_IMPORTS = {
    "free",
    "malloc",
    "pcc_gc_object_is_known_no_lock",
    "pcc_gc_object_list_head",
    "pcc_gc_object_node_is_active",
    "pcc_gc_object_node_next",
    "pcc_gc_trace_referents_for_promotion",
    "pcc_thread_safepoint",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend3_remembered_overflow",
    "pcc_gc_backend3_remembered_owner_head",
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


def test_generational_remembered_owners_have_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_generational_remembered_owners" in makefile
    assert "def _backend3_remember_owner(" not in managed
    assert "def _backend3_clear_remembered_owners(" not in managed
    assert "def _backend3_scan_remembered_owners(" not in managed
    assert "def _backend3_drain_remembered_owners(" not in managed
    assert '_backend3_remember_owner = extern(' in managed
    assert '_backend3_drain_remembered_owners = extern(' in managed
    assert '_trace_referents_for_promotion = extern(' in managed
    assert '@c_abi_export("pcc_gc_trace_referents_for_promotion")' not in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_generational_remembered_owners_have_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("generational_remembered_owners_" + emitter + ".ll")
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

        source = tmp_path / "generational_remembered_owners.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("generational_remembered_owners_" + emitter + ".o")
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


def test_generational_remembered_owners_preserve_overflow_and_budget_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    remember = _export_body(strict, "pcc_gc_backend3_remember_owner")
    clear = _export_body(strict, "pcc_gc_backend3_clear_remembered_owners")
    scan = _export_body(strict, "pcc_gc_backend3_scan_remembered_owners")
    drain = _export_body(strict, "pcc_gc_backend3_drain_remembered_owners")

    allocation_failure = remember.split("if ptr_is_null(node) != 0:", 1)[1].split(
        "return", 1
    )[0]
    assert allocation_failure.index("pcc_gc_backend3_remembered_overflow") < (
        allocation_failure.index("owner_flags | 512")
    )
    assert clear.index("pcc_gc_backend3_remembered_owner_list_set_head(null())") < (
        clear.index("while ptr_is_null(node) == 0:")
    )
    assert clear.index("pcc_gc_backend3_remembered_overflow") < clear.index(
        "while ptr_is_null(node) == 0:"
    )
    assert "pcc_gc_object_node_is_active(node)" in scan
    assert "local_processed < remaining_budget" in scan
    assert scan.index("pcc_gc_trace_referents_for_promotion(owner)") < scan.index(
        "flags & ~512"
    )
    overflow = drain.split(
        'global_addr("pcc_gc_backend3_remembered_overflow")', 1
    )[1].split("while", 1)[0]
    assert "pcc_gc_backend3_clear_remembered_owners()" in overflow
    assert "pcc_gc_backend3_scan_remembered_owners(remaining_budget)" in overflow
    assert "local_processed < remaining_budget" in drain
    assert drain.index("free(node)") < drain.index(
        "pcc_gc_object_is_known_no_lock(owner)"
    )


def test_production_archive_has_one_generational_remembered_owner_provider(
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
        assert ":freestanding_gc_generational_remembered_owners.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]
