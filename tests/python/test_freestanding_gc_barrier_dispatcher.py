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
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_barrier_dispatcher.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

PUBLIC_SYMBOLS = {
    "pcc_gc_note_slot_write_barrier",
    "pcc_gc_note_write_barrier",
    "pcc_gc_step",
}
OWNED_SYMBOLS = PUBLIC_SYMBOLS | {
    "pcc_gc_dispatch_ptr_can_have_header",
    "pcc_gc_dispatch_selected_backend",
    "pcc_gc_dispatch_tracing_work_pending",
}
RAW_PROVIDER_SYMBOLS = {
    "pcc_gc_backend4_step_generation_aging",
    "pcc_gc_backend4_step_remembered_roots",
    "pcc_gc_backend4_store_buffer_enqueue",
}
RAW_FUNCTION_IMPORTS = {
    "pcc_gc_backend3_remember_owner",
    "pcc_gc_backend4_evacuation_page_drain",
    "pcc_gc_backend4_remap_and_retire_unlocked",
    "pcc_gc_backend4_select_relocation_pages",
    "pcc_gc_config_ensure",
    "pcc_gc_generational_step",
    "pcc_gc_incremental_concurrent_step",
    "pcc_gc_object_is_known_no_lock",
    "pcc_gc_tracing_has_sweep_candidate",
    "pcc_gc_tracing_record_pause",
    "pcc_gc_tracing_step_cycle",
    "pcc_platform_monotonic_us",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    "pcc_resume_world",
    "pcc_stop_the_world",
} | RAW_PROVIDER_SYMBOLS
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend4_genzgc_store_barriers",
    "pcc_gc_backend_selected",
    "pcc_gc_cms_wb_flushes",
    "pcc_gc_config_initialized",
    "pcc_gc_cycle_requested",
    "pcc_gc_explicit_collect_active",
    "pcc_gc_forwarding_population",
    "pcc_gc_mark_active",
    "pcc_gc_metric_step",
    "pcc_gc_relocation_set_head",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _export_body(source: str, symbol: str) -> str:
    return source.split(f'@c_abi_export("{symbol}")', 1)[1].split(
        "\n@c_abi_export", 1
    )[0]


def _literal_global_imports() -> set[str]:
    globals_: set[str] = set()
    tree = ast.parse(STRICT_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "global_addr" and node.func.id != "global_load_ptr":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                globals_.add(value)
    return globals_


def test_barrier_dispatcher_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert RAW_PROVIDER_SYMBOLS <= _exported_symbols(managed)
    assert "freestanding_gc_barrier_dispatcher" in makefile
    for symbol in PUBLIC_SYMBOLS:
        assert f'"{symbol}"' in managed
        assert f'@c_abi_export("{symbol}")' not in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_barrier_dispatcher_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("barrier_dispatcher_" + emitter + ".ll")
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

        source = tmp_path / "barrier_dispatcher.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("barrier_dispatcher_" + emitter + ".o")
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


def test_barrier_dispatcher_preserves_backend_order_and_lock_contract() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    step = _export_body(strict, "pcc_gc_step")
    barrier = _export_body(strict, "pcc_gc_note_slot_write_barrier")

    assert step.index("pcc_gc_incremental_concurrent_step(budget)") < step.index(
        'global_addr("pcc_gc_metric_step")'
    )
    assert step.index("pcc_gc_backend4_step_remembered_roots") < step.index(
        "pcc_gc_backend4_step_generation_aging"
    )
    assert step.index("pcc_gc_backend4_step_generation_aging") < step.index(
        "pcc_gc_backend4_evacuation_page_drain"
    )
    assert step.index("pcc_gc_backend4_evacuation_page_drain") < step.index(
        "pcc_gc_backend4_select_relocation_pages"
    )
    assert "pcc_gc_backend4_remap_and_retire_unlocked()" in step
    assert "pcc_gc_tracing_record_pause(" in step

    assert "_ptr_can_have_header(owner)" in barrier
    assert "_ptr_can_have_header(value)" in barrier
    generational = barrier.split("if backend != 3 and backend != 4:", 1)[1]
    assert generational.index("value_flags & 128") < generational.index(
        "pcc_py_gc_minor_graph_lock()"
    )
    assert "pcc_gc_object_is_known_no_lock(owner)" in barrier
    assert "pcc_gc_object_is_known_no_lock(value)" in barrier
    assert "pcc_gc_backend3_remember_owner(owner, owner_flags)" in barrier
    assert "pcc_gc_backend4_store_buffer_enqueue(owner, slot, value)" in barrier


def test_production_archive_has_one_barrier_dispatcher_owner(
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
        assert ":freestanding_gc_barrier_dispatcher.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]


def _link_barrier_probe(
    tmp_path: Path, name: str, archive: Path, backend_kind: int
) -> Path:
    source = tmp_path / (name + ".c")
    executable = tmp_path / name
    source.write_text(
        textwrap.dedent(
            f"""
            #include "py_runtime.h"
            #include <stdio.h>

            enum {{
                PY_FLAG_GC_YOUNG = 0x80,
                PY_FLAG_GC_OLD = 0x100,
                PY_FLAG_GC_REMEMBERED = 0x200
            }};

            int main(void) {{
                if (pcc_gc_set_backend({backend_kind}) != 0) return 2;
                pcc_gc_telemetry_reset();
                PyObject *owner = pcc_gc_alloc(32, PY_TYPE_LIST, 0);
                PyObject *value = py_list_new(0);
                PyObject *slot = 0;
                if (owner == 0 || value == 0) return 3;
                ((PyObjectHeader *)owner)->flags =
                    (((PyObjectHeader *)owner)->flags &
                     ~(PY_FLAG_GC_YOUNG | PY_FLAG_GC_REMEMBERED)) |
                    PY_FLAG_GC_OLD;
                if ((((PyObjectHeader *)value)->flags & PY_FLAG_GC_YOUNG) == 0)
                    return 4;

                pcc_gc_note_slot_write_barrier(owner, &slot, value);
                printf("%d,%lld,%lld\\n",
                       ((((PyObjectHeader *)owner)->flags &
                         PY_FLAG_GC_REMEMBERED) != 0),
                       (long long)pcc_gc_backend4_generation_barrier_score(),
                       (long long)pcc_gc_backend4_store_buffer_entries());
                return 0;
            }}
            """
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
    ("backend_kind", "expected"),
    [(3, "1,0,0\n"), (4, "1,1,1\n")],
)
def test_barrier_dispatcher_matches_c_oracle_for_old_to_young_edges(
    tmp_path: Path,
    c_runtime_archive: Path,
    pcc_py_runtime_archive: Path,
    backend_kind: int,
    expected: str,
) -> None:
    oracle = _link_barrier_probe(
        tmp_path, "barrier_c_" + str(backend_kind), c_runtime_archive, backend_kind
    )
    implementation = _link_barrier_probe(
        tmp_path,
        "barrier_pcc_python_" + str(backend_kind),
        pcc_py_runtime_archive,
        backend_kind,
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
