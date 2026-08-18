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
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_zpage_lifecycle.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
DEALLOC_SOURCE = RUNTIME_DIR / "py" / "py_obj_dealloc.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend4_free_page_count_for_class",
    "pcc_gc_backend4_free_page_limit_for_class",
    "pcc_gc_backend4_sweep_deferred_recycles",
    "pcc_gc_backend4_zpage_cache",
    "pcc_gc_backend4_zpage_clear_reusable_state",
    "pcc_gc_backend4_zpage_destroy",
    "pcc_gc_backend4_zpage_detach_for_relocation",
    "pcc_gc_backend4_zpage_find",
    "pcc_gc_backend4_zpage_find_owner_for_page",
    "pcc_gc_backend4_zpage_free_detached_payload_spans",
    "pcc_gc_backend4_zpage_finish_relocation_detach",
    "pcc_gc_backend4_zpage_page_head",
    "pcc_gc_backend4_zpage_recycle",
    "pcc_gc_backend4_zpage_remove",
    "pcc_gc_backend4_zpage_remove_payload_span_base",
    "pcc_gc_backend4_zpage_remove_payload_spans",
    "pcc_gc_backend4_zpage_set_page_head",
    "pcc_gc_backend4_zpage_unlink_node",
    "pcc_gc_backend4_zpage_unlink_page",
}
RAW_FUNCTION_IMPORTS = {
    "free",
    "pcc_dealloc_cascade_active",
    "pcc_gc_backend4_zpage_clear_active_page",
    "pcc_gc_backend4_zpage_node_release",
    "pcc_gc_object_index_find",
    "pcc_gc_object_known_size",
    "pcc_gc_object_node_freeing",
    "pcc_gc_object_node_set_zpage",
    "pcc_gc_object_node_zpage",
    "pcc_gc_zpage_owner_index_find",
    "pcc_gc_zpage_owner_index_remove",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_dealloc_trash_head",
    "pcc_gc_backend4_deferred_recycle_pages",
    "pcc_gc_backend4_free_page_head",
    "pcc_gc_backend4_page_head",
    "pcc_gc_backend4_retained_page_head",
    "pcc_gc_backend4_selector_page_cursor",
    "pcc_gc_backend4_selector_page_seed",
    "pcc_gc_backend4_selector_page_seed_pending",
    "pcc_gc_backend4_selector_scan_best",
    "pcc_gc_backend4_selector_scan_best_score",
    "pcc_gc_backend4_selector_scan_cursor",
    "pcc_gc_backend4_selector_scan_restart",
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


def test_zpage_lifecycle_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    dealloc = DEALLOC_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_zpage_lifecycle" in makefile
    cross_object_only = {
        "pcc_gc_backend4_zpage_detach_for_relocation",
        "pcc_gc_backend4_zpage_free_detached_payload_spans",
        "pcc_gc_backend4_zpage_finish_relocation_detach",
    }
    for symbol in OWNED_SYMBOLS - {
        "pcc_gc_backend4_sweep_deferred_recycles",
    } - cross_object_only:
        assert f'"{symbol}"' in managed
        assert f'@c_abi_export("{symbol}")' not in managed
    assert '"pcc_gc_backend4_sweep_deferred_recycles"' in dealloc
    assert '@c_abi_export("pcc_gc_backend4_sweep_deferred_recycles")' not in dealloc
    assert 'define_global_ptr_null("pcc_dealloc_trash_head")' in dealloc


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_zpage_lifecycle_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("zpage_lifecycle_" + emitter + ".ll")
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

        source = tmp_path / "zpage_lifecycle.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("zpage_lifecycle_" + emitter + ".o")
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


def test_zpage_lifecycle_preserves_cache_and_forwarding_safety_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")

    assert "return 8" in strict
    assert "return 4" in strict
    assert 'global_store_ptr("pcc_gc_backend4_retained_page_head", page)' in strict
    assert "store_ptr(owner_node, 64, null())" in strict
    assert "offset + size" in strict
    assert "pcc_gc_zpage_owner_index_remove(load_ptr(node, 0))" in strict
    assert "load_i64(page, 96) <= 0" in strict
    assert 'global_load_ptr("pcc_dealloc_trash_head")' in strict
    assert "store_i32(page, 104, 1)" in strict


def test_backend4_free_path_never_scans_all_zpage_lists_for_origin() -> None:
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    start = managed.index('def pcc_gc_note_object_freeing(o) -> None:')
    end = managed.index('\n\n@c_abi_export(', start + 1)
    freeing = managed[start:end]

    assert "pcc_gc_object_index_find(o)" in freeing
    assert "zpage_flags" in freeing
    assert "_backend4_zpage_owns_addr" not in freeing
    assert "_backend4_zpage_owns_addr" not in managed
    assert "_backend4_zpage_list_owns_addr" not in managed


def test_production_archive_has_one_zpage_lifecycle_owner(
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
        assert ":freestanding_gc_zpage_lifecycle.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]


def _link_zpage_lifecycle_probe(
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
                PyObject *objects[18];
                for (int i = 0; i < 18; i++) {
                    objects[i] = pcc_gc_alloc(2048, PY_TYPE_LIST, 0);
                    if (objects[i] == 0) return 3;
                }
                printf("%lld,%lld\n",
                       (long long)pcc_gc_backend4_zpage_count(),
                       (long long)pcc_gc_backend4_zpage_free_pages());
                for (int i = 17; i >= 0; i--) pcc_gc_release(objects[i]);
                printf("%lld,%lld,%lld\n",
                       (long long)pcc_gc_backend4_zpage_count(),
                       (long long)pcc_gc_backend4_zpage_free_pages(),
                       (long long)pcc_gc_backend4_zpage_free_capacity_bytes());

                PyObject *reuse = pcc_gc_alloc(2048, PY_TYPE_LIST, 0);
                if (reuse == 0) return 4;
                printf("%lld,%lld\n",
                       (long long)pcc_gc_backend4_zpage_count(),
                       (long long)pcc_gc_backend4_zpage_free_pages());
                pcc_gc_release(reuse);
                printf("%lld,%lld\n",
                       (long long)pcc_gc_backend4_zpage_count(),
                       (long long)pcc_gc_backend4_zpage_free_pages());

                PyObject *large = pcc_gc_alloc(70000, PY_TYPE_LIST, 0);
                if (large == 0) return 5;
                printf("%lld,%lld,%lld\n",
                       (long long)pcc_gc_backend4_zpage_count(),
                       (long long)pcc_gc_backend4_zpage_large_pages(),
                       (long long)pcc_gc_backend4_zpage_free_pages());
                pcc_gc_release(large);
                printf("%lld,%lld\n",
                       (long long)pcc_gc_backend4_zpage_count(),
                       (long long)pcc_gc_backend4_zpage_free_pages());
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


def _link_zpage_tail_reuse_probe(
    tmp_path: Path, name: str, archive: Path
) -> Path:
    source = tmp_path / (name + ".c")
    executable = tmp_path / name
    source.write_text(
        textwrap.dedent(
            r'''
            #include "py_runtime.h"
            #include <stdio.h>

            static void print_delta(
                int64_t count0,
                int64_t allocated0,
                int64_t used0
            ) {
                printf("%lld,%lld,%lld\n",
                       (long long)(pcc_gc_backend4_zpage_count() - count0),
                       (long long)(pcc_gc_backend4_zpage_allocated_bytes() - allocated0),
                       (long long)(pcc_gc_backend4_zpage_used_bytes() - used0));
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING) != 0) {
                    return 2;
                }
                int64_t count0 = pcc_gc_backend4_zpage_count();
                int64_t allocated0 = pcc_gc_backend4_zpage_allocated_bytes();
                int64_t used0 = pcc_gc_backend4_zpage_used_bytes();

                PyObject *anchor = pcc_gc_alloc(128, PY_TYPE_LIST, 0);
                PyObject *dict = py_dict_new();
                if (anchor == 0 || dict == 0) return 3;
                int64_t dict_offset =
                    pcc_gc_backend4_zpage_owner_offset_bytes(dict);
                if (dict_offset != 128) return 4;
                print_delta(count0, allocated0, used0);

                /* A fresh dict reserves a 56-byte owner followed by its
                 * 192-byte entries payload.  Dropping that tail bundle must
                 * expose the anchor tail, not strand the owner header. */
                pcc_gc_release(dict);
                print_delta(count0, allocated0, used0);

                PyObject *replacement = py_dict_new();
                if (replacement == 0) return 5;
                if (pcc_gc_backend4_zpage_owner_offset_bytes(replacement)
                    != dict_offset) return 6;
                print_delta(count0, allocated0, used0);

                pcc_gc_release(replacement);
                pcc_gc_release(anchor);
                print_delta(count0, allocated0, used0);
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


def test_zpage_lifecycle_matches_c_oracle_for_cache_limit_reuse_and_large_retire(
    tmp_path: Path,
    c_runtime_archive: Path,
    pcc_py_runtime_archive: Path,
) -> None:
    oracle = _link_zpage_lifecycle_probe(
        tmp_path, "zpage_lifecycle_c_oracle", c_runtime_archive
    )
    implementation = _link_zpage_lifecycle_probe(
        tmp_path, "zpage_lifecycle_pcc_python", pcc_py_runtime_archive
    )
    oracle_result = subprocess.run(
        [str(oracle)], capture_output=True, text=True, timeout=30
    )
    result = subprocess.run(
        [str(implementation)], capture_output=True, text=True, timeout=30
    )
    assert oracle_result.returncode == 0, oracle_result.stdout + oracle_result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert oracle_result.stdout == (
        "9,0\n"
        "0,8,32768\n"
        "1,7\n"
        "0,8\n"
        "1,1,8\n"
        "0,8\n"
    )
    assert result.stdout == oracle_result.stdout


def test_zpage_lifecycle_matches_c_oracle_for_owner_payload_tail_reuse(
    tmp_path: Path,
    c_runtime_archive: Path,
    pcc_py_runtime_archive: Path,
) -> None:
    oracle = _link_zpage_tail_reuse_probe(
        tmp_path, "zpage_tail_reuse_c_oracle", c_runtime_archive
    )
    implementation = _link_zpage_tail_reuse_probe(
        tmp_path, "zpage_tail_reuse_pcc_python", pcc_py_runtime_archive
    )
    oracle_result = subprocess.run(
        [str(oracle)], capture_output=True, text=True, timeout=30
    )
    result = subprocess.run(
        [str(implementation)], capture_output=True, text=True, timeout=30
    )
    assert oracle_result.returncode == 0, oracle_result.stdout + oracle_result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert oracle_result.stdout == (
        "1,376,376\n"
        "1,128,128\n"
        "1,376,376\n"
        "0,0,0\n"
    )
    assert result.stdout == oracle_result.stdout
