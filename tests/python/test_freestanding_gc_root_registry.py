from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from pcc.py_frontend import pipeline
from pcc.py_frontend.codegen.runtime_abi import FREESTANDING_GC_RUNTIME_GLOBALS
from tests.runtime_build_cache import cached_threaded_pcc_python_runtime


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"
REGISTRY_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_root_registry.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
ORACLE_SOURCE = RUNTIME_DIR / "src" / "py_gc_backend.c"
MAKEFILE = RUNTIME_DIR / "Makefile"

PUBLIC_SYMBOLS = {
    "pcc_gc_scheduler_root_register_handle",
    "pcc_gc_scheduler_root_register",
    "pcc_gc_scheduler_root_unregister_handle",
    "pcc_gc_scheduler_root_unregister",
    "pcc_gc_register_continuation_root",
    "pcc_gc_unregister_continuation_root",
}
INTERNAL_SYMBOLS = {
    "pcc_gc_cycle_requested_store_release",
    "pcc_gc_scheduler_root_link_locked",
    "pcc_gc_scheduler_root_unlink_locked",
    "pcc_gc_root_slot_count_from_map",
    "pcc_gc_root_map_is_borrowed",
}
RAW_FUNCTION_IMPORTS = {
    "free",
    "malloc",
    "memset",
    "pcc_gc_backend",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_cycle_requested",
    "pcc_gc_scheduler_root_head",
    "pcc_gc_continuation_root_head",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _compile_object(tmp_path: Path, emitter: str) -> tuple[Path, str]:
    llvm_ir = tmp_path / ("freestanding_gc_root_registry_" + emitter + ".ll")
    pipeline.compile_python(
        str(REGISTRY_SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        libpython_mode="off",
        python_library=True,
    )
    ir_text = llvm_ir.read_text(encoding="utf-8")
    source = llvm_ir
    if emitter == "self":
        from pcc.backend.self_backend_dispatch import emit_self_asm

        source = tmp_path / "freestanding_gc_root_registry.s"
        source.write_text(
            emit_self_asm(ir_text), encoding="utf-8"
        )
    obj = tmp_path / ("freestanding_gc_root_registry_" + emitter + ".o")
    result = subprocess.run(
        ["clang", "-c", str(source), "-o", str(obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return obj, ir_text


def test_root_registry_has_one_strict_source_owner_and_one_lock_contract():
    strict = REGISTRY_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    oracle = ORACLE_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == PUBLIC_SYMBOLS | INTERNAL_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(PUBLIC_SYMBOLS | INTERNAL_SYMBOLS)
    assert "freestanding_gc_root_registry" in makefile
    assert 'atomic_store_i32(slot, 0, value, "release")' in strict
    assert "__atomic_store_n(&pcc_gc_cycle_requested, value, __ATOMIC_RELEASE)" in oracle
    for name in PUBLIC_SYMBOLS:
        body = strict.split("def " + name, 1)[1].split("\n\n@", 1)[0]
        assert "gc_backend_current()" in body or name in {
            "pcc_gc_scheduler_root_register",
        }


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_root_registry_object_has_exact_raw_closure(tmp_path: Path, emitter: str):
    obj, ir_text = _compile_object(tmp_path, emitter)
    assert RAW_GLOBAL_IMPORTS <= FREESTANDING_GC_RUNTIME_GLOBALS
    assert "store atomic i32" in ir_text
    assert "release, align 4" in ir_text
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
    assert defined == PUBLIC_SYMBOLS | INTERNAL_SYMBOLS


def _harness_source(concurrent: bool) -> str:
    if concurrent:
        return r'''
#include "py_runtime.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>

enum { THREADS = 4, ROUNDS = 512 };
static PyObject *scheduler_slots[THREADS];
static PyObject *continuation_slots[THREADS][2];
static int32_t continuation_maps[THREADS][1] = {{-2}, {-2}, {-2}, {-2}};

static void *mutate(void *raw) {
    intptr_t index = (intptr_t)raw;
    for (int i = 0; i < ROUNDS; i++) {
        void *handle = pcc_gc_scheduler_root_register_handle(
            &scheduler_slots[index]
        );
        if (handle == NULL) return (void *)1;
        pcc_gc_register_continuation_root(
            continuation_maps[index], continuation_slots[index]
        );
        pcc_gc_unregister_continuation_root(continuation_slots[index]);
        pcc_gc_scheduler_root_unregister_handle(handle);
    }
    return NULL;
}

static void *observe(void *raw) {
    (void)raw;
    for (int i = 0; i < THREADS * ROUNDS; i++) {
        int64_t scheduler = pcc_gc_scheduler_root_count();
        int64_t continuation = pcc_gc_continuation_root_slot_count();
        if (scheduler < 0 || scheduler > THREADS) return (void *)1;
        if (continuation < 0 || continuation > THREADS * 2) return (void *)1;
    }
    return NULL;
}

int main(void) {
    pthread_t workers[THREADS];
    pthread_t observer;
    for (intptr_t i = 0; i < THREADS; i++) {
        if (pthread_create(&workers[i], NULL, mutate, (void *)i) != 0) return 2;
    }
    if (pthread_create(&observer, NULL, observe, NULL) != 0) return 3;
    for (int i = 0; i < THREADS; i++) {
        void *result = NULL;
        pthread_join(workers[i], &result);
        if (result != NULL) return 4;
    }
    void *result = NULL;
    pthread_join(observer, &result);
    if (result != NULL) return 5;
    printf("final:%lld,%lld\n",
           (long long)pcc_gc_scheduler_root_count(),
           (long long)pcc_gc_continuation_root_slot_count());
    return 0;
}
'''
    return r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

int main(void) {
    PyObject *scheduler_slots[2] = {NULL, NULL};
    void *first = pcc_gc_scheduler_root_register_handle(&scheduler_slots[0]);
    pcc_gc_scheduler_root_register(&scheduler_slots[1]);
    void *second = pcc_gc_scheduler_root_register_handle(&scheduler_slots[0]);
    if (first == NULL || second == NULL) return 1;
    printf("scheduler:%lld\n", (long long)pcc_gc_scheduler_root_count());
    pcc_gc_scheduler_root_unregister(&scheduler_slots[0]);
    printf("scheduler-slot:%lld\n", (long long)pcc_gc_scheduler_root_count());
    pcc_gc_scheduler_root_unregister_handle(first);
    pcc_gc_scheduler_root_unregister(&scheduler_slots[1]);
    printf("scheduler-empty:%lld\n", (long long)pcc_gc_scheduler_root_count());

    int32_t owned_map[1] = {2};
    int32_t borrowed_map[1] = {-2};
    int32_t zero_map[1] = {0};
    int32_t min_map[1] = {(int32_t)0x80000000u};
    int32_t huge_map[1] = {100001};
    PyObject *owned[2] = {NULL, NULL};
    PyObject *borrowed[2] = {NULL, NULL};
    pcc_gc_register_continuation_root(owned_map, owned);
    pcc_gc_register_continuation_root(borrowed_map, borrowed);
    pcc_gc_register_continuation_root(zero_map, owned);
    pcc_gc_register_continuation_root(min_map, owned);
    pcc_gc_register_continuation_root(huge_map, owned);
    printf("continuation:%lld\n",
           (long long)pcc_gc_continuation_root_slot_count());
    pcc_gc_unregister_continuation_root(owned);
    printf("continuation-owned:%lld\n",
           (long long)pcc_gc_continuation_root_slot_count());
    pcc_gc_unregister_continuation_root(borrowed);
    printf("continuation-empty:%lld\n",
           (long long)pcc_gc_continuation_root_slot_count());
    return 0;
}
'''


def _link_harness(
    tmp_path: Path, name: str, archive: Path, *, concurrent: bool = False
) -> Path:
    source = tmp_path / (name + ".c")
    executable = tmp_path / name
    source.write_text(_harness_source(concurrent), encoding="utf-8")
    command = [
        "clang",
        "-std=c11",
        f"-I{RUNTIME_DIR / 'include'}",
        str(source),
        str(archive),
    ]
    if concurrent:
        command.append("-pthread")
    command.extend(["-o", str(executable)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


def test_production_archive_uniquely_owns_registry_and_matches_c_oracle(
    tmp_path: Path,
    c_runtime_archive: Path,
    pcc_py_runtime_archive: Path,
):
    members_result = subprocess.run(
        ["ar", "-t", str(pcc_py_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert members_result.returncode == 0, members_result.stdout + members_result.stderr
    assert "freestanding_gc_root_registry.o" in members_result.stdout.splitlines()

    symbols_result = subprocess.run(
        ["nm", "-A", "-g", str(pcc_py_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    owners: dict[str, list[str]] = {
        symbol: [] for symbol in PUBLIC_SYMBOLS | INTERNAL_SYMBOLS
    }
    for line in symbols_result.stdout.splitlines():
        symbol = line.split()[-1].lstrip("_") if line.strip() else ""
        if symbol in owners and " U " not in line:
            owners[symbol].append(line)
    assert all(len(lines) == 1 for lines in owners.values())
    assert all(
        ":freestanding_gc_root_registry.o:" in lines[0]
        for lines in owners.values()
    )
    assert not any(
        ":py_gc_backend.o:" in line for lines in owners.values() for line in lines
    )

    oracle = _link_harness(tmp_path, "gc_registry_c_oracle", c_runtime_archive)
    implementation = _link_harness(
        tmp_path, "gc_registry_pcc_python", pcc_py_runtime_archive
    )
    for backend in range(5):
        env = {**os.environ, "PCC_GC_BACKEND": str(backend)}
        oracle_result = subprocess.run(
            [str(oracle)], env=env, capture_output=True, text=True, timeout=30
        )
        result = subprocess.run(
            [str(implementation)], env=env, capture_output=True, text=True, timeout=30
        )
        assert oracle_result.returncode == 0, oracle_result.stdout + oracle_result.stderr
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == oracle_result.stdout


def test_root_registry_survives_pthread_mutation_and_observation(
    tmp_path: Path,
    threaded_c_runtime_archive: Path,
):
    threaded_pcc_python_archive = (
        cached_threaded_pcc_python_runtime() / "libpy_runtime_pcc_py.a"
    )
    oracle = _link_harness(
        tmp_path,
        "gc_registry_threads_c_oracle",
        threaded_c_runtime_archive,
        concurrent=True,
    )
    implementation = _link_harness(
        tmp_path,
        "gc_registry_threads_pcc_python",
        threaded_pcc_python_archive,
        concurrent=True,
    )
    for backend in range(5):
        env = {**os.environ, "PCC_GC_BACKEND": str(backend)}
        oracle_result = subprocess.run(
            [str(oracle)], env=env, capture_output=True, text=True, timeout=30
        )
        result = subprocess.run(
            [str(implementation)], env=env, capture_output=True, text=True, timeout=30
        )
        assert oracle_result.returncode == 0, oracle_result.stdout + oracle_result.stderr
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == oracle_result.stdout == "final:0,0\n"
