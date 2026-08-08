from __future__ import annotations

import ast
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from pcc.py_frontend import pipeline
from pcc.py_frontend.codegen.runtime_abi import FREESTANDING_GC_RUNTIME_GLOBALS


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_zpage_allocation.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend4_try_zpage_alloc",
    "pcc_gc_backend4_zpage_track_alloc",
}
RAW_FUNCTION_IMPORTS = {
    "malloc",
    "memset",
    "pcc_gc_backend4_evacuation_page_find",
    "pcc_gc_backend4_zpage_active_page",
    "pcc_gc_backend4_zpage_clear_active_page",
    "pcc_gc_backend4_zpage_find_page_for_addr",
    "pcc_gc_backend4_zpage_find_reusable_page",
    "pcc_gc_backend4_zpage_find_reusable_page_for_gen",
    "pcc_gc_backend4_zpage_link_node",
    "pcc_gc_backend4_zpage_node_alloc",
    "pcc_gc_backend4_zpage_node_release",
    "pcc_gc_backend4_zpage_pop_free_page",
    "pcc_gc_backend4_zpage_reset",
    "pcc_gc_backend4_zpage_set_active_page",
    "pcc_gc_config_ensure",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend4_evacuation_page_head",
    "pcc_gc_backend4_page_head",
    "pcc_gc_backend_selected",
    "pcc_gc_config_initialized",
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


def test_zpage_allocation_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_zpage_allocation" in makefile
    assert "def pcc_gc_backend4_try_zpage_alloc(" not in managed
    assert "def _backend4_zpage_track_alloc(" not in managed
    assert 'pcc_gc_backend4_try_zpage_alloc = extern(' in managed
    assert '_backend4_zpage_track_alloc = extern(' in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_zpage_allocation_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("zpage_allocation_" + emitter + ".ll")
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

        source = tmp_path / "zpage_allocation.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("zpage_allocation_" + emitter + ".o")
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


def test_zpage_allocation_preserves_page_and_pending_handoff_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    allocation = strict.split("def pcc_gc_backend4_try_zpage_alloc", 1)[1].split(
        '@c_abi_export("pcc_gc_backend4_zpage_track_alloc")', 1
    )[0]
    tracking = strict.split("def pcc_gc_backend4_zpage_track_alloc", 1)[1]

    assert "size < 16" in allocation
    assert "(size + 7) & -8" in allocation
    assert "capacity - allocated >= alloc_size" in allocation
    assert "pcc_gc_backend4_evacuation_page_find(active)" in allocation
    assert "store_i64(page, 88, load_i64(page, 88) + 1)" in allocation
    assert "pcc_gc_backend4_zpage_find_page_for_addr(owner, size)" in tracking
    assert "pending - 1" in tracking
    assert "store_i64(node, 24, existing_offset)" in tracking
    assert "pcc_gc_backend4_zpage_link_node(node)" in tracking


def test_production_archive_has_one_zpage_allocation_owner(
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
        assert ":freestanding_gc_zpage_allocation.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]


def _link_zpage_allocation_probe(
    tmp_path: Path, name: str, archive: Path, size: int
) -> Path:
    source = tmp_path / (name + ".c")
    executable = tmp_path / name
    source.write_text(
        textwrap.dedent(
            r'''
            #include "py_runtime.h"
            #include <stdio.h>

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING) != 0) {
                    return 2;
                }
                long long count0 = pcc_gc_backend4_zpage_count();
                long long capacity0 = pcc_gc_backend4_zpage_capacity_bytes();
                long long used0 = pcc_gc_backend4_zpage_used_bytes();
                PyObject *a = pcc_gc_alloc(SIZE_VALUE, PY_TYPE_LIST, 0);
                PyObject *b = pcc_gc_alloc(SIZE_VALUE, PY_TYPE_LIST, 0);
                if (a == 0 || b == 0) return 3;
                printf("%lld,%lld,%lld,%lld,%lld\n",
                       (long long)pcc_gc_backend4_zpage_count() - count0,
                       (long long)pcc_gc_backend4_zpage_capacity_bytes() - capacity0,
                       (long long)pcc_gc_backend4_zpage_used_bytes() - used0,
                       (long long)pcc_gc_backend4_zpage_owner_offset_bytes(a),
                       (long long)pcc_gc_backend4_zpage_owner_offset_bytes(b));
                pcc_gc_release(b);
                pcc_gc_release(a);
                printf("%lld,%lld,%lld\n",
                       (long long)pcc_gc_backend4_zpage_count() - count0,
                       (long long)pcc_gc_backend4_zpage_capacity_bytes() - capacity0,
                       (long long)pcc_gc_backend4_zpage_used_bytes() - used0);
                return 0;
            }
            '''
        ).replace("SIZE_VALUE", str(size)).lstrip(),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "clang",
            "-std=c11",
            f"-I{RUNTIME_DIR / 'include'}",
            f"-I{RUNTIME_DIR / 'src'}",
            str(source),
            str(archive),
            "-pthread",
            "-lm",
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (128, "1,4096,256,0,128\n0,0,0\n"),
        (8192, "1,65536,16384,0,8192\n0,0,0\n"),
        (70000, "2,262144,140000,0,0\n0,0,0\n"),
    ],
)
def test_zpage_allocation_matches_c_oracle_across_page_classes(
    tmp_path: Path,
    c_runtime_archive: Path,
    pcc_py_runtime_archive: Path,
    size: int,
    expected: str,
) -> None:
    suffix = str(size)
    oracle = _link_zpage_allocation_probe(
        tmp_path, "zpage_alloc_c_oracle_" + suffix, c_runtime_archive, size
    )
    implementation = _link_zpage_allocation_probe(
        tmp_path,
        "zpage_alloc_pcc_python_" + suffix,
        pcc_py_runtime_archive,
        size,
    )
    oracle_result = subprocess.run(
        [str(oracle)], capture_output=True, text=True, timeout=30
    )
    result = subprocess.run(
        [str(implementation)], capture_output=True, text=True, timeout=30
    )
    assert oracle_result.returncode == 0, oracle_result.stdout + oracle_result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert oracle_result.stdout == expected
    assert result.stdout == oracle_result.stdout
