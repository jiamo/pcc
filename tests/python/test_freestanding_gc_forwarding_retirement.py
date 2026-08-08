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
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_forwarding_retirement.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
C_ORACLE_SOURCE = RUNTIME_DIR / "src" / "py_gc_backend.c"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend4_drain_parked_pages",
    "pcc_gc_backend4_note_forwarding_removed_on_page",
    "pcc_gc_backend4_park_page",
    "pcc_gc_backend4_remap_and_retire_unlocked",
    "pcc_gc_backend4_zpage_note_forwarding_removed",
    "pcc_gc_forwarding_remove",
    "pcc_gc_forwarding_remove_target",
}
LOCAL_HELPER_SYMBOLS = {
    "pcc_gc_backend4_release_retained_pages_unlocked",
    "pcc_gc_retire_forwarded_source_unlocked",
}
DEFINED_SYMBOLS = OWNED_SYMBOLS | LOCAL_HELPER_SYMBOLS
RAW_FUNCTION_IMPORTS = {
    "free",
    "pcc_dealloc_cascade_active",
    "pcc_gc_backend",
    "pcc_gc_backend4_remap_referents",
    "pcc_gc_backend4_zpage_clear_active_page",
    "pcc_gc_backend4_zpage_destroy",
    "pcc_gc_backend4_zpage_find_page_for_addr",
    "pcc_gc_backend4_zpage_unlink_page",
    "pcc_gc_forwarding_index_remove",
    "pcc_gc_forwarding_list_head",
    "pcc_gc_forwarding_target_index_remove",
    "pcc_gc_forwarding_target_unlink",
    "pcc_gc_forwarding_unlink_main",
    "pcc_gc_identity_remove",
    "pcc_gc_live_bytes_subtract",
    "pcc_gc_managed_pointer_index_remove",
    "pcc_gc_object_index_find",
    "pcc_gc_object_index_remove",
    "pcc_gc_object_list_head",
    "pcc_gc_object_node_freeing",
    "pcc_gc_object_node_release",
    "pcc_gc_object_node_size",
    "pcc_gc_object_node_unlink",
    "pcc_gc_visit_registered_root_slots",
    "py_decref",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend4_deferred_recycle_pages",
    "pcc_gc_backend4_parked_head",
    "pcc_gc_backend4_retained_page_head",
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


def test_forwarding_retirement_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == DEFINED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(DEFINED_SYMBOLS)
    assert "freestanding_gc_forwarding_retirement" in makefile
    for symbol in OWNED_SYMBOLS:
        assert f'"{symbol}"' in managed
        assert f'@c_abi_export("{symbol}")' not in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_forwarding_retirement_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("forwarding_retirement_" + emitter + ".ll")
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

        source = tmp_path / "forwarding_retirement.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("forwarding_retirement_" + emitter + ".o")
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
    assert defined == DEFINED_SYMBOLS


def test_forwarding_retirement_preserves_one_epoch_and_park_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")

    assert "old_flags & 131072" in strict
    assert "old_flags | 131072" in strict
    assert "old_flags & ~(2048 | 131072)" in strict
    assert "pcc_gc_visit_registered_root_slots(3, 0)" in strict
    assert "pcc_gc_backend4_drain_parked_pages()" in strict
    assert 'global_store_ptr("pcc_gc_backend4_parked_head", page)' in strict
    assert "load_i64(page, 96)" in strict
    assert "pcc_gc_backend4_zpage_unlink_page(page)" in strict
    assert "pcc_gc_backend4_park_page(page)" in strict
    assert "if ptr_eq(load_ptr(scan, 0), from_obj) != 0:" in strict
    assert "_retire_forwarded_source(old)" in strict


def test_forwarding_retirement_releases_only_after_two_remap_epochs() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    oracle = C_ORACLE_SOURCE.read_text(encoding="utf-8")

    release_start = strict.index("def _release_retained_pages")
    remap_start = strict.index("def pcc_gc_backend4_remap_and_retire_unlocked")
    remap = strict[remap_start:]
    assert remap.index("_release_retained_pages()") < remap.index(
        "pcc_gc_backend4_drain_parked_pages()"
    )
    release = strict[release_start:remap_start]
    assert 'global_store_ptr("pcc_gc_backend4_retained_page_head", null())' in release
    assert "load_i64(page, 32) > 0" in release
    assert "load_i64(page, 88) > 0" in release
    assert "load_i64(page, 96) > 0" in release
    assert "free(span)" in release
    assert "free(page)" in release

    oracle_remap = oracle[oracle.index("static void pcc_gc_backend4_remap_and_retire_unlocked") :]
    assert oracle_remap.index(
        "pcc_gc_backend4_release_retained_pages_unlocked();"
    ) < oracle_remap.index("pcc_gc_backend4_drain_parked_pages_unlocked();")
    assert "page->pending_forwardings > 0" in oracle
    assert "free(page->span_base);" in oracle
    assert "free(page);" in oracle


def test_production_archive_has_one_forwarding_retirement_owner(
    pcc_py_runtime_archive: Path,
) -> None:
    symbols_result = subprocess.run(
        ["nm", "-A", "-g", str(pcc_py_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    for symbol in DEFINED_SYMBOLS:
        owners = [
            line
            for line in symbols_result.stdout.splitlines()
            if line.strip()
            and line.split()[-1].lstrip("_") == symbol
            and " U " not in line
        ]
        assert len(owners) == 1, (symbol, owners)
        assert ":freestanding_gc_forwarding_retirement.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]


def _link_forwarding_retirement_probe(
    tmp_path: Path, name: str, archive: Path
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
                PyObject *root_a = 0;
                PyObject *root_b = 0;
                pcc_gc_scheduler_root_register(&root_a);
                pcc_gc_scheduler_root_register(&root_b);
                PyObject *a = pcc_gc_alloc(128, PY_TYPE_LIST, 0);
                PyObject *b = pcc_gc_alloc(128, PY_TYPE_LIST, 0);
                if (a == 0 || b == 0) return 3;
                pcc_gc_store_root(&root_a, a);
                pcc_gc_store_root(&root_b, b);
                pcc_gc_release(a);
                pcc_gc_release(b);

                pcc_gc_reset_relocation_set();
                if (pcc_gc_select_relocation_set(8) != 2) return 4;
                if (pcc_gc_backend4_evacuation_page_drain(1) != 2) return 5;
                printf("%lld\n", (long long)pcc_gc_backend4_forwarding_entries());

                (void)pcc_gc_step(256);
                printf("%lld\n", (long long)pcc_gc_backend4_forwarding_entries());
                (void)pcc_gc_step(256);
                printf("%lld\n", (long long)pcc_gc_backend4_forwarding_entries());
                (void)pcc_gc_step(256);
                printf("%lld,%lld\n",
                       (long long)pcc_gc_backend4_forwarding_entries(),
                       (long long)pcc_gc_backend4_verify_no_old_addresses());
                if (pcc_gc_load_ptr(0, &root_a) == a) return 6;
                if (pcc_gc_load_ptr(0, &root_b) == b) return 7;

                pcc_gc_store_root(&root_a, 0);
                pcc_gc_store_root(&root_b, 0);
                pcc_gc_scheduler_root_unregister(&root_a);
                pcc_gc_scheduler_root_unregister(&root_b);
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


def test_forwarding_retirement_matches_c_oracle_across_three_remap_epochs(
    tmp_path: Path,
    c_runtime_archive: Path,
    pcc_py_runtime_archive: Path,
) -> None:
    oracle = _link_forwarding_retirement_probe(
        tmp_path, "forwarding_retirement_c_oracle", c_runtime_archive
    )
    implementation = _link_forwarding_retirement_probe(
        tmp_path, "forwarding_retirement_pcc_python", pcc_py_runtime_archive
    )
    oracle_result = subprocess.run(
        [str(oracle)], capture_output=True, text=True, timeout=30
    )
    result = subprocess.run(
        [str(implementation)], capture_output=True, text=True, timeout=30
    )
    assert oracle_result.returncode == 0, oracle_result.stdout + oracle_result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert oracle_result.stdout == "2\n0\n0\n0,1\n"
    assert result.stdout == oracle_result.stdout
