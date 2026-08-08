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
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_relocation_drain.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend4_evacuation_drain",
    "pcc_gc_backend4_evacuation_page_drain",
    "pcc_gc_relocation_drain_evacuation_page_head",
    "pcc_gc_relocation_drain_note_incomplete_batch",
    "pcc_gc_relocation_drain_relocation_set_head",
    "pcc_gc_relocation_drain_remap_if_drained_unlocked",
    "pcc_gc_relocation_drain_selected",
    "pcc_gc_relocation_drain_selected_page",
}
RAW_FUNCTION_IMPORTS = {
    "pcc_gc_backend",
    "pcc_gc_backend4_relocate_copy_unlocked",
    "pcc_gc_backend4_remap_and_retire_unlocked",
    "pcc_gc_backend4_zpage_page_for_owner",
    "pcc_gc_config_ensure",
    "pcc_gc_object_known_size",
    "pcc_gc_relocate_copy",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    "pcc_thread_safepoint",
    "py_decref",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend4_evacuation_incomplete_batches_count",
    "pcc_gc_backend4_evacuation_page_head",
    "pcc_gc_relocation_set_head",
    "pcc_gc_forwarding_population",
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


def test_relocation_drain_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_relocation_drain" in makefile
    assert "def _relocate_selected(" not in managed
    assert "def _relocate_selected_page(" not in managed
    assert 'pcc_gc_backend4_evacuation_drain = extern(' in managed
    assert 'pcc_gc_backend4_evacuation_page_drain = extern(' in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_relocation_drain_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("relocation_drain_" + emitter + ".ll")
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

        source = tmp_path / "relocation_drain.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("relocation_drain_" + emitter + ".o")
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


def test_relocation_drain_preserves_budget_lock_and_handoff_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")

    assert "while ptr_is_null(node) == 0 and moved < budget:" in strict
    assert "if (moved & 15) == 0:" in strict
    assert "pcc_gc_backend4_relocate_copy_unlocked(" in strict
    assert "pcc_gc_backend4_zpage_page_for_owner(obj)" in strict
    assert "while pages < page_budget:" in strict
    assert "pcc_py_gc_minor_graph_lock()" in strict
    assert "pcc_py_gc_minor_graph_unlock()" in strict
    assert "pcc_gc_backend4_remap_and_retire_unlocked()" in strict
    assert "pcc_gc_backend4_evacuation_incomplete_batches_count" in strict


def test_production_archive_has_one_relocation_drain_owner(
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
        assert ":freestanding_gc_relocation_drain.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]


def _link_drain_probe(tmp_path: Path, name: str, archive: Path) -> Path:
    source = tmp_path / (name + ".c")
    executable = tmp_path / name
    source.write_text(
        textwrap.dedent(
            r'''
            #include "py_runtime.h"
            #include <stdio.h>

            enum { PY_FLAG_GC_OLD = 0x100 };

            int main(int argc, char **argv) {
                if (pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING) != 0) {
                    return 2;
                }
                PyObject *a = pcc_gc_alloc(128, PY_TYPE_LIST, PY_FLAG_GC_OLD);
                PyObject *b = pcc_gc_alloc(128, PY_TYPE_LIST, PY_FLAG_GC_OLD);
                if (a == 0 || b == 0) return 3;
                pcc_gc_telemetry_reset();
                if (pcc_gc_select_relocation_set(8) != 2) return 4;

                long long first = 0;
                long long second = 0;
                long long third = 0;
                if (argc > 1 && argv[1][0] == 's') {
                    first = pcc_gc_step(1);
                } else if (argc > 1 && argv[1][0] == 'p') {
                    first = pcc_gc_backend4_evacuation_page_drain(1);
                    second = pcc_gc_backend4_evacuation_page_drain(1);
                } else {
                    first = pcc_gc_backend4_evacuation_drain(1);
                    second = pcc_gc_backend4_evacuation_drain(1);
                    third = pcc_gc_backend4_evacuation_drain(1);
                }
                printf("%lld,%lld,%lld,%lld,%lld,%lld,%lld\n",
                       first,
                       second,
                       third,
                       (long long)pcc_gc_relocation_set_size(),
                       (long long)pcc_gc_backend4_evacuation_page_candidate_score(),
                       (long long)pcc_gc_backend4_evacuation_incomplete_batches(),
                       (long long)pcc_gc_backend4_evacuation_efficiency_per_mille());
                return 0;
            }
            '''
        ).lstrip(),
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
    ("argument", "expected"),
    [
        ("object", "1,1,0,0,0,1,1000\n"),
        ("page", "2,0,0,0,0,0,1000\n"),
        ("step", "2,0,0,0,0,0,1000\n"),
    ],
)
def test_relocation_drain_matches_c_oracle_for_object_page_and_step_budgets(
    tmp_path: Path,
    c_runtime_archive: Path,
    pcc_py_runtime_archive: Path,
    argument: str,
    expected: str,
) -> None:
    oracle = _link_drain_probe(tmp_path, "drain_c_oracle_" + argument, c_runtime_archive)
    implementation = _link_drain_probe(
        tmp_path, "drain_pcc_python_" + argument, pcc_py_runtime_archive
    )
    oracle_result = subprocess.run(
        [str(oracle), argument], capture_output=True, text=True, timeout=30
    )
    result = subprocess.run(
        [str(implementation), argument], capture_output=True, text=True, timeout=30
    )
    assert oracle_result.returncode == 0, oracle_result.stdout + oracle_result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert oracle_result.stdout == expected
    assert result.stdout == oracle_result.stdout
