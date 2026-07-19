from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from unittest import mock

from tests.runtime_build_cache import (
    cached_c_runtime,
    cached_threaded_c_runtime,
    cached_threaded_pcc_python_runtime,
)

REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "py_runtime"

_PCC_PY_RUNTIME_BUILD_CACHE: Path | None = None


def _compile_probe(
    tmp_path,
    source: str,
    *,
    runtime_cc: str | None = None,
    runtime_high: str | None = None,
    backend: str | None = None,
    ir_scaffold_mode: str | None = "on",
):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "probe.py"
    exe = tmp_path / "probe.out"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    env = {}
    if runtime_cc is not None:
        env["PCC_RUNTIME_CC"] = runtime_cc
    if runtime_high is not None:
        env["PCC_RUNTIME_HIGH"] = runtime_high
    with mock.patch.dict(os.environ, env, clear=False):
        compile_python(
            str(src),
            str(exe),
            ir_scaffold_mode=ir_scaffold_mode,
            libpython_mode="off",
            backend=backend,
        )
    return exe


def _cc() -> str:
    return os.environ.get("CC", "cc")


def _build_runtime(tmp_path: Path) -> Path:
    del tmp_path
    return cached_c_runtime()


def _build_threaded_runtime(tmp_path: Path) -> Path:
    del tmp_path
    return cached_threaded_c_runtime()


def _build_pcc_py_runtime(tmp_path: Path) -> Path:
    global _PCC_PY_RUNTIME_BUILD_CACHE
    if (
        _PCC_PY_RUNTIME_BUILD_CACHE is not None
        and (_PCC_PY_RUNTIME_BUILD_CACHE / "libpy_runtime_pcc_py.a").is_file()
    ):
        return _PCC_PY_RUNTIME_BUILD_CACHE
    _PCC_PY_RUNTIME_BUILD_CACHE = cached_threaded_pcc_python_runtime()
    return _PCC_PY_RUNTIME_BUILD_CACHE


def _run_backend_three(exe):
    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_BACKEND": "3",
            "PCC_GC_MINOR_HEAP_SIZE": "1024",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    return subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )


def test_gc_frame_index_accepts_raw_slot_pointer_keys(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "frame_index_raw_pointer_probe.c"
    exe = tmp_path / "frame_index_raw_pointer_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_internal.h"
            #include <stdint.h>

            int main(void) {
                char storage[32];
                void *odd_slot_key = (void *)(storage + 1);
                uintptr_t node = 0x1234;
                if (pcc_gc_frame_index_insert(odd_slot_key, &node) != 1) {
                    return 3;
                }
                if (pcc_gc_frame_index_find(odd_slot_key) != &node) {
                    return 4;
                }
                uintptr_t replacement = 0x5678;
                if (pcc_gc_frame_index_replace(odd_slot_key, &replacement) != &node) {
                    return 5;
                }
                if (pcc_gc_frame_index_find(odd_slot_key) != &replacement) {
                    return 6;
                }
                if (pcc_gc_frame_index_remove(odd_slot_key) != &replacement) {
                    return 7;
                }
                if (pcc_gc_frame_index_find(odd_slot_key) != 0) {
                    return 8;
                }
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_gc_open_addressed_indexes_preserve_probe_chains_after_delete(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "gc_open_addressed_index_probe.c"
    exe = tmp_path / "gc_open_addressed_index_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_internal.h"
            #include <stdint.h>

            static uint64_t hash_ptr(const void *p) {
                uint64_t v = (uint64_t)(uintptr_t)p >> 3;
                v ^= v >> 17;
                v ^= v >> 33;
                return v;
            }

            static void *key_for_bucket(uint64_t mask, uint64_t bucket, uint64_t *cursor) {
                for (;;) {
                    uintptr_t raw = (((uintptr_t)(*cursor)) << 4) + 0x10;
                    (*cursor)++;
                    void *key = (void *)raw;
                    if ((hash_ptr(key) & mask) == bucket) {
                        return key;
                    }
                }
            }

            int main(void) {
                void *frame_keys[8];
                uintptr_t frame_nodes[8];
                uint64_t cursor = 1;
                for (int i = 0; i < 8; i++) {
                    frame_keys[i] = key_for_bucket(255, 250, &cursor);
                    frame_nodes[i] = 0x1000u + (uintptr_t)i;
                    if (pcc_gc_frame_index_insert(frame_keys[i], &frame_nodes[i]) != 1) {
                        return 10 + i;
                    }
                }
                uintptr_t replacement = 0x9999u;
                if (pcc_gc_frame_index_insert(frame_keys[0], &replacement) != 0) {
                    return 30;
                }
                if (pcc_gc_frame_index_find(frame_keys[0]) != &frame_nodes[0]) {
                    return 31;
                }
                if (pcc_gc_frame_index_remove(frame_keys[0]) != &frame_nodes[0]) {
                    return 32;
                }
                if (pcc_gc_frame_index_remove(frame_keys[3]) != &frame_nodes[3]) {
                    return 33;
                }
                if (pcc_gc_frame_index_find(frame_keys[0]) != 0) {
                    return 34;
                }
                if (pcc_gc_frame_index_find(frame_keys[3]) != 0) {
                    return 35;
                }
                for (int i = 1; i < 8; i++) {
                    if (i == 3) continue;
                    if (pcc_gc_frame_index_find(frame_keys[i]) != &frame_nodes[i]) {
                        return 40 + i;
                    }
                }
                pcc_gc_frame_index_clear();

                PyObject *gc_keys[8];
                PyGcNode gc_nodes[8];
                cursor = 50000;
                for (int i = 0; i < 8; i++) {
                    gc_keys[i] = (PyObject *)key_for_bucket(255, 250, &cursor);
                    if (py_gc_index_insert(gc_keys[i], &gc_nodes[i]) != 1) {
                        return 50 + i;
                    }
                }
                if (py_gc_index_insert(gc_keys[0], &gc_nodes[1]) != 0) return 58;
                if (py_gc_index_remove(gc_keys[0]) != &gc_nodes[0]) return 59;
                if (py_gc_index_remove(gc_keys[3]) != &gc_nodes[3]) return 60;
                for (int i = 1; i < 8; i++) {
                    if (i == 3) continue;
                    if (py_gc_index_find(gc_keys[i]) != &gc_nodes[i]) {
                        return 61 + i;
                    }
                }

                PyObject *resize_keys[300];
                PyGcNode resize_nodes[300];
                for (int i = 0; i < 300; i++) {
                    resize_keys[i] = (PyObject *)(uintptr_t)(0x1000000u + (i * 16u));
                    if (py_gc_index_insert(resize_keys[i], &resize_nodes[i]) != 1) {
                        return 100;
                    }
                }
                for (int i = 0; i < 300; i += 3) {
                    if (py_gc_index_remove(resize_keys[i]) != &resize_nodes[i]) {
                        return 101;
                    }
                }
                for (int i = 0; i < 300; i++) {
                    PyGcNode *expected = (i % 3) == 0 ? 0 : &resize_nodes[i];
                    if (py_gc_index_find(resize_keys[i]) != expected) return 102;
                }

                PyObject *obj_keys[6];
                uintptr_t obj_nodes[6];
                cursor = 100000;
                for (int i = 0; i < 6; i++) {
                    obj_keys[i] = (PyObject *)key_for_bucket(16383, 16380, &cursor);
                    obj_nodes[i] = 0x2000u + (uintptr_t)i;
                    if (pcc_gc_object_index_insert(obj_keys[i], &obj_nodes[i]) != 1) {
                        return 60 + i;
                    }
                }
                if (pcc_gc_object_index_insert(obj_keys[1], &replacement) != 0) {
                    return 80;
                }
                if (pcc_gc_object_index_find(obj_keys[1]) != &obj_nodes[1]) {
                    return 81;
                }
                if (pcc_gc_object_index_remove(obj_keys[0]) != &obj_nodes[0]) {
                    return 82;
                }
                if (pcc_gc_object_index_remove(obj_keys[2]) != &obj_nodes[2]) {
                    return 83;
                }
                if (pcc_gc_object_index_find(obj_keys[0]) != 0) {
                    return 84;
                }
                if (pcc_gc_object_index_find(obj_keys[2]) != 0) {
                    return 85;
                }
                for (int i = 1; i < 6; i++) {
                    if (i == 2) continue;
                    if (pcc_gc_object_index_find(obj_keys[i]) != &obj_nodes[i]) {
                        return 90 + i;
                    }
                }
                pcc_gc_object_index_clear();
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_gc_frame_registry_hot_path_uses_frame_index_lookup():
    c_src = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    c_enter_leave = c_src.split("void pcc_gc_note_frame_enter", 1)[1].split(
        "void pcc_gc_thread_unregister_buffers", 1
    )[0]
    assert "pcc_gc_frame_index_insert" in c_enter_leave
    assert "pcc_gc_frame_index_replace" in c_enter_leave
    assert "pcc_gc_frame_index_remove" in c_enter_leave
    assert "pcc_gc_note_frame_enter_lifo" in c_enter_leave
    assert "pcc_gc_note_frame_leave_lifo" in c_enter_leave
    assert "PCC_GC_FRAME_NODE_FLAG_LIFO" in c_src
    assert "n->root_count = n_slots" in c_src
    assert "pcc_gc_frame_node_alloc_unlocked(n_slots)" in c_src
    assert "pcc_gc_frame_node_release_unlocked(indexed)" in c_enter_leave
    assert "pcc_gc_frame_node_release_unlocked(n)" in c_enter_leave
    assert "free(indexed->stable_values)" not in c_enter_leave
    assert "free(indexed)" not in c_enter_leave
    assert "sizeof(PccGcFrameNode) + stable_bytes" in c_src
    assert "PCC_GC_FRAME_NODE_POOL_MAX_ROOTS 16" in c_src
    assert "PCC_GC_FRAME_NODE_POOL_LIMIT 1024" in c_src
    c_promote = c_src.split("static void pcc_gc_promote_frame_roots", 1)[1].split(
        "static void pcc_gc_promote_scheduler_roots", 1
    )[0]
    assert "pcc_gc_root_slot_count_from_map" not in c_promote
    assert "f->root_count" in c_promote
    assert "c->root_count" in c_promote
    assert "pcc_gc_visit_mapped_root_slots_unlocked" in c_promote
    assert "pcc_gc_promote_mapped_root_slot" in c_promote
    assert "f->stable_values" in c_promote
    assert "c->stable_values" in c_promote

    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    py_enter_leave = py_src.split('@c_abi_export("pcc_gc_note_frame_enter")', 1)[
        1
    ].split('@c_abi_export("pcc_gc_thread_unregister_buffers")', 1)[0]
    assert "pcc_gc_frame_index_insert" in py_enter_leave
    assert "pcc_gc_frame_index_replace" in py_enter_leave
    assert "pcc_gc_frame_index_remove" in py_enter_leave
    assert '@c_abi_export("pcc_gc_note_frame_enter_lifo")' in py_enter_leave
    assert '@c_abi_export("pcc_gc_note_frame_leave_lifo")' in py_enter_leave
    assert "borrowed | 2" in py_enter_leave
    assert "store_i64(node, 40, n_slots)" in py_enter_leave
    assert "_frame_node_alloc(n_slots)" in py_enter_leave
    assert "_frame_node_release(indexed)" in py_enter_leave
    assert "_frame_node_release(node)" in py_enter_leave
    assert "node_size: int = _frame_node_size(root_count)" in py_enter_leave
    assert "return 64 + root_count * 8" in py_enter_leave
    assert "stable = ptr_add(node, 64)" in py_enter_leave
    assert "free(load_ptr(indexed, 56))" not in py_enter_leave
    assert "free(indexed)" not in py_enter_leave
    py_substrate = (RUNTIME_DIR / "py" / "py_substrate.py").read_text(
        encoding="utf-8"
    )
    assert 'define_global_ptr_null("pcc_gc_frame_node_pool_heads")' in py_substrate
    assert 'define_global_ptr_null("pcc_gc_frame_node_pool_counts")' in py_substrate
    py_promote = py_src.split("def _promote_frame_roots", 1)[1].split(
        "def _promote_tls_exception_root", 1
    )[0]
    py_root_slot = py_src.split("def _py_visit_mapped_root_slot", 1)[1].split(
        "def _py_visit_mapped_root_slots", 1
    )[0]
    assert "_mapped_root_count" not in py_promote
    assert "load_i64(frame, 40)" in py_promote
    assert "_py_visit_mapped_root_slots" in py_promote
    assert "_promote_cached_frame_slot" in py_root_slot
    assert "load_ptr(frame, 56)" in py_promote
    assert "load_i32(frame, 48) & 1" in py_promote


def test_pcc_python_frame_and_scheduler_roots_share_slot_walkers_source():
    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    helper_name = "_py_visit_mapped_root_slot"
    helper_start = py_src.index(f"def {helper_name}(")
    scheduler_helper_start = py_src.index(
        "def _py_visit_scheduler_root_slots(", helper_start
    )
    helper_body = py_src[helper_start:scheduler_helper_start]
    for token in (
        "_py_visit_mapped_root_slot(",
        "_promote_cached_frame_slot(",
        "_mark_root_gray_if_known(",
        "_resolve_root_slot_unlocked(",
        "if mode == 1:  # _PY_ROOT_VISIT_GRAY",
        "if mode == 2:  # _PY_ROOT_VISIT_PROMOTE",
        "if mode == 3:  # _PY_ROOT_VISIT_REWRITE",
    ):
        assert token in helper_body

    scheduler_helper_end = py_src.index("def _gray_current_roots(", scheduler_helper_start)
    scheduler_helper_body = py_src[scheduler_helper_start:scheduler_helper_end]
    assert 'global_load_ptr("pcc_gc_scheduler_root_head")' in scheduler_helper_body
    assert "_py_visit_mapped_root_slot(" in scheduler_helper_body

    gray_body = py_src.split("def _gray_current_roots()", 1)[1].split(
        "def _gray_refcount_external_roots", 1
    )[0]
    assert "_py_visit_mapped_root_slots(" in gray_body
    assert "_py_visit_scheduler_root_slots(1, 1)" in gray_body
    assert "_gray_mapped_roots(load_ptr(frame, 0), load_ptr(frame, 8), 1)" not in gray_body
    assert "_resolve_root_slot_unlocked(slot, 0)" not in gray_body

    promote_body = py_src.split("def _promote_frame_roots", 1)[1].split(
        "def _promote_tls_exception_root", 1
    )[0]
    assert "_py_visit_mapped_root_slots(" in promote_body
    assert "_py_visit_scheduler_root_slots(2, 0)" in promote_body
    assert "_promote_cached_frame_slot(" not in promote_body
    assert "_promote_young_slot(slot, 0)" not in promote_body

    remap_body = py_src.split("def _backend4_remap_and_retire", 1)[1].split(
        "# Retire ONE EPOCH LATE", 1
    )[0]
    assert "_py_visit_mapped_root_slots(" in remap_body
    assert "_py_visit_scheduler_root_slots(3, 0)" in remap_body
    assert "_rewrite_mapped_roots(load_ptr(frame, 0), load_ptr(frame, 8))" not in remap_body
    assert "_resolve_root_slot_unlocked(slot, 0)" not in remap_body


def test_gc_lifo_frame_roots_skip_frame_index_but_remain_roots(tmp_path):
    work_runtime = _build_runtime(tmp_path)
    src = tmp_path / "gc_lifo_frame_root_probe.c"
    exe = tmp_path / "gc_lifo_frame_root_probe.out"
    src.write_text(
        textwrap.dedent(
            r"""
            #include "py_runtime.h"
            #include "py_internal.h"

            int main(void) {
                int32_t frame_map[1] = {1};
                PyObject *slots[1] = {0};
                pcc_gc_set_backend(3);

                pcc_gc_frame_enter_lifo(frame_map, slots);
                if (pcc_gc_frame_root_slot_count() != 1) return 10;
                if (pcc_gc_frame_index_find(slots) != 0) return 11;
                pcc_gc_frame_leave_lifo(slots);
                if (pcc_gc_frame_root_slot_count() != 0) return 12;

                pcc_gc_frame_enter(frame_map, slots);
                if (pcc_gc_frame_index_find(slots) == 0) return 13;
                pcc_gc_frame_leave_lifo(slots);
                if (pcc_gc_frame_root_slot_count() != 1) return 14;
                pcc_gc_frame_leave(slots);
                if (pcc_gc_frame_root_slot_count() != 0) return 15;
                return 0;
            }
            """
        )
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_generational_minor_heap_default_is_bootstrap_sized():
    c_src = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    assert "static int64_t pcc_gc_minor_heap_size = 33554432;" in c_src
    assert "static int64_t pcc_gc_minor_alloc_max = 16;" in c_src
    assert '"PCC_GC_MINOR_HEAP_SIZE",\n        33554432,' in c_src
    assert '"PCC_GC_MINOR_ALLOC_MAX",\n        16,' in c_src

    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    assert 'getenv(cstr("PCC_GC_MINOR_HEAP_SIZE")), 33554432' in py_src
    assert 'getenv(cstr("PCC_GC_MINOR_ALLOC_MAX")), 16' in py_src


def test_gc_relocation_read_non_candidate_fast_path_skips_graph_lock():
    c_src = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    c_note = c_src.split("PyObject *pcc_gc_note_relocation_read", 1)[1].split(
        "void pcc_gc_note_store", 1
    )[0]
    assert "pcc_gc_is_known_object(o)" in c_note
    assert "PY_FLAG_GC_RELOCATION_CANDIDATE" in c_note
    assert c_note.index("PY_FLAG_GC_RELOCATION_CANDIDATE") < c_note.index(
        "pcc_gc_graph_lock()"
    )

    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    py_note = py_src.split('@c_abi_export("pcc_gc_note_relocation_read")', 1)[1].split(
        '@c_abi_export("pcc_gc_note_store")', 1
    )[0]
    assert "_is_known_object(o)" in py_note
    assert "flags & 2048" in py_note
    assert py_note.index("flags & 2048") < py_note.index("_object_graph_lock()")


def test_gc_slot_barriers_fast_path_non_relocation_and_non_old_to_young():
    c_obj = (RUNTIME_DIR / "src" / "py_obj.c").read_text(encoding="utf-8")
    assert "static int py_gc_relocation_candidate" in c_obj
    c_load = c_obj.split("PyObject *pcc_gc_load_ptr", 1)[1].split(
        "PyObject *pcc_gc_load_borrowed_ptr", 1
    )[0]
    assert "py_gc_relocation_candidate(value)" in c_load
    assert "pcc_gc_forwarding_population_load() <= 0" in c_load
    assert "!py_gc_backend4_should_check_slot(slot)" in c_load
    assert c_load.index("pcc_gc_forwarding_population_load() <= 0") < c_load.index(
        "pcc_gc_note_load()"
    )
    assert c_load.index("!py_gc_backend4_should_check_slot(slot)") < c_load.index(
        "pcc_gc_note_load()"
    )
    assert c_load.index("py_gc_relocation_candidate(value)") < c_load.index(
        "pcc_gc_note_relocation_read(value)"
    )

    c_gc = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    c_barrier = c_gc.split("void pcc_gc_note_slot_write_barrier", 1)[1].split(
        "void pcc_gc_note_write_barrier", 1
    )[0]
    c_gen_barrier = c_barrier.split(
        "barrier_backend == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR", 1
    )[1]
    assert "(py_header_flags_load(owner_h) & PY_FLAG_GC_OLD) == 0" in c_gen_barrier
    assert "(py_header_flags_load(value_h) & PY_FLAG_GC_YOUNG) == 0" in c_gen_barrier
    assert c_gen_barrier.index("PY_FLAG_GC_OLD) == 0") < c_gen_barrier.index(
        "pcc_gc_graph_lock()"
    )

    py_obj = (RUNTIME_DIR / "py" / "py_obj.py").read_text(encoding="utf-8")
    assert "def _gc_relocation_candidate" in py_obj
    py_load = py_obj.split('@c_abi_export("pcc_gc_load_ptr")', 1)[1].split(
        '@c_abi_export("pcc_gc_load_borrowed_ptr")', 1
    )[0]
    assert "_gc_relocation_candidate(v)" in py_load
    assert "_gc_forwarding_population() <= 0" in py_load
    assert "_gc_backend4_should_check_slot(slot) == 0" in py_load
    assert py_load.index("_gc_forwarding_population() <= 0") < py_load.index(
        "pcc_gc_note_load()"
    )
    assert py_load.index("_gc_backend4_should_check_slot(slot) == 0") < py_load.index(
        "pcc_gc_note_load()"
    )
    assert py_load.index("_gc_relocation_candidate(v)") < py_load.index(
        "pcc_gc_note_relocation_read(v)"
    )

    py_gc = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    py_barrier = py_gc.split('@c_abi_export("pcc_gc_note_slot_write_barrier")', 1)[
        1
    ].split('@c_abi_export("pcc_gc_note_write_barrier")', 1)[0]
    assert "if (owner_flags & 256) == 0 or (value_flags & 128) == 0" in py_barrier
    assert py_barrier.index("value_flags & 128") < py_barrier.index(
        "_object_graph_lock()"
    )


def test_gc_indexes_use_open_addressed_slots_and_tombstone_delete():
    src = (RUNTIME_DIR / "src" / "py_gc_index_table.c").read_text(encoding="utf-8")
    assert "typedef struct PccGcIndexSlot" in src
    assert "pcc_gc_index_find_slot" in src
    assert "PCC_GC_INDEX_SLOT_DELETED" in src
    assert "first_deleted" in src
    assert "state = PCC_GC_INDEX_SLOT_DELETED" in src
    assert "pcc_gc_index_delete_slot" not in src
    assert "hole_dist < scan_dist" not in src
    assert "#define PCC_GC_INDEX_DEFAULT_INIT_CAP 256" in src
    py_init = src.split("static int py_gc_index_init", 1)[1].split(
        "PyGcNode *py_gc_index_find", 1
    )[0]
    assert "py_gc_index_rehash(PCC_GC_INDEX_DEFAULT_INIT_CAP)" in py_init
    assert "int64_t used" in src
    assert "py_gc_index_used + 1 > py_gc_index_cap / 2" in src
    assert "index->used + 1 > index->cap / 2" in src
    assert "pcc_gc_object_index_rehash(16384)" in src
    assert "PccGcObjectIndexEntry" not in src
    assert "PccGcPtrIndexEntry" not in src
    insert = src.split("int64_t pcc_gc_object_index_insert", 1)[1].split(
        "void *pcc_gc_object_index_remove", 1
    )[0]
    assert "pcc_gc_object_index_find(obj)" not in insert
    assert "pcc_gc_index_find_slot" in insert


def test_default_gc_implementations_share_object_node_index_source_of_truth():
    c_gc = (RUNTIME_DIR / "src" / "py_obj_gc.c").read_text(encoding="utf-8")
    py_gc = (RUNTIME_DIR / "py" / "py_obj_gc.py").read_text(encoding="utf-8")

    for source in (c_gc, py_gc):
        assert "py_gc_index_find" in source
        assert "py_gc_index_insert" in source
        assert "py_gc_index_remove" in source

    for duplicate in (
        "PyGcNodeSlot",
        "py_gc_node_index",
        "py_gc_node_hash",
        "py_gc_node_index_rehash",
    ):
        assert duplicate not in c_gc

    c_find = c_gc.split("static PyGcNode *py_gc_find_node", 1)[1].split(
        "static void py_gc_unlink_node", 1
    )[0]
    assert "return py_gc_index_find(o);" in c_find
    assert 'extern("py_gc_index_find"' in py_gc


def test_generational_minor_refill_skips_global_young_scan():
    c_src = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    assert "(void)pcc_gc_step_generational_promotion(1024, 0);" in c_src
    assert "processed += pcc_gc_step_generational_promotion(budget, 1);" in c_src
    c_step = c_src.split("static int64_t pcc_gc_step_generational_promotion", 1)[
        1
    ].split("static int64_t pcc_gc_step_colored_remembered_roots", 1)[0]
    assert "int promote_all_young" in c_step
    assert "if (promote_all_young)" in c_step
    assert "pcc_gc_backend3_drain_remembered_owners" in c_step
    assert "pcc_gc_backend3_remember_owner_unlocked(owner, owner_h)" in c_src

    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    assert "_step_generational_promotion(1024, 0)" in py_src
    assert "_step_generational_promotion(budget, 1)" in py_src
    py_step = py_src.split("def _step_generational_promotion", 1)[1].split(
        "def _step_colored_remembered_roots", 1
    )[0]
    assert "promote_all_young: int" in py_step
    assert "if promote_all_young != 0:" in py_step
    assert "_backend3_drain_remembered_owners" in py_step
    assert "_backend3_remember_owner(owner, owner_flags)" in py_src

    substrate_src = (RUNTIME_DIR / "py" / "py_substrate.py").read_text(
        encoding="utf-8"
    )
    assert 'define_global_ptr_null("pcc_gc_backend3_remembered_owner_head")' in substrate_src


def test_c_runtime_core_container_promotion_reuses_owner_slot_walker_source():
    c_src = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    helper_name = "pcc_gc_visit_core_container_owner_slots"
    helper_start = c_src.index(f"static int {helper_name}(")
    visit_start = c_src.index("int py_obj_visit_slots(", helper_start)
    visit_end = c_src.index(
        "typedef struct {\n    void (*visit)(PyObject *child);",
        visit_start,
    )
    visit_body = c_src[visit_start:visit_end]
    promote_start = c_src.index(
        "static void pcc_gc_promote_owner_referents(",
        visit_end,
    )
    promote_end = c_src.index(
        "static void pcc_gc_promote_remembered_owner_referents(",
        promote_start,
    )
    promote_body = c_src[promote_start:promote_end]
    assert helper_name in visit_body
    assert "py_obj_visit_slots(" in promote_body
    assert "pcc_gc_promote_owner_slot" in promote_body
    assert "&promote_ctx" in promote_body

    for token in (
        "PY_TYPE_LIST",
        "PY_TYPE_TUPLE",
        "PY_TYPE_DICT",
        "PY_TYPE_SET",
        "PY_TYPE_CONTINUATION",
        "PY_TYPE_CLASS",
        "PY_TYPE_INSTANCE",
    ):
        assert token not in promote_body


def _assert_pcc_python_covered_slot_dispatch(py_src: str) -> None:
    covered_body = py_src.split("def _py_obj_visit_covered_slots(", 1)[1].split(
        "def _trace_referents(",
        1,
    )[0]
    expected_order = (
        "_py_obj_visit_core_container_owner_slots(o, mode, recurse)",
        "_py_obj_visit_fixed_owner_slots(o, mode, recurse)",
        "_py_obj_visit_weakref_slots(o, mode, recurse)",
        "_py_obj_visit_continuation_owner_slots(o, mode, recurse)",
        "_py_obj_visit_class_slots(o, mode, recurse)",
        "_py_obj_visit_cext_object_slots(o, mode, recurse)",
        "_py_obj_visit_instance_owner_slots(o, mode, recurse)",
    )
    cursor = -1
    for token in expected_order:
        next_cursor = covered_body.index(token)
        assert next_cursor > cursor
        cursor = next_cursor
    assert "if handled != 0:" in covered_body


def test_pcc_python_core_container_promotion_reuses_owner_slot_walker_source():
    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    helper_name = "_py_obj_visit_core_container_owner_slots"
    helper_start = py_src.index(f"def {helper_name}(")
    helper_end = py_src.index("def _py_obj_visit_class_slots(", helper_start)
    helper_body = py_src[helper_start:helper_end]
    for token in (
        "tag == 5:  # PY_TYPE_LIST",
        "tag == 7:  # PY_TYPE_TUPLE",
        "tag == 6:  # PY_TYPE_DICT",
        "tag == 8:  # PY_TYPE_SET",
        "_py_obj_visit_slot(",
        "global_load_ptr(\"py_set_dummy\")",
    ):
        assert token in helper_body

    trace_py = py_src.split("def _trace_referents(o)", 1)[1].split(
        "def _subtract_known_child_ref", 1
    )[0]
    promote_py = py_src.split("def _trace_referents_for_promotion_mode", 1)[
        1
    ].split("def _trace_referents_for_promotion", 1)[0]
    remap_py = py_src.split("def _remap_referents(o)", 1)[1].split(
        "def _backend4_remap_and_retire", 1
    )[0]
    _assert_pcc_python_covered_slot_dispatch(py_src)
    assert (
        "_py_obj_visit_covered_slots(o, 1, 0) != 0:  # _PY_OBJ_VISIT_TRACE"
        in trace_py
    )
    assert (
        "_py_obj_visit_covered_slots(o, 2, recurse) != 0:  # _PY_OBJ_VISIT_PROMOTE"
        in promote_py
    )
    assert (
        "_py_obj_visit_covered_slots(o, 3, 0) != 0:  # _PY_OBJ_VISIT_UPDATE"
        in remap_py
    )
    for token in (
        "tag == 5:  # PY_TYPE_LIST",
        "tag == 7:  # PY_TYPE_TUPLE",
        "tag == 6:  # PY_TYPE_DICT",
        "tag == 8:  # PY_TYPE_SET",
    ):
        assert token not in promote_py


def test_pcc_python_fixed_owner_promotion_reuses_owner_slot_walker_source():
    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    helper_name = "_py_obj_visit_fixed_owner_slots"
    helper_start = py_src.index(f"def {helper_name}(")
    helper_end = py_src.index("def _py_obj_visit_weakref_slots(", helper_start)
    helper_body = py_src[helper_start:helper_end]
    for token in (
        "tag == 9:  # PY_TYPE_FUNC",
        "tag == 14:  # PY_TYPE_ITER",
        "tag == 15:  # PY_TYPE_GEN",
        "tag == 20:  # PY_TYPE_COROUTINE",
        "tag == 28:  # PY_TYPE_TASK",
        "tag == 30:  # PY_TYPE_VIRTUAL_THREAD",
        "tag == 12:  # PY_TYPE_EXC",
        "tag == 101:  # PY_TYPE_PROPERTY",
        "tag == 102:  # PY_TYPE_CLASSMETHOD",
        "tag == 103:  # PY_TYPE_STATICMETHOD",
        "tag == 19:  # PY_TYPE_MEMORYVIEW",
        "tag == 27:  # PY_TYPE_THREAD",
        "_py_obj_visit_slot(",
    ):
        assert token in helper_body
    for excluded in (
        "tag == 10:  # PY_TYPE_CLASS",
        "tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags",
        "tag == 29:  # PY_TYPE_CONTINUATION",
        "tag == 21:  # PY_TYPE_WEAKREF",
    ):
        assert excluded not in helper_body
    weak_helper = py_src.split("def _py_obj_visit_weakref_slots(", 1)[1].split(
        "\ndef ",
        1,
    )[0]
    assert "tag != 21:  # PY_TYPE_WEAKREF" in weak_helper
    assert "_PY_OBJ_SLOT_BORROWED_UPDATE_ONLY" in weak_helper
    assert "_PY_OBJ_SLOT_OWNED" in weak_helper

    trace_py = py_src.split("def _trace_referents(o)", 1)[1].split(
        "def _subtract_known_child_ref", 1
    )[0]
    promote_py = py_src.split("def _trace_referents_for_promotion_mode", 1)[
        1
    ].split("def _trace_referents_for_promotion", 1)[0]
    remap_py = py_src.split("def _remap_referents(o)", 1)[1].split(
        "def _backend4_remap_and_retire", 1
    )[0]
    _assert_pcc_python_covered_slot_dispatch(py_src)
    assert (
        "_py_obj_visit_covered_slots(o, 1, 0) != 0:  # _PY_OBJ_VISIT_TRACE"
        in trace_py
    )
    assert (
        "_py_obj_visit_covered_slots(o, 2, recurse) != 0:  # _PY_OBJ_VISIT_PROMOTE"
        in promote_py
    )
    assert (
        "_py_obj_visit_covered_slots(o, 3, 0) != 0:  # _PY_OBJ_VISIT_UPDATE"
        in remap_py
    )
    for token in (
        "tag == 9:  # PY_TYPE_FUNC",
        "tag == 14:  # PY_TYPE_ITER",
        "tag == 15:  # PY_TYPE_GEN",
        "tag == 20:  # PY_TYPE_COROUTINE",
        "tag == 28:  # PY_TYPE_TASK",
        "tag == 30:  # PY_TYPE_VIRTUAL_THREAD",
        "tag == 12:  # PY_TYPE_EXC",
        "tag == 101:  # PY_TYPE_PROPERTY",
        "tag == 102:  # PY_TYPE_CLASSMETHOD",
        "tag == 103:  # PY_TYPE_STATICMETHOD",
        "tag == 19:  # PY_TYPE_MEMORYVIEW",
        "tag == 27:  # PY_TYPE_THREAD",
    ):
        assert token not in promote_py


def test_pcc_python_continuation_promotion_reuses_owner_slot_walker_source():
    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    helper_name = "_py_obj_visit_continuation_owner_slots"
    helper_start = py_src.index(f"def {helper_name}(")
    helper_end = py_src.index("def _py_obj_visit_class_slots(", helper_start)
    helper_body = py_src[helper_start:helper_end]
    for token in (
        "tag != 29:  # PY_TYPE_CONTINUATION",
        "load_ptr(o, 24)",
        "load_ptr(chunk, 16)",
        "load_i64(chunk, 8)",
        "_py_obj_visit_slot(slots, i * 8, 1, mode, recurse)",
    ):
        assert token in helper_body
    for excluded in (
        "tag == 10:  # PY_TYPE_CLASS",
        "tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags",
        "tag == 9:  # PY_TYPE_FUNC",
    ):
        assert excluded not in helper_body

    trace_py = py_src.split("def _trace_referents(o)", 1)[1].split(
        "def _subtract_known_child_ref", 1
    )[0]
    promote_py = py_src.split("def _trace_referents_for_promotion_mode", 1)[
        1
    ].split("def _trace_referents_for_promotion", 1)[0]
    remap_py = py_src.split("def _remap_referents(o)", 1)[1].split(
        "def _backend4_remap_and_retire", 1
    )[0]
    _assert_pcc_python_covered_slot_dispatch(py_src)
    assert (
        "_py_obj_visit_covered_slots(o, 1, 0) != 0:  # _PY_OBJ_VISIT_TRACE"
        in trace_py
    )
    assert (
        "_py_obj_visit_covered_slots(o, 2, recurse) != 0:  # _PY_OBJ_VISIT_PROMOTE"
        in promote_py
    )
    assert (
        "_py_obj_visit_covered_slots(o, 3, 0) != 0:  # _PY_OBJ_VISIT_UPDATE"
        in remap_py
    )
    for body in (trace_py, promote_py, remap_py):
        assert "tag == 29:  # PY_TYPE_CONTINUATION" not in body


def test_pcc_python_instance_owner_promotion_reuses_owner_slot_walker_source():
    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    helper_name = "_py_obj_visit_instance_owner_slots"
    helper_start = py_src.index(f"def {helper_name}(")
    helper_end = py_src.index("def _trace_referents(", helper_start)
    helper_body = py_src[helper_start:helper_end]
    for token in (
        "tag != 11 and tag != 200 and tag < 104",
        "load_ptr(o, 16)",
        "_py_obj_visit_slot(o, 16, 2, mode, recurse)",
        "load_i32(cls, 72)",
        "if n_fields < 0:",
        "_py_obj_visit_slot(o, 24 + i * 8, 1, mode, recurse)",
        "load_i32(cls, 12)",
        "_py_obj_visit_slot(o, 24 + n_fields * 8, 1, mode, recurse)",
    ):
        assert token in helper_body
    for excluded in (
        "tag == 10:  # PY_TYPE_CLASS",
        "tag == 29:  # PY_TYPE_CONTINUATION",
        "tag == 9:  # PY_TYPE_FUNC",
    ):
        assert excluded not in helper_body

    trace_py = py_src.split("def _trace_referents(o)", 1)[1].split(
        "def _subtract_known_child_ref", 1
    )[0]
    promote_py = py_src.split("def _trace_referents_for_promotion_mode", 1)[
        1
    ].split("def _trace_referents_for_promotion", 1)[0]
    remap_py = py_src.split("def _remap_referents(o)", 1)[1].split(
        "def _backend4_remap_and_retire", 1
    )[0]
    _assert_pcc_python_covered_slot_dispatch(py_src)
    assert (
        "_py_obj_visit_covered_slots(o, 1, 0) != 0:  # _PY_OBJ_VISIT_TRACE"
        in trace_py
    )
    assert (
        "_py_obj_visit_covered_slots(o, 2, recurse) != 0:  # _PY_OBJ_VISIT_PROMOTE"
        in promote_py
    )
    assert (
        "_py_obj_visit_covered_slots(o, 3, 0) != 0:  # _PY_OBJ_VISIT_UPDATE"
        in remap_py
    )
    for body in (trace_py, promote_py, remap_py):
        assert "tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags" not in body


def test_pcc_python_subtract_referents_reuses_slot_walkers_source():
    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")
    slot_adapter = py_src.split("def _py_obj_visit_slot(", 1)[1].split(
        "def _py_obj_visit_core_container_owner_slots", 1
    )[0]
    assert "if mode == 4:  # _PY_OBJ_VISIT_SUBTRACT" in slot_adapter
    assert "role != 3:  # _PY_OBJ_SLOT_BORROWED_UPDATE_ONLY" in slot_adapter
    assert "child = pcc_gc_load_ptr_extern(" in slot_adapter
    assert "ptr_add(slot_base, slot_offset)" in slot_adapter
    assert "_subtract_known_child_ref(child)" in slot_adapter
    assert "_subtract_known_child_ref(load_ptr(slot_base, slot_offset))" not in slot_adapter

    subtract_py = py_src.split("def _subtract_referent_refs(o)", 1)[1].split(
        "def _trace_referents_for_promotion_mode", 1
    )[0]
    _assert_pcc_python_covered_slot_dispatch(py_src)
    assert (
        "_py_obj_visit_covered_slots(o, 4, 0) != 0:  # _PY_OBJ_VISIT_SUBTRACT"
        in subtract_py
    )
    for token in (
        "tag: int = load_i32(o, 8)",
        "tag == 5:  # PY_TYPE_LIST",
        "tag == 10:  # PY_TYPE_CLASS",
        "tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags",
    ):
        assert token not in subtract_py


def test_clear_referents_reuses_slot_contract_source():
    c_src = (RUNTIME_DIR / "src" / "py_gc_backend.c").read_text(encoding="utf-8")
    py_src = (RUNTIME_DIR / "py" / "py_gc_backend.py").read_text(encoding="utf-8")

    c_clear_slot_start = c_src.index("static void pcc_gc_clear_owned_slot(")
    c_clear_slot_body = c_src[
        c_clear_slot_start:c_src.index("static void pcc_gc_clear_referents(", c_clear_slot_start)
    ]
    assert "role != PY_OBJ_SLOT_OWNED" in c_clear_slot_body
    assert "pcc_gc_clear_slot(slot)" in c_clear_slot_body

    c_clear_start = c_src.index("static void pcc_gc_clear_referents(")
    c_clear_body = c_src[
        c_clear_start:c_src.index("/* PASS-1 of the two-phase sweep", c_clear_start)
    ]
    assert "py_obj_visit_slots(o, pcc_gc_clear_owned_slot, NULL)" in c_clear_body
    assert "pcc_gc_clear_slot(&" not in c_clear_body
    assert "pcc_gc_clear_slot((" not in c_clear_body
    assert "pcc_gc_visit_class_slots(" not in c_clear_body

    py_slot_adapter = py_src.split("def _py_obj_visit_slot(", 1)[1].split(
        "def _py_obj_visit_core_container_owner_slots", 1
    )[0]
    assert "if mode == 5:  # _PY_OBJ_VISIT_CLEAR" in py_slot_adapter
    assert "role == 1:  # _PY_OBJ_SLOT_OWNED" in py_slot_adapter
    assert "_clear_slot(slot_base, slot_offset)" in py_slot_adapter

    py_clear = py_src.split("def _clear_referents(o)", 1)[1].split(
        "def _clear_unreachable(o)", 1
    )[0]
    _assert_pcc_python_covered_slot_dispatch(py_src)
    assert "_py_obj_visit_covered_slots(o, 5, 0)  # _PY_OBJ_VISIT_CLEAR" in py_clear
    for token in (
        "_clear_slot(o, 16)",
        "_clear_slot(o, 24)",
        "_clear_slot(o, 40)",
        "elif tag == 10:  # PY_TYPE_CLASS",
        "elif tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags",
    ):
        assert token not in py_clear


def _assert_backend_three_minor_refill_promotes_tls_exception_root(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_tls_exc_root_probe.c"
    exe = tmp_path / f"{archive_name}_minor_tls_exc_root_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdio.h>

            static int force_refill(PyObject **held, int n) {
                for (int i = 0; i < n; i++) {
                    held[i] = py_str_new("x", 1);
                    if (held[i] == 0) return 0;
                }
                return 1;
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *exc = py_exc_new(PY_EXC_RUNTIMEERROR, "tls");
                if (exc == 0) return 3;
                py_tls_exc_set(exc);

                PyObject *held[64] = {0};
                if (!force_refill(held, 64)) return 4;

                PyObject *cur = (PyObject *)py_tls_exc_get();
                if (cur == 0) return 5;
                printf("%d\\n", (py_header(cur)->flags & PY_FLAG_GC_OLD) != 0);
                printf("%d\\n", (py_header(cur)->flags & PY_FLAG_GC_YOUNG) != 0);
                py_incref(cur);
                py_decref(cur);

                py_tls_exc_set(0);
                py_decref(cur);
                for (int i = 0; i < 64; i++) {
                    if (held[i] != 0) py_decref(held[i]);
                }
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    link_cmd = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link_cmd.extend(extra_link_args)
    link_cmd.extend(["-o", str(exe)])
    build = subprocess.run(
        link_cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "0"]


def _assert_backend_three_non_list_slots_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_non_list_slot_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_non_list_slot_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>
            #include <string.h>

            static void force_refill(void) {
                for (int i = 0; i < 4; i++) {
                    PyObject *filler = py_str_new("filler", 6);
                    if (filler == 0) exit(20 + i);
                    pcc_gc_release(filler);
                }
            }

            static int forwarded_slot_matches(PyObject *child, PyObject *slot) {
                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                return forwarded != 0
                    && forwarded != child
                    && slot == forwarded
                    && slot != child
                    && ((((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0);
            }

            static int check_tuple_slot(void) {
                PyObject *owner = py_tuple_new(1);
                if (owner == 0) return 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("tuple-child", 11);
                if (child == 0) return 0;
                py_tuple_set_item(owner, 0, child);
                force_refill();

                return forwarded_slot_matches(
                    child, ((PyTupleObject *)owner)->items[0]
                );
            }

            static int check_dict_value_slot(void) {
                PyObject *owner = py_dict_new();
                if (owner == 0) return 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("dict-child", 10);
                if (child == 0) return 0;
                py_dict_set(owner, py_tag_int(7), child);
                force_refill();

                PyDictObject *d = (PyDictObject *)owner;
                PyObject *slot = 0;
                for (int64_t i = 0; i < d->entries_used; i++) {
                    if (d->entries[i].key != 0) {
                        slot = d->entries[i].value;
                        break;
                    }
                }
                return forwarded_slot_matches(child, slot);
            }

            static int check_set_key_slot(void) {
                PyObject *owner = py_set_new();
                if (owner == 0) return 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("set-child", 9);
                if (child == 0) return 0;
                py_set_add(owner, child);
                force_refill();

                PySetObject *s = (PySetObject *)owner;
                PyObject *slot = 0;
                for (int64_t i = 0; i < s->capacity; i++) {
                    PyObject *key = s->entries[i].key;
                    if (key != 0 && key != py_set_dummy) {
                        slot = key;
                        break;
                    }
                }
                return forwarded_slot_matches(child, slot);
            }

            static int check_instance_field_slot(void) {
                PyClassObject *cls = (PyClassObject *)pcc_gc_alloc(
                    sizeof(PyClassObject), PY_TYPE_CLASS, 0
                );
                if (cls == 0) return 0;
                memset((char *)cls + sizeof(PyObjectHeader), 0,
                       sizeof(PyClassObject) - sizeof(PyObjectHeader));
                cls->n_fields = 1;

                PyInstanceObject *owner = (PyInstanceObject *)pcc_gc_alloc(
                    sizeof(PyInstanceObject) + 2 * sizeof(PyObject *),
                    PY_TYPE_INSTANCE,
                    0
                );
                if (owner == 0) return 0;
                owner->cls = cls;
                owner->fields[0] = 0;
                owner->fields[1] = 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("field-child", 11);
                if (child == 0) return 0;
                pcc_gc_store_ptr((PyObject *)owner, &owner->fields[0], child);
                force_refill();

                return forwarded_slot_matches(child, owner->fields[0]);
            }

            static int check_valuebox_field_slot(void) {
                PyClassObject *cls = (PyClassObject *)pcc_gc_alloc(
                    sizeof(PyClassObject), PY_TYPE_CLASS, 0
                );
                if (cls == 0) return 0;
                memset((char *)cls + sizeof(PyObjectHeader), 0,
                       sizeof(PyClassObject) - sizeof(PyObjectHeader));
                cls->n_fields = 1;

                PyValueBoxObject *owner = (PyValueBoxObject *)pcc_gc_alloc(
                    sizeof(PyValueBoxObject) + 2 * sizeof(PyObject *),
                    PY_TYPE_VALUEBOX,
                    0
                );
                if (owner == 0) return 0;
                owner->cls = cls;
                owner->fields[0] = 0;
                owner->fields[1] = 0;
                (void)pcc_gc_step(1);

                PyObject *child = py_str_new("valuebox-child", 14);
                if (child == 0) return 0;
                pcc_gc_store_ptr((PyObject *)owner, &owner->fields[0], child);
                force_refill();

                return forwarded_slot_matches(child, owner->fields[0]);
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                printf("%d\\n", check_tuple_slot());
                printf("%d\\n", check_dict_value_slot());
                printf("%d\\n", check_set_key_slot());
                printf("%d\\n", check_instance_field_slot());
                printf("%d\\n", check_valuebox_field_slot());
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1"]


def _assert_backend_three_frame_root_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_frame_root_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_frame_root_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                int32_t frame_map[1] = {1};
                PyObject *slots[1] = {0};
                PyObject *child = py_str_new("root-child", 10);
                if (child == 0) return 3;
                pcc_gc_store_root(&slots[0], child);
                pcc_gc_frame_enter(frame_map, slots);
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", slots[0] == forwarded ? 1 : 0);
                printf("%d\\n", slots[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_frame_leave(slots);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def _assert_backend_three_borrowed_frame_root_rewrite_preserves_source_ref(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_borrowed_frame_root_probe.c"
    exe = tmp_path / f"{archive_name}_minor_borrowed_frame_root_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                static const int32_t borrowed_frame_map[1] = {-1};
                PyObject *slots[1] = {0};

                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *child = py_str_new("borrowed-root", 13);
                if (child == 0) return 3;
                slots[0] = child;
                pcc_gc_frame_enter(borrowed_frame_map, slots);

                (void)pcc_gc_step(1024);

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", slots[0] == forwarded ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)child)->refcount == 1 ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_frame_leave(slots);
                pcc_gc_release(child);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def _assert_backend_three_suspended_generator_frame_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_gen_frame_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_gen_frame_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static PyObject *dummy_resume(PyObject *gen, PyObject *frame) {
                (void)gen;
                (void)frame;
                py_incref(py_None);
                return py_None;
            }

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *frame = py_list_new(1);
                if (frame == 0) return 3;
                py_list_append(frame, py_None);

                PyObject *gen = py_gen_new((void *)dummy_resume, frame);
                if (gen == 0) return 4;

                PyListObject *frame_list = (PyListObject *)frame;
                PyGenObject *gen_obj = (PyGenObject *)gen;
                pcc_gc_release(frame);

                (void)pcc_gc_step(8);
                printf("%d\\n", gen_obj->frame == (PyObject *)frame_list ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)frame_list)->flags & PY_FLAG_GC_OLD ? 1 : 0);

                PyObject *child = py_str_new("suspended-child", 15);
                if (child == 0) return 5;
                py_list_set((PyObject *)frame_list, 0, child);
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", frame_list->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", frame_list->items[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                py_list_set((PyObject *)frame_list, 0, py_None);
                pcc_gc_release(child);
                pcc_gc_release(gen);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1", "0"]


def _assert_backend_three_continuation_stack_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_continuation_slot_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_continuation_slot_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void resume_marker(void) {}

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                int32_t frame_map[1] = {1};
                PyObject *initial = py_str_new("initial-cont-child", 18);
                if (initial == 0) return 3;
                PyObject *slots[1] = {initial};
                PyObject *cont = py_continuation_new(frame_map, slots, (void *)&resume_marker);
                if (cont == 0) return 4;
                if (py_continuation_mount(cont, 0) != 0) return 5;

                PyContinuationObject *cont_obj = (PyContinuationObject *)cont;
                PyContinuationStackChunk *chunk = cont_obj->stack_chunk;
                if (chunk == 0 || chunk->slots == 0 || chunk->slot_count != 1) return 6;
                pcc_gc_release(initial);

                (void)pcc_gc_step(8);
                printf("%d\\n", ((PyObjectHeader *)cont)->flags & PY_FLAG_GC_OLD ? 1 : 0);

                PyObject *child = py_str_new("continuation-child", 18);
                if (child == 0) return 7;
                if (py_continuation_set_slot(cont, 0, child) != 0) return 8;
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", chunk->slots[0] == forwarded ? 1 : 0);
                printf("%d\\n", chunk->slots[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                py_continuation_set_slot(cont, 0, py_None);
                pcc_gc_release(child);
                pcc_gc_release(cont);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "0"]


def _assert_backend_three_generator_coroutine_state_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_gen_coro_state_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_gen_coro_state_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                const char *name;
                PyNativeFuncEntry entry;
                PyObject *captures;
                PyObject *args;
                PyObject *result;
                int32_t closed;
                int32_t done;
            } ProbeCoroutineObject;

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            static PyObject *dummy_gen_resume(PyObject *gen, PyObject *frame) {
                (void)gen;
                (void)frame;
                py_incref(py_None);
                return py_None;
            }

            static PyObject *coro_entry(PyObject *captures, PyObject *args) {
                (void)captures;
                (void)args;
                return py_str_new("coro-result", 11);
            }

            static int forwarded_slot_matches(PyObject *child, PyObject *slot) {
                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                return forwarded != 0
                    && forwarded != child
                    && slot == forwarded
                    && slot != child
                    && ((((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0);
            }

            static int check_generator_send_slot(void) {
                PyObject *frame = py_list_new(0);
                if (frame == 0) return 0;
                PyObject *gen = py_gen_new((void *)dummy_gen_resume, frame);
                if (gen == 0) return 0;
                pcc_gc_release(frame);

                (void)pcc_gc_step(8);
                if ((((PyObjectHeader *)gen)->flags & PY_FLAG_GC_OLD) == 0) {
                    return 0;
                }

                PyObject *child = py_str_new("gen-send", 8);
                if (child == 0) return 0;
                py_gen_set_state(gen, 1);
                PyObject *result = py_gen_send(gen, child);
                if (result == 0) return 0;
                pcc_gc_release(result);
                force_refill();

                PyGenObject *g = (PyGenObject *)gen;
                int ok = forwarded_slot_matches(child, g->send_value);
                pcc_gc_release(child);
                pcc_gc_release(gen);
                return ok;
            }

            static int check_coroutine_result_slot(void) {
                PyObject *coro = py_coroutine_new_native(
                    "probe", (void *)coro_entry, 0, 0
                );
                if (coro == 0) return 0;

                (void)pcc_gc_step(8);
                if ((((PyObjectHeader *)coro)->flags & PY_FLAG_GC_OLD) == 0) {
                    return 0;
                }

                PyObject *result = py_coroutine_run(coro);
                if (result == 0) return 0;
                PyObject *child = result;
                py_incref(child);
                pcc_gc_release(result);
                force_refill();

                ProbeCoroutineObject *c = (ProbeCoroutineObject *)coro;
                int ok = forwarded_slot_matches(child, c->result);
                pcc_gc_release(child);
                pcc_gc_release(coro);
                return ok;
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                printf("%d\\n", check_generator_send_slot());
                printf("%d\\n", check_coroutine_result_slot());
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1"]


def _assert_backend_three_task_state_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_task_state_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_task_state_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            static int forwarded_slot_matches(PyObject *child, PyObject *slot) {
                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                return forwarded != 0
                    && forwarded != child
                    && slot == forwarded
                    && slot != child
                    && ((((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0);
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *coro = py_coroutine_new_native("task-coro", 0, 0, 0);
                if (coro == 0) return 3;
                PyObject *task = py_task_new(coro);
                if (task == 0) return 4;
                pcc_gc_release(coro);

                (void)pcc_gc_step(8);
                if ((((PyObjectHeader *)task)->flags & PY_FLAG_GC_OLD) == 0) {
                    return 5;
                }

                PyObject *result = py_str_new("task-result", 11);
                PyObject *waiter = py_str_new("task-waiter", 11);
                if (result == 0 || waiter == 0) return 6;
                if (
                    ((((PyObjectHeader *)result)->flags & PY_FLAG_GC_YOUNG) == 0)
                    || ((((PyObjectHeader *)waiter)->flags & PY_FLAG_GC_YOUNG) == 0)
                ) {
                    return 7;
                }
                py_task_set_result(task, result);
                py_task_set_waiter(task, waiter);
                if ((((PyObjectHeader *)task)->flags & PY_FLAG_GC_REMEMBERED) == 0) {
                    return 8;
                }

                force_refill();

                PyTaskObject *t = (PyTaskObject *)task;
                printf("%d\\n", forwarded_slot_matches(result, t->result));
                printf("%d\\n", forwarded_slot_matches(waiter, t->waiter));

                py_task_set_result(task, py_None);
                py_task_set_waiter(task, 0);
                pcc_gc_release(result);
                pcc_gc_release(waiter);
                pcc_gc_release(task);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "512",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1"]


def _assert_backend_three_scheduler_root_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_scheduler_root_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_scheduler_root_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *slot = 0;
                pcc_gc_scheduler_root_register(&slot);

                PyObject *child = py_str_new("scheduler-root", 14);
                if (child == 0) return 3;
                pcc_gc_store_root(&slot, child);
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", slot == forwarded ? 1 : 0);
                printf("%d\\n", slot != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_scheduler_root_unregister(&slot);
                pcc_gc_store_root(&slot, 0);
                pcc_gc_release(child);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def _assert_backend_three_scheduler_queue_entry_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_scheduler_queue_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_scheduler_queue_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PccGcSchedulerQueue *queue = pcc_gc_scheduler_queue_new();
                if (queue == 0) return 3;

                PyObject *child = py_str_new("queued-task", 11);
                if (child == 0) return 4;
                if (pcc_gc_scheduler_queue_push(queue, child) != 0) return 5;
                printf("%d\\n", pcc_gc_scheduler_queue_len(queue) == 1 ? 1 : 0);

                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);

                PyObject *out = 0;
                pcc_gc_scheduler_root_register(&out);
                printf("%d\\n", pcc_gc_scheduler_queue_pop_into(queue, &out) == 1 ? 1 : 0);
                printf("%d\\n", out == forwarded ? 1 : 0);
                printf("%d\\n", pcc_gc_scheduler_queue_len(queue) == 0 ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 0 : 1);

                pcc_gc_scheduler_root_unregister(&out);
                pcc_gc_store_root(&out, 0);
                pcc_gc_scheduler_queue_free(queue);
                pcc_gc_release(child);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1", "1"]


def _assert_backend_three_class_metadata_slots_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_class_metadata_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_minor_class_metadata_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            static void force_refill(void) {
                for (int i = 0; i < 6; i++) {
                    PyObject *filler = pcc_gc_alloc(96, PY_TYPE_FLOAT, 0);
                    if (filler == 0) exit(30 + i);
                    pcc_gc_release(filler);
                }
            }

            static int forwarded_slot_matches(PyObject *child, PyObject *slot) {
                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                return forwarded != 0
                    && forwarded != child
                    && slot == forwarded
                    && slot != child
                    && ((((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0);
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyClassObject *cls = py_class_new("C", 0, 0, 0, 0);
                if (cls == 0) return 3;
                (void)pcc_gc_step(1);

                PyObject *method_child = py_str_new("method-child", 12);
                PyObject *del_child = py_str_new("del-child", 9);
                if (method_child == 0 || del_child == 0) return 4;

                py_class_add_method(cls, "m", method_child);
                py_class_add_method(cls, "__del__", del_child);
                force_refill();

                printf("%d\\n", cls->n_methods == 2 ? 1 : 0);
                printf("%d\\n", forwarded_slot_matches(method_child, cls->methods[0].func));
                printf("%d\\n", forwarded_slot_matches(del_child, cls->methods[1].func));
                printf("%d\\n", forwarded_slot_matches(del_child, cls->del_method));
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1"]


def _assert_backend_three_forwarded_minor_source_cleanup(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_forwarded_minor_cleanup_probe.c"
    exe = tmp_path / f"{archive_name}_forwarded_minor_cleanup_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            static void force_refill(void) {
                for (int i = 0; i < 4; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) exit(20 + i);
                    pcc_gc_release(filler);
                }
            }

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;
                pcc_gc_pin((PyObject *)owner);

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);

                PyObject *replacement = pcc_gc_alloc(256, PY_TYPE_INT, 0);
                if (replacement == 0) return 6;
                int64_t replace_rc = pcc_gc_install_forwarding(child, replacement);
                printf("%d\\n", replace_rc != 0 ? 1 : 0);
                printf("%d\\n", pcc_gc_note_relocation_read(child) == forwarded ? 1 : 0);
                printf(
                    "%d\\n",
                    pcc_gc_load_ptr((PyObject *)owner, &owner->items[0]) == forwarded
                        ? 1
                        : 0
                );

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_unpin((PyObject *)owner);
                pcc_gc_release(child);
                pcc_gc_release(replacement);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1"]


def _assert_backend_three_cross_domain_remembered_slot_rewrite(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_cross_domain_remembered_probe.c"
    exe = tmp_path / f"{archive_name}_cross_domain_remembered_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            typedef struct {
                PyObject *child;
                int64_t tid;
            } WorkerResult;

            static void force_refill(void) {
                for (int i = 0; i < 4; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) exit(20 + i);
                    pcc_gc_release(filler);
                }
            }

            static void *alloc_child_in_worker(void *arg) {
                WorkerResult *result = (WorkerResult *)arg;
                result->tid = pcc_current_thread_id();
                result->child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                return result->child == 0 ? (void *)(uintptr_t)1 : 0;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 3;
                }
                int64_t main_tid = pcc_current_thread_id();
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 4;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 5;
                pcc_gc_pin((PyObject *)owner);
                (void)pcc_gc_step(1);

                WorkerResult worker = {0, 0};
                PccThreadHandle *thread = 0;
                void *thread_result = 0;
                if (pcc_thread_start(&thread, alloc_child_in_worker, &worker) != 0) {
                    return 6;
                }
                if (pcc_thread_join(thread, &thread_result) != 0) return 7;
                if (thread_result != 0 || worker.child == 0) return 8;
                printf("%d\\n", worker.tid != main_tid ? 1 : 0);

                int32_t child_flags = py_header(worker.child)->flags;
                printf(
                    "%d\\n",
                    (child_flags & PY_FLAG_GC_YOUNG) != 0
                        && (child_flags & PY_FLAG_GC_MINOR_ARENA) != 0
                        ? 1
                        : 0
                );

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], worker.child);
                printf(
                    "%d\\n",
                    (py_header((PyObject *)owner)->flags & PY_FLAG_GC_REMEMBERED) != 0
                        ? 1
                        : 0
                );

                printf("%d\\n", pcc_gc_step(64) > 0 ? 1 : 0);
                force_refill();

                PyObject *forwarded = pcc_gc_note_relocation_read(worker.child);
                printf("%d\\n", forwarded != 0 && forwarded != worker.child ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf(
                    "%d\\n",
                    (
                        (py_header(forwarded)->flags & PY_FLAG_GC_OLD) != 0
                        && (py_header(forwarded)->flags & PY_FLAG_GC_MINOR_ARENA) == 0
                    ) ? 1 : 0
                );

                PyObject *replacement = pcc_gc_alloc(256, PY_TYPE_INT, 0);
                if (replacement == 0) return 9;
                printf(
                    "%d\\n",
                    pcc_gc_install_forwarding(worker.child, replacement) != 0 ? 1 : 0
                );
                printf(
                    "%d\\n",
                    pcc_gc_load_ptr((PyObject *)owner, &owner->items[0]) == forwarded
                        ? 1
                        : 0
                );

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_unpin((PyObject *)owner);
                pcc_gc_release(worker.child);
                pcc_gc_release(replacement);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link.extend(extra_link_args)
    link.extend(["-pthread", "-o", str(exe)])
    build = subprocess.run(
        link,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1", "1", "1", "1", "1"]


def test_generational_backend_small_alloc_uses_minor_fast_path(tmp_path):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import load_i32

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            print(pcc_gc_backend())
            pcc_gc_telemetry_reset()
            o = pcc_gc_alloc(64, 2, 0)
            print(load_i32(o, 12) & 128)
            print(pcc_gc_telemetry(8))
            print(pcc_gc_telemetry(10))
            pcc_gc_release(o)

        if __name__ == "__main__":
            main()
        """,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["3", "128", "1", "64"]


def test_generational_backend_minor_heap_pressure_triggers_collection(tmp_path):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            pcc_gc_telemetry_reset()
            i: int = 0
            while i < 40:
                o = pcc_gc_alloc(64, 2, 0)
                i = i + 1
            print(pcc_gc_telemetry(8))
            print(pcc_gc_telemetry(9) > 0)
            print(pcc_gc_telemetry(10) <= 1024)

        if __name__ == "__main__":
            main()
        """,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["40", "True", "True"]


def test_generational_backend_c_runtime_uses_minor_bump_arena(tmp_path):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_arena_probe.c"
    exe = tmp_path / "minor_arena_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>

            enum {
                PY_FLAG_GC_MINOR_ARENA = 0x1000,
                MINOR_ARENA_REFILLS = 19,
                MINOR_ARENA_BUMPS = 20,
                MINOR_ARENA_FALLBACKS = 21
            };

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *objects[8];
                for (int i = 0; i < 8; i++) {
                    objects[i] = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (objects[i] == 0) return 3;
                }

                PyObjectHeader *h = (PyObjectHeader *)objects[0];
                printf("%d\\n", (h->flags & PY_FLAG_GC_MINOR_ARENA) != 0);
                printf("%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_MINOR_ALLOCATIONS));
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_REFILLS));
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_BUMPS));
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_FALLBACKS));

                for (int i = 0; i < 8; i++) {
                    pcc_gc_release(objects[i]);
                }
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "512",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines == ["1", "8", "1", "8", "0"]


def test_generational_backend_c_runtime_skips_graph_leaf_tracking(tmp_path):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "gc3_graph_leaf_probe.c"
    exe = tmp_path / "gc3_graph_leaf_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>

            extern int64_t pcc_gc_object_is_known(PyObject *obj);

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }

                PyObject *leaf = pcc_gc_alloc(256, PY_TYPE_FLOAT, 0);
                PyObject *container = pcc_gc_alloc(256, PY_TYPE_LIST, 0);
                if (leaf == 0 || container == 0) return 3;
                printf("%lld\\n", (long long)pcc_gc_object_is_known(leaf));
                printf("%lld\\n", (long long)pcc_gc_object_is_known(container));
                pcc_gc_release(leaf);
                pcc_gc_release(container);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    result = _run_backend_three(exe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["0", "1"]


def test_generational_backend_c_runtime_reuses_retained_empty_minor_blocks(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_arena_reuse_probe.c"
    exe = tmp_path / "minor_arena_reuse_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>

            enum {
                MINOR_ARENA_REFILLS = 19
            };

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *a0 = pcc_gc_alloc(128, PY_TYPE_NONE, 0);
                PyObject *a1 = pcc_gc_alloc(128, PY_TYPE_NONE, 0);
                PyObject *b0 = pcc_gc_alloc(128, PY_TYPE_NONE, 0);
                PyObject *b1 = pcc_gc_alloc(128, PY_TYPE_NONE, 0);
                if (a0 == 0 || a1 == 0 || b0 == 0 || b1 == 0) return 3;
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_REFILLS));

                pcc_gc_release(a0);
                pcc_gc_release(a1);
                pcc_gc_release(b0);
                pcc_gc_release(b1);

                PyObject *c0 = pcc_gc_alloc(128, PY_TYPE_NONE, 0);
                PyObject *c1 = pcc_gc_alloc(128, PY_TYPE_NONE, 0);
                PyObject *c2 = pcc_gc_alloc(128, PY_TYPE_NONE, 0);
                if (c0 == 0 || c1 == 0 || c2 == 0) return 4;
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_REFILLS));

                pcc_gc_release(c0);
                pcc_gc_release(c1);
                pcc_gc_release(c2);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["2", "2"]


def test_generational_backend_c_runtime_frees_minor_object_by_index_when_flag_clobbered(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_arena_flag_clobber_probe.c"
    exe = tmp_path / "minor_arena_flag_clobber_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>

            void pcc_gc_free_object_memory(PyObject *o);

            enum {
                PY_FLAG_GC_MINOR_ARENA = 0x1000,
                PY_FLAG_GC_MALLOC_ALLOC = 0x40000
            };

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }

                PyObject *leading = pcc_gc_alloc(64, PY_TYPE_NONE, 0);
                PyObject *o = pcc_gc_alloc(64, PY_TYPE_NONE, 0);
                if (leading == 0 || o == 0) return 3;
                PyObjectHeader *h = (PyObjectHeader *)o;
                printf("%d\\n", (h->flags & PY_FLAG_GC_MINOR_ARENA) != 0);
                h->flags &= ~PY_FLAG_GC_MINOR_ARENA;
                printf("%d\\n", h->flags != 0);
                pcc_gc_free_object_memory(o);
                PyObject *heap = pcc_gc_alloc(256, PY_TYPE_NONE, 0);
                if (heap == 0) return 4;
                printf("%d\\n", (((PyObjectHeader *)heap)->flags & PY_FLAG_GC_MALLOC_ALLOC) != 0);
                pcc_gc_free_object_memory(heap);
                puts("ok");
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    result = _run_backend_three(exe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "ok"]


def test_generational_backend_c_runtime_retains_empty_minor_span_for_stale_release(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_arena_stale_release_probe.c"
    exe = tmp_path / "minor_arena_stale_release_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }

                PyObject *o = pcc_gc_alloc(64, PY_TYPE_NONE, 0);
                if (o == 0) return 3;
                pcc_gc_release(o);

                PyObjectHeader *h = (PyObjectHeader *)o;
                h->refcount = 1;
                h->type_tag = PY_TYPE_NONE;
                h->flags = 0;
                pcc_gc_release(o);
                puts("ok");
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    result = _run_backend_three(exe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["ok"]


def test_tracing_backends_ignore_zero_flag_unknown_shell_on_release(tmp_path):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "tracing_zero_flag_shell_probe.c"
    exe = tmp_path / "tracing_zero_flag_shell_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdio.h>
            #include <stdlib.h>
            #include <sys/mman.h>

            #ifndef MAP_ANON
            #define MAP_ANON MAP_ANONYMOUS
            #endif

            int main(int argc, char **argv) {
                if (argc != 2) return 2;
                int backend = atoi(argv[1]);
                if (pcc_gc_set_backend(backend) != 0) return 3;

                void *mem = mmap(
                    0,
                    4096,
                    PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANON,
                    -1,
                    0
                );
                if (mem == MAP_FAILED) return 4;

                PyObjectHeader *h = (PyObjectHeader *)mem;
                h->refcount = 1;
                h->type_tag = PY_TYPE_NONE;
                h->flags = 0;

                pcc_gc_release((PyObject *)mem);
                munmap(mem, 4096);
                puts("ok");
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    for backend in ("1", "2"):
        result = subprocess.run(
            [str(exe), backend],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, (
            f"backend={backend} rc={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert result.stdout.strip() == "ok"


def test_generational_backend_pcc_python_runtime_uses_minor_bump_arena(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import load_i32

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            print(pcc_gc_backend())
            pcc_gc_telemetry_reset()

            o0 = pcc_gc_alloc(64, 2, 0)
            # Check allocation provenance before later pressure may legally
            # promote ``o0`` to an old-generation copy.  Promotion clears the
            # minor-arena ownership bit by design; dedicated tests below cover
            # that transition and root rewriting.
            print(load_i32(o0, 12) & 4096)
            i: int = 1
            while i < 8:
                o = pcc_gc_alloc(64, 2, 0)
                pcc_gc_release(o)
                i = i + 1

            print(pcc_gc_telemetry(8))
            print(pcc_gc_telemetry(19))
            print(pcc_gc_telemetry(20))
            print(pcc_gc_telemetry(21))
            pcc_gc_release(o0)

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["3", "4096", "8", "1", "8", "0"]


def test_generational_backend_pcc_python_runtime_skips_graph_leaf_tracking(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_object_is_known = extern("pcc_gc_object_is_known", (c_ptr,), c_int64)

        def main() -> None:
            leaf = pcc_gc_alloc(256, 3, 0)
            container = pcc_gc_alloc(256, 5, 0)
            print(pcc_gc_object_is_known(leaf))
            print(pcc_gc_object_is_known(container))
            pcc_gc_release(leaf)
            pcc_gc_release(container)

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["0", "1"]


def test_generational_backend_pcc_python_runtime_reuses_retained_empty_minor_blocks(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            pcc_gc_telemetry_reset()
            a0 = pcc_gc_alloc(128, 0, 0)
            a1 = pcc_gc_alloc(128, 0, 0)
            b0 = pcc_gc_alloc(128, 0, 0)
            b1 = pcc_gc_alloc(128, 0, 0)
            print(pcc_gc_telemetry(19))
            pcc_gc_release(a0)
            pcc_gc_release(a1)
            pcc_gc_release(b0)
            pcc_gc_release(b1)

            c0 = pcc_gc_alloc(128, 0, 0)
            c1 = pcc_gc_alloc(128, 0, 0)
            c2 = pcc_gc_alloc(128, 0, 0)
            print(pcc_gc_telemetry(19))
            pcc_gc_release(c0)
            pcc_gc_release(c1)
            pcc_gc_release(c2)

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_BACKEND": "3",
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["2", "2"]


def test_generational_backend_pcc_python_runtime_frees_minor_object_by_index_when_flag_clobbered(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import load_i32, store_i32

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_free_object_memory = extern(
            "pcc_gc_free_object_memory", (c_ptr,), c_void
        )
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)

        def main() -> None:
            print(pcc_gc_backend())
            leading = pcc_gc_alloc(64, 0, 0)
            o = pcc_gc_alloc(64, 0, 0)
            flags: int = load_i32(o, 12)
            print(flags & 4096)
            store_i32(o, 12, flags & ~4096)
            print((flags & ~4096) != 0)
            pcc_gc_free_object_memory(o)
            heap = pcc_gc_alloc(256, 0, 0)
            print(load_i32(heap, 12) & 262144)
            pcc_gc_free_object_memory(heap)
            print("ok")

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == [
        "3",
        "4096",
        "True",
        "262144",
        "ok",
    ]


def test_generational_backend_pcc_python_runtime_retains_empty_minor_span_for_stale_release(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import store_i32, store_i64

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)

        def main() -> None:
            print(pcc_gc_backend())
            o = pcc_gc_alloc(64, 0, 0)
            pcc_gc_release(o)
            store_i64(o, 0, 1)
            store_i32(o, 8, 0)
            store_i32(o, 12, 0)
            pcc_gc_release(o)
            print("ok")

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["3", "ok"]


def test_generational_backend_pcc_python_runtime_threaded_minor_blocks(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)

    src = tmp_path / "pcc_py_minor_thread_blocks.c"
    exe = tmp_path / "pcc_py_minor_thread_blocks.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            enum {
                MINOR_ARENA_REFILLS = 19
            };

            static void *alloc_once(void *arg) {
                (void)arg;
                PyObject *o = pcc_gc_alloc(64, PY_TYPE_NONE, 0);
                if (o == 0) return (void *)(uintptr_t)1;
                pcc_gc_release(o);
                return 0;
            }

            static int run_thread_once(void) {
                PccThreadHandle *t = 0;
                void *result = 0;
                if (pcc_thread_start(&t, alloc_once, 0) != 0) return 1;
                if (pcc_thread_join(t, &result) != 0) return 2;
                return result == 0 ? 0 : 3;
            }

            int main(void) {
                if (pcc_threads_enabled() != 1) return 2;
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 3;
                }
                pcc_gc_telemetry_reset();

                if (run_thread_once() != 0) return 4;
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_REFILLS));
                if (run_thread_once() != 0) return 5;
                printf("%lld\\n", (long long)pcc_gc_telemetry(MINOR_ARENA_REFILLS));
                if (pcc_gc_set_backend(PCC_GC_KIND_REFCOUNT_CYCLE) != 0) return 6;
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            "-pthread",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime_pcc_py.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "512",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "2"]


def test_generational_backend_pcc_python_runtime_class_instances_deallocate_from_minor_arena(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int64

        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)

        class A:
            pass

        def make() -> None:
            a = A()

        def main() -> None:
            print("backend", pcc_gc_backend())
            i: int = 0
            while i < 100:
                make()
                i = i + 1
            print("ok")

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["backend 3", "ok"]


def test_generational_backend_pcc_python_runtime_string_constructor_preserves_minor_flags(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int64, c_ptr, c_void
        from pcc.unsafe import cstr, load_i32

        py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            pcc_gc_telemetry_reset()
            s = py_str_new(cstr("abc"), 3)
            flags: int = load_i32(s, 12)
            print(pcc_gc_backend())
            print(flags & 128)
            print(flags & 4096)
            pcc_gc_release(s)

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["3", "128", "4096"]


def test_generational_backend_pcc_python_runtime_minor_refill_promotes_remembered_young_child(
    tmp_path,
):
    exe = _compile_probe(
        tmp_path,
        """
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import calloc, load_i32, null, store_i64, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
        pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)

        def main() -> None:
            pcc_gc_telemetry_reset()
            owner = pcc_gc_alloc(40, 5, 0)
            store_i64(owner, 16, 1)
            store_i64(owner, 24, 1)
            items = calloc(1, 8)
            store_ptr(owner, 32, items)

            pcc_gc_step(1)
            child = pcc_gc_alloc(64, 2, 0)
            pcc_gc_store_ptr(owner, items, child)
            print(load_i32(child, 12) & 128)
            print(load_i32(owner, 12) & 512)

            i: int = 0
            while i < 20:
                pcc_gc_alloc(64, 2, 0)
                i = i + 1

            print(pcc_gc_telemetry(9) > 0)
            print(load_i32(child, 12) & 256)
            print(load_i32(owner, 12) & 512)

            pcc_gc_store_ptr(owner, items, null())
            pcc_gc_release(child)
            pcc_gc_release(owner)

        if __name__ == "__main__":
            main()
        """,
        runtime_cc="pcc",
        runtime_high="py",
        backend="self",
        ir_scaffold_mode=None,
    )
    result = _run_backend_three(exe)
    assert result.returncode == 0, (
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().splitlines() == ["128", "512", "True", "256", "0"]


def test_generational_backend_minor_refill_promotes_tls_exception_root(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_minor_refill_promotes_tls_exception_root(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_promotes_remembered_young_child(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_refill_probe.c"
    exe = tmp_path / "minor_refill_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            enum {
                PY_FLAG_GC_YOUNG = 0x80,
                PY_FLAG_GC_OLD = 0x100,
                PY_FLAG_GC_REMEMBERED = 0x200
            };

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                printf("%d\\n", ((PyObjectHeader *)child)->flags & PY_FLAG_GC_YOUNG ? 1 : 0);
                printf("%d\\n", owner->h.flags & PY_FLAG_GC_REMEMBERED ? 1 : 0);

                PyObject *fillers[3];
                for (int i = 0; i < 3; i++) {
                    fillers[i] = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (fillers[i] == 0) return 6;
                }

                printf("%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_MINOR_COLLECTIONS));
                printf("%d\\n", ((PyObjectHeader *)child)->flags & PY_FLAG_GC_OLD ? 1 : 0);
                printf("%d\\n", owner->h.flags & PY_FLAG_GC_REMEMBERED ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)fillers[2])->flags & PY_FLAG_GC_YOUNG ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                for (int i = 0; i < 3; i++) {
                    pcc_gc_release(fillers[i]);
                }
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "0", "1"]


def test_generational_backend_minor_refill_oldifies_copy_for_remembered_child(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_oldify_copy_probe.c"
    exe = tmp_path / "minor_oldify_copy_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                for (int i = 0; i < 3; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) return 6;
                    pcc_gc_release(filler);
                }

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                PyObject *loaded = pcc_gc_load_ptr(
                    (PyObject *)owner, &owner->items[0]
                );

                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", loaded == forwarded ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_OLD ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "0"]


def test_generational_backend_minor_refill_rewrites_remembered_list_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)

    src = tmp_path / "minor_oldify_slot_rewrite_probe.c"
    exe = tmp_path / "minor_oldify_slot_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                for (int i = 0; i < 3; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) return 6;
                    pcc_gc_release(filler);
                }

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", owner->items[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime.a"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def _assert_backend_three_young_owner_promotion_rewrites_list_referent(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_young_owner_slot_rewrite_probe.c"
    exe = tmp_path / f"{archive_name}_young_owner_slot_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            int main(void) {
                static const int32_t frame_map[1] = {1};
                PyObject *roots[1] = {0};

                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *owner = py_list_new(1);
                if (owner == 0) return 3;
                PyObject *child = py_str_new("args", 4);
                if (child == 0) return 4;

                py_list_append(owner, child);
                pcc_gc_store_root(&roots[0], child);
                pcc_gc_frame_enter(frame_map, roots);

                (void)pcc_gc_step(1024);

                PyListObject *list = (PyListObject *)owner;
                PyObject *forwarded = pcc_gc_note_relocation_read(child);

                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", roots[0] == forwarded ? 1 : 0);
                printf("%d\\n", list->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", pcc_gc_load_ptr(owner, &list->items[0]) == forwarded ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_OLD ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_frame_leave(roots);
                pcc_gc_store_root(&roots[0], 0);
                pcc_gc_release(child);
                pcc_gc_release(owner);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link_cmd = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link_cmd.extend(extra_link_args)
    link_cmd.extend(["-o", str(exe)])
    build = subprocess.run(
        link_cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "1", "0"]


def _assert_backend_three_safepoint_does_not_promote_frame_roots(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_safepoint_root_promotion_probe.c"
    exe = tmp_path / f"{archive_name}_safepoint_root_promotion_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            int main(void) {
                static const int32_t borrowed_frame_map[1] = {-1};
                PyObject *roots[1] = {0};

                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *root = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (root == 0) return 3;
                roots[0] = root;
                pcc_gc_frame_enter(borrowed_frame_map, roots);

                pcc_gc_safepoint();
                printf("%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_RELOCATION_FORWARDS));
                printf("%d\\n", pcc_gc_note_relocation_read(root) == root ? 1 : 0);

                (void)pcc_gc_step(1024);
                PyObject *forwarded = pcc_gc_note_relocation_read(root);
                printf("%d\\n", forwarded != 0 && forwarded != root ? 1 : 0);
                printf("%d\\n", roots[0] == forwarded ? 1 : 0);

                pcc_gc_frame_leave(roots);
                roots[0] = 0;
                pcc_gc_release(root);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link_cmd = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link_cmd.extend(extra_link_args)
    link_cmd.extend(["-o", str(exe)])
    build = subprocess.run(
        link_cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["0", "1", "1", "1"]


def test_generational_backend_young_owner_promotion_rewrites_list_referent_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_young_owner_promotion_rewrites_list_referent(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_safepoint_does_not_promote_frame_roots(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_safepoint_does_not_promote_frame_roots(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_non_list_slots_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_frame_root_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_borrowed_frame_root_rewrite_preserves_source_ref(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_borrowed_frame_root_rewrite_preserves_source_ref(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_suspended_generator_frame_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_generator_coroutine_state_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_task_state_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_task_state_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_scheduler_root_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_scheduler_queue_entry_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_refill_rewrites_class_metadata_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_class_metadata_slots_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_forwarded_minor_source_is_inactive_after_oldify(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_forwarded_minor_source_cleanup(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def _assert_backend_three_forwarded_source_release_consumes_source_ref(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / "forwarded_source_release_probe.c"
    exe = tmp_path / "forwarded_source_release_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *source = py_str_new("x", 1);
                if (source == 0) return 3;

                (void)pcc_gc_step(1024);
                PyObject *target = pcc_gc_note_relocation_read(source);
                if (target == 0 || target == source) return 4;

                py_incref(target);
                printf("%lld\\n", (long long)pcc_gc_backend4_forwarding_entries());
                pcc_gc_release(source);
                printf("%lld\\n", (long long)pcc_gc_backend4_forwarding_entries());
                printf("%lld\\n", (long long)((PyObjectHeader *)target)->refcount);
                pcc_gc_release(target);
                return 0;
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / archive_name),
        ]
        + list(extra_link_args or [])
        + [
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    result = _run_backend_three(exe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "0", "1"]


def _assert_backend_three_oldified_tuple_retains_old_child(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_oldified_tuple_child_ref_probe.c"
    exe = tmp_path / f"{archive_name}_oldified_tuple_child_ref_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            int main(void) {
                static const int32_t frame_map[1] = {1};
                PyObject *roots[1] = {0};

                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *child_source = py_str_new("x", 1);
                if (child_source == 0) return 3;
                pcc_gc_store_root(&roots[0], child_source);
                pcc_gc_release(child_source);
                pcc_gc_frame_enter(frame_map, roots);
                (void)pcc_gc_step(1024);

                PyObject *child_old = roots[0];
                if (child_old == 0 || child_old == child_source) return 4;

                PyObject *owner = py_tuple_new(1);
                if (owner == 0) return 5;
                py_tuple_set_item(owner, 0, child_old);

                pcc_gc_frame_leave(roots);
                pcc_gc_store_root(&roots[0], 0);
                pcc_gc_store_root(&roots[0], owner);
                pcc_gc_release(owner);
                pcc_gc_frame_enter(frame_map, roots);
                (void)pcc_gc_step(1024);

                PyObject *owner_old = roots[0];
                PyObject *slot = ((PyTupleObject *)owner_old)->items[0];
                int64_t known = pcc_gc_object_is_known(slot);
                printf("%lld\\n", (long long)known);
                if (known == 0) return 0;

                PyObject *item = py_tuple_get(owner_old, 0);
                printf("%d\\n", item == slot ? 1 : 0);
                printf("%d\\n", py_header(item)->type_tag == PY_TYPE_STR ? 1 : 0);
                printf("%lld\\n", (long long)py_str_byte_len(item));
                pcc_gc_release(item);

                pcc_gc_frame_leave(roots);
                pcc_gc_store_root(&roots[0], 0);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link_cmd = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link_cmd.extend(extra_link_args)
    link_cmd.extend(["-o", str(exe)])
    build = subprocess.run(
        link_cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1"]


def _assert_backend_three_minor_arena_tuple_cycle_promotes_in_place(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_minor_tuple_cycle_promotion_probe.c"
    exe = tmp_path / f"{archive_name}_minor_tuple_cycle_promotion_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            int main(void) {
                static const int32_t frame_map[1] = {1};
                PyObject *roots[1] = {0};

                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *a = py_tuple_new(1);
                PyObject *b = py_tuple_new(1);
                if (a == 0 || b == 0) return 3;
                py_tuple_set_item(a, 0, b);
                py_tuple_set_item(b, 0, a);

                pcc_gc_store_root(&roots[0], a);
                pcc_gc_release(a);
                pcc_gc_release(b);
                pcc_gc_frame_enter(frame_map, roots);

                (void)pcc_gc_step(1024);

                PyObject *root = pcc_gc_load_ptr(0, &roots[0]);
                PyObject *child = ((PyTupleObject *)root)->items[0];
                printf("%d\\n", (py_header(root)->flags & PY_FLAG_GC_OLD) != 0);
                printf("%d\\n", (py_header(root)->flags & PY_FLAG_GC_YOUNG) != 0);
                printf("%d\\n", (py_header(child)->flags & PY_FLAG_GC_OLD) != 0);
                printf("%d\\n", (py_header(child)->flags & PY_FLAG_GC_YOUNG) != 0);
                printf("%d\\n", ((PyTupleObject *)child)->items[0] == root);

                pcc_gc_frame_leave(roots);
                pcc_gc_store_root(&roots[0], 0);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link_cmd = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link_cmd.extend(extra_link_args)
    link_cmd.extend(["-o", str(exe)])
    build = subprocess.run(
        link_cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "0", "1", "0", "1"]


def _assert_backend_three_string_loop_owned_root_cleanup(
    tmp_path: Path,
    work_runtime: Path,
    archive_name: str,
    *,
    extra_link_args: list[str] | None = None,
):
    src = tmp_path / f"{archive_name}_string_loop_owned_root_cleanup_probe.c"
    exe = tmp_path / f"{archive_name}_string_loop_owned_root_cleanup_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            int main(void) {
                static const int32_t frame_map[1] = {1};
                PyObject *raw_slot = 0;
                int raw_owned = 0;

                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *parts = py_tuple_new(32);
                PyObject *cooked = py_list_new(0);
                if (parts == 0 || cooked == 0) return 3;

                for (int64_t i = 0; i < 32; i++) {
                    PyObject *pair = py_tuple_new(2);
                    PyObject *text = py_str_new("piece", 5);
                    if (pair == 0 || text == 0) return 4;
                    py_tuple_set_item(pair, 0, text);
                    py_tuple_set_item(pair, 1, py_True);
                    py_tuple_set_item(parts, i, pair);
                    pcc_gc_release(text);
                    pcc_gc_release(pair);
                }

                pcc_gc_frame_enter(frame_map, &raw_slot);
                for (int64_t i = 0; i < 32; i++) {
                    PyObject *pair = py_tuple_get(parts, i);
                    PyObject *raw_new = py_tuple_get(pair, 0);
                    pcc_gc_release(pair);

                    if (raw_owned) {
                        PyObject *current = pcc_gc_load_ptr(0, &raw_slot);
                        pcc_gc_release(current);
                    }
                    raw_slot = raw_new;
                    raw_owned = 1;

                    (void)pcc_gc_step(1024);
                    PyObject *item = pcc_gc_load_ptr(0, &raw_slot);
                    py_list_append(cooked, item);
                }

                if (raw_owned) {
                    PyObject *current = pcc_gc_load_ptr(0, &raw_slot);
                    pcc_gc_release(current);
                    raw_slot = 0;
                    raw_owned = 0;
                }
                pcc_gc_frame_leave(&raw_slot);

                printf("%lld\\n", (long long)py_list_len(cooked));
                pcc_gc_release(cooked);
                pcc_gc_release(parts);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    link_cmd = [
        _cc(),
        "-std=c11",
        f"-I{work_runtime / 'include'}",
        f"-I{work_runtime / 'src'}",
        str(src),
        str(work_runtime / archive_name),
    ]
    if extra_link_args:
        link_cmd.extend(extra_link_args)
    link_cmd.extend(["-o", str(exe)])
    build = subprocess.run(
        link_cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "512",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["32"]


def test_generational_backend_release_of_forwarded_source_consumes_source_ref(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_forwarded_source_release_consumes_source_ref(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_cross_domain_remembered_slot_rewrite(tmp_path):
    work_runtime = _build_threaded_runtime(tmp_path)
    _assert_backend_three_cross_domain_remembered_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_promotes_tls_exception_root(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_minor_refill_promotes_tls_exception_root(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)

    src = tmp_path / "pcc_py_minor_oldify_copy_probe.c"
    exe = tmp_path / "pcc_py_minor_oldify_copy_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                for (int i = 0; i < 3; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) return 6;
                    pcc_gc_release(filler);
                }

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                PyObject *loaded = pcc_gc_load_ptr(
                    (PyObject *)owner, &owner->items[0]
                );

                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", loaded == forwarded ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_OLD ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime_pcc_py.a"),
            "-pthread",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "1", "0"]


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_remembered_list_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)

    src = tmp_path / "pcc_py_minor_oldify_slot_rewrite_probe.c"
    exe = tmp_path / "pcc_py_minor_oldify_slot_rewrite_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>
            #include <stdlib.h>

            typedef struct {
                PyObjectHeader h;
                int64_t length;
                int64_t capacity;
                PyObject **items;
            } ProbeListObject;

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                ProbeListObject *owner = (ProbeListObject *)pcc_gc_alloc(
                    40, PY_TYPE_LIST, 0
                );
                if (owner == 0) return 3;
                owner->length = 1;
                owner->capacity = 1;
                owner->items = (PyObject **)calloc(1, sizeof(PyObject *));
                if (owner->items == 0) return 4;

                (void)pcc_gc_step(1);
                PyObject *child = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                if (child == 0) return 5;
                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], child);

                for (int i = 0; i < 3; i++) {
                    PyObject *filler = pcc_gc_alloc(64, PY_TYPE_INT, 0);
                    if (filler == 0) return 6;
                    pcc_gc_release(filler);
                }

                PyObject *forwarded = pcc_gc_note_relocation_read(child);
                printf("%d\\n", forwarded != 0 && forwarded != child ? 1 : 0);
                printf("%d\\n", owner->items[0] == forwarded ? 1 : 0);
                printf("%d\\n", owner->items[0] != child ? 1 : 0);
                printf("%d\\n", ((PyObjectHeader *)forwarded)->flags & PY_FLAG_GC_MINOR_ARENA ? 1 : 0);

                pcc_gc_store_ptr((PyObject *)owner, &owner->items[0], 0);
                pcc_gc_release(child);
                pcc_gc_release((PyObject *)owner);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime_pcc_py.a"),
            "-pthread",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_GC_MINOR_HEAP_SIZE": "256",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "1", "1", "0"]


def test_generational_backend_pcc_python_runtime_old_list_retains_appended_tuples(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)

    src = tmp_path / "pcc_py_old_list_tuple_retention_probe.c"
    exe = tmp_path / "pcc_py_old_list_tuple_retention_probe.out"
    src.write_text(
        textwrap.dedent(
            """
            #include "py_runtime.h"
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            int main(void) {
                if (pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) != 0) {
                    return 2;
                }
                pcc_gc_telemetry_reset();

                PyObject *table = py_list_new(0);
                if (table == 0) return 3;
                (void)pcc_gc_step(1024);

                for (int i = 0; i < 96; i++) {
                    PyObject *name = py_str_new("field", 5);
                    PyObject *kind = py_str_new("type", 4);
                    PyObject *pair = py_tuple_new(2);
                    if (name == 0 || kind == 0 || pair == 0) return 4;
                    py_tuple_set_item(pair, 0, name);
                    py_tuple_set_item(pair, 1, kind);
                    py_list_append(table, pair);
                    py_decref(name);
                    py_decref(kind);
                    py_decref(pair);
                    if ((i % 8) == 7) {
                        (void)pcc_gc_step(1024);
                    }
                }

                printf("%lld\\n", (long long)py_list_len(table));
                py_decref(table);
                return 0;
            }
            """
        ).lstrip()
    , encoding="utf-8")
    build = subprocess.run(
        [
            _cc(),
            "-std=c11",
            f"-I{work_runtime / 'include'}",
            f"-I{work_runtime / 'src'}",
            str(src),
            str(work_runtime / "libpy_runtime_pcc_py.a"),
            "-pthread",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr

    env = os.environ.copy()
    env.update(
        {
            "PCC_DEBUG_RUNTIME": "1",
            "PCC_GC_MINOR_HEAP_SIZE": "512",
            "PCC_GC_MINOR_ALLOC_MAX": "128",
        }
    )
    result = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "[BAD_INCREF]" not in result.stderr
    assert result.stdout.strip().splitlines() == ["96"]


def test_generational_backend_pcc_python_runtime_young_owner_promotion_rewrites_list_referent_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_young_owner_promotion_rewrites_list_referent(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_safepoint_does_not_promote_frame_roots(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_safepoint_does_not_promote_frame_roots(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_non_list_slots_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_frame_root_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_frame_root_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_borrowed_frame_root_rewrite_preserves_source_ref(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_borrowed_frame_root_rewrite_preserves_source_ref(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_suspended_generator_frame_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_suspended_generator_frame_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_continuation_stack_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_continuation_stack_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_generator_coroutine_state_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_generator_coroutine_state_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_task_state_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_task_state_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_scheduler_root_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_scheduler_queue_entry_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_refill_rewrites_class_metadata_slots_to_oldified_copy(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_class_metadata_slots_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_forwarded_minor_source_is_inactive_after_oldify(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_forwarded_minor_source_cleanup(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_release_of_forwarded_source_consumes_source_ref(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_forwarded_source_release_consumes_source_ref(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_oldified_tuple_retains_old_child_ref(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_oldified_tuple_retains_old_child(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_minor_arena_tuple_cycle_promotes_in_place(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_minor_arena_tuple_cycle_promotes_in_place(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_string_loop_owned_root_cleanup(
    tmp_path,
):
    work_runtime = _build_runtime(tmp_path)
    _assert_backend_three_string_loop_owned_root_cleanup(
        tmp_path,
        work_runtime,
        "libpy_runtime.a",
    )


def test_generational_backend_pcc_python_runtime_oldified_tuple_retains_old_child_ref(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_oldified_tuple_retains_old_child(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_minor_arena_tuple_cycle_promotes_in_place(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_minor_arena_tuple_cycle_promotes_in_place(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_string_loop_owned_root_cleanup(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_string_loop_owned_root_cleanup(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )


def test_generational_backend_pcc_python_runtime_cross_domain_remembered_slot_rewrite(
    tmp_path,
):
    work_runtime = _build_pcc_py_runtime(tmp_path)
    _assert_backend_three_cross_domain_remembered_slot_rewrite(
        tmp_path,
        work_runtime,
        "libpy_runtime_pcc_py.a",
        extra_link_args=["-pthread"],
    )
