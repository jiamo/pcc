from pathlib import Path
import subprocess
import textwrap

from pcc.py_frontend.codegen.runtime_abi import RUNTIME_SIGNATURES


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_HEADER = REPO_ROOT / "pcc" / "py_runtime" / "include" / "py_runtime.h"
PY_OBJ_PORT = REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_obj.py"
LAYER1_CODEGEN = REPO_ROOT / "pcc" / "py_frontend" / "codegen" / "layer1.py"


BACKEND_REFCOUNT = 0
BACKEND_TRICOLOR = 1
BACKEND_CONCURRENT = 2
BACKEND_GENERATIONAL = 3
BACKEND_COLORED_RELOCATING = 4
BACKEND_INVALID = 9

GC_SURFACE = [
    "pcc_gc_alloc",
    "pcc_gc_retain",
    "pcc_gc_release",
    "pcc_gc_load_ptr",
    "pcc_gc_store_ptr",
    "pcc_gc_store_root",
    "pcc_gc_frame_enter",
    "pcc_gc_frame_leave",
    "pcc_gc_safepoint",
    "pcc_gc_collect",
    "pcc_gc_pin",
    "pcc_gc_unpin",
    "pcc_gc_object_id",
    "pcc_gc_reset_relocation_set",
    "pcc_gc_select_relocation_set",
    "pcc_gc_relocation_set_contains",
    "pcc_gc_relocation_set_size",
    "pcc_gc_install_forwarding",
    "pcc_gc_relocate_copy",
    "pcc_gc_backend",
    "pcc_gc_set_backend",
    "pcc_gc_backend_name",
    "pcc_gc_telemetry",
    "pcc_gc_telemetry_reset",
    "pcc_gc_step",
]

GC_KIND_NAMES = [
    "PCC_GC_KIND_REFCOUNT_CYCLE",
    "PCC_GC_KIND_INCREMENTAL_TRICOLOR",
    "PCC_GC_KIND_CONCURRENT_MARK_SWEEP",
    "PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR",
    "PCC_GC_KIND_COLORED_RELOCATING",
]

GC_COUNTER_NAMES = [
    "PCC_GC_COUNTER_ALLOCATIONS",
    "PCC_GC_COUNTER_WRITE_BARRIERS",
    "PCC_GC_COUNTER_READ_BARRIERS",
    "PCC_GC_COUNTER_SAFEPOINTS",
    "PCC_GC_COUNTER_PIN_BALANCE",
    "PCC_GC_COUNTER_WORK_STEPS",
    "PCC_GC_COUNTER_DEBT_BYTES",
    "PCC_GC_COUNTER_MAX_PAUSE_US",
    "PCC_GC_COUNTER_MINOR_ALLOCATIONS",
    "PCC_GC_COUNTER_MINOR_COLLECTIONS",
    "PCC_GC_COUNTER_MINOR_BYTES",
    "PCC_GC_COUNTER_CMS_WORKER_STARTS",
    "PCC_GC_COUNTER_CMS_QUEUE_PUSHES",
    "PCC_GC_COUNTER_CMS_WORKER_DRAINS",
    "PCC_GC_COUNTER_CMS_MUTATOR_ASSISTS",
    "PCC_GC_COUNTER_RELOCATION_FORWARDS",
    "PCC_GC_COUNTER_RELOCATION_BARRIER_FORWARDS",
    "PCC_GC_COUNTER_RELOCATION_PIN_REJECTS",
    "PCC_GC_COUNTER_CMS_WORKER_TRACES",
    "PCC_GC_COUNTER_MINOR_ARENA_REFILLS",
    "PCC_GC_COUNTER_MINOR_ARENA_BUMPS",
    "PCC_GC_COUNTER_MINOR_ARENA_FALLBACKS",
    "PCC_GC_COUNTER_CMS_WORKER_STOPS",
    "PCC_GC_COUNTER_CMS_WB_FLUSHES",
]


def test_gc_abstraction_surface_is_public_runtime_abi():
    header = RUNTIME_HEADER.read_text(encoding="utf-8")
    for name in GC_SURFACE:
        assert name in header
        assert name in RUNTIME_SIGNATURES


def test_gc_backend_kinds_are_algorithmic_not_project_branded():
    header = RUNTIME_HEADER.read_text(encoding="utf-8")
    for name in GC_KIND_NAMES + GC_COUNTER_NAMES:
        assert name in header
    forbidden = [
        "PCC_GC_BACKEND_LUA",
        "PCC_GC_BACKEND_GO",
        "PCC_GC_BACKEND_OCAML",
        "PCC_GC_BACKEND_ZGC",
    ]
    for name in forbidden:
        assert name not in header


def test_pcc_python_refcount_backend_exports_gc_surface():
    py_obj = PY_OBJ_PORT.read_text(encoding="utf-8")
    for name in GC_SURFACE[:12]:
        assert f'@c_abi_export("{name}")' in py_obj
    # py_gc_backend.py covers most of the late surface, but
    # ``pcc_gc_telemetry`` was extracted into ``py_gc_telemetry.py``.
    # Check the union of the two backend-side files.
    py_gc_backend = (
        REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_gc_backend.py"
    ).read_text(encoding="utf-8")
    py_gc_telemetry = (
        REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_gc_telemetry.py"
    ).read_text(encoding="utf-8")
    backend_surface = py_gc_backend + "\n" + py_gc_telemetry
    for name in GC_SURFACE[12:]:
        assert f'@c_abi_export("{name}")' in backend_surface, (
            f"missing @c_abi_export for {name} in either "
            "py_gc_backend.py or py_gc_telemetry.py"
        )


def test_pcc_python_gc_backend_tracks_frame_stack_not_single_root_slot():
    py_gc_backend = (
        REPO_ROOT / "pcc" / "py_runtime" / "py" / "py_gc_backend.py"
    ).read_text(encoding="utf-8")
    assert 'global_load_ptr("pcc_gc_frame_head")' in py_gc_backend
    assert 'global_store_ptr("pcc_gc_frame_head", node)' in py_gc_backend
    seed_body = py_gc_backend.split("def _seed_roots() -> None:", 1)[1]
    seed_body = seed_body.split("def _begin_mark_cycle() -> None:", 1)[0]
    frame_roots_body = py_gc_backend.split("def _gray_current_roots() -> None:", 1)[
        1
    ]
    frame_roots_body = frame_roots_body.split("def _begin_mark_cycle() -> None:", 1)[
        0
    ]
    assert "_gray_current_roots()" in seed_body
    assert "load_ptr(frame, 0)" in frame_roots_body
    assert "load_ptr(frame, 8)" in frame_roots_body
    assert "load_ptr(frame, 16)" in frame_roots_body


def test_pcc_python_collect_uses_gc_hook_not_direct_stub_return():
    py_obj = PY_OBJ_PORT.read_text(encoding="utf-8")
    assert 'extern("py_gc_collect"' in py_obj
    collect_body = py_obj.split('@c_abi_export("pcc_gc_collect")', 1)[1]
    collect_body = collect_body.split('@c_abi_export("pcc_gc_pin")', 1)[0]
    assert "py_gc_collect()" in collect_body


def test_refcount_entrypoints_are_compatibility_not_codegen_surface():
    header = RUNTIME_HEADER.read_text(encoding="utf-8")
    gc_pos = header.index("/* ---- GC interface")
    refcount_pos = header.index("/* ---- INCREF/DECREF compatibility")
    assert gc_pos < refcount_pos


def test_python_codegen_uses_gc_surface_for_owned_ref_release():
    layer1 = LAYER1_CODEGEN.read_text(encoding="utf-8")
    assert 'runtime["py_incref"]' not in layer1
    assert 'runtime["py_decref"]' not in layer1


def test_gc_backend_selector_runs_in_no_libpython_binary(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(f"""
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import free, load_i32, malloc, null, ptr_add, store_i32, store_i64, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
        pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
        pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
        pcc_gc_unpin = extern("pcc_gc_unpin", (c_ptr,), c_void)
        pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_collect = extern("pcc_gc_collect", (c_int32,), c_int64)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)
        pcc_gc_safepoint = extern("pcc_gc_safepoint", (), c_void)
        pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)

        def main() -> None:
            pcc_gc_set_backend({BACKEND_REFCOUNT})
            print(pcc_gc_backend())
            print(pcc_gc_set_backend({BACKEND_TRICOLOR}))
            print(pcc_gc_backend())
            print(pcc_gc_set_backend({BACKEND_COLORED_RELOCATING}))
            print(pcc_gc_backend())
            print(pcc_gc_set_backend({BACKEND_INVALID}))
            print(pcc_gc_backend())
            pcc_gc_telemetry_reset()
            print(pcc_gc_telemetry(5))
            print(pcc_gc_step(7))
            print(pcc_gc_telemetry(5))
            pcc_gc_safepoint()
            print(pcc_gc_telemetry(3))
            print(pcc_gc_telemetry(5))

            pcc_gc_set_backend({BACKEND_TRICOLOR})
            pcc_gc_telemetry_reset()
            o = pcc_gc_alloc(24, 2, 0)
            print(pcc_gc_telemetry(0))
            print(pcc_gc_telemetry(5))
            pcc_gc_release(o)

            owner = pcc_gc_alloc(24, 2, 0)
            child = pcc_gc_alloc(24, 2, 0)
            print(load_i32(child, 12) & 8)
            store_i32(owner, 12, 32)
            slot2 = malloc(8)
            store_ptr(slot2, 0, null())
            pcc_gc_store_ptr(owner, slot2, child)
            print(load_i32(child, 12) & 16)
            pcc_gc_step(1)
            print(load_i32(child, 12) & 32)
            print(pcc_gc_collect(0))
            free(slot2)

            slot = malloc(8)
            store_ptr(slot, 0, null())
            pcc_gc_set_backend({BACKEND_CONCURRENT})
            v = pcc_gc_alloc(24, 2, 0)
            pcc_gc_telemetry_reset()
            pcc_gc_store_ptr(null(), slot, v)
            print(pcc_gc_telemetry(1))
            pcc_gc_store_ptr(null(), slot, null())
            pcc_gc_release(v)

            owner3 = pcc_gc_alloc(24, 2, 0)
            child3 = pcc_gc_alloc(24, 2, 0)
            pcc_gc_store_ptr(owner3, slot, child3)
            print(load_i32(child3, 12) & 16)
            pcc_gc_step(1024)
            print(load_i32(child3, 12) & 32)
            pcc_gc_store_ptr(owner3, slot, null())
            pcc_gc_release(child3)
            pcc_gc_release(owner3)

            pcc_gc_set_backend({BACKEND_GENERATIONAL})
            gen = pcc_gc_alloc(24, 2, 0)
            print(load_i32(gen, 12) & 128)
            pcc_gc_step(1)
            print(load_i32(gen, 12) & 256)
            pcc_gc_release(gen)

            old_owner = pcc_gc_alloc(24, 2, 0)
            young_child = pcc_gc_alloc(24, 2, 0)
            store_i32(old_owner, 12, 256)
            pcc_gc_store_ptr(old_owner, slot, young_child)
            print(load_i32(old_owner, 12) & 512)
            pcc_gc_store_ptr(old_owner, slot, null())
            pcc_gc_release(young_child)
            pcc_gc_release(old_owner)

            pcc_gc_set_backend({BACKEND_COLORED_RELOCATING})
            pcc_gc_telemetry_reset()
            pcc_gc_load_ptr(null(), slot)
            print(pcc_gc_telemetry(2))
            moving = pcc_gc_alloc(24, 2, 0)
            pcc_gc_pin(moving)
            print(load_i32(moving, 12) & 64)
            pcc_gc_unpin(moving)
            print(load_i32(moving, 12) & 64)
            pcc_gc_release(moving)
            free(slot)

        if __name__ == "__main__":
            main()
        """).lstrip())
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == [
        "0", "0", "1", "0", "4", "-1", "4", "0", "0", "1", "1", "2",
        "1", "0", "8", "16", "32", "2", "1", "0", "32",
        "128", "256", "512", "1", "64", "0",
    ]


def test_tracing_gc_backend_marks_registered_frame_roots_only(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(f"""
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import free, load_i32, malloc, null, store_i32, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
        pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
        pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)

        def main() -> None:
            pcc_gc_set_backend({BACKEND_TRICOLOR})
            rooted = pcc_gc_alloc(24, 2, 0)
            floating = pcc_gc_alloc(24, 2, 0)
            store_i32(floating, 12, load_i32(floating, 12) & ~16384)

            frame_map = malloc(4)
            root_slots = malloc(8)
            store_i32(frame_map, 0, 1)
            store_ptr(root_slots, 0, rooted)
            pcc_gc_frame_enter(frame_map, root_slots)

            print(pcc_gc_step(1024))
            print(load_i32(rooted, 12) & 32)
            print(load_i32(rooted, 12) & 1024)
            print(load_i32(floating, 12) & 8)
            print(load_i32(floating, 12) & 1024)

            pcc_gc_frame_leave(root_slots)
            print(pcc_gc_step(1024))
            print(load_i32(rooted, 12) & 8)
            print(load_i32(rooted, 12) & 1024)
            print(load_i32(floating, 12) & 8)

            pcc_gc_release(floating)
            pcc_gc_release(rooted)
            free(root_slots)
            free(frame_map)

        if __name__ == "__main__":
            main()
        """).lstrip())
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == [
        "1", "32", "0", "8", "1024", "0", "8", "1024", "8",
    ]


def test_tracing_gc_backend_traces_tuple_child_from_frame_root(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(f"""
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import free, load_i32, malloc, store_i32, store_i64, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_collect = extern("pcc_gc_collect", (c_int32,), c_int64)
        pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
        pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)

        def main() -> None:
            pcc_gc_set_backend({BACKEND_TRICOLOR})
            tup = pcc_gc_alloc(32, 7, 0)
            child = pcc_gc_alloc(24, 2, 0)
            store_i64(tup, 16, 1)
            store_ptr(tup, 24, child)

            frame_map = malloc(4)
            root_slots = malloc(8)
            store_i32(frame_map, 0, 1)
            store_ptr(root_slots, 0, tup)
            pcc_gc_frame_enter(frame_map, root_slots)

            print(pcc_gc_collect(0))
            print(load_i32(tup, 12) & 32)
            print(load_i32(child, 12) & 32)

            pcc_gc_frame_leave(root_slots)
            pcc_gc_release(child)
            pcc_gc_release(tup)
            free(root_slots)
            free(frame_map)

        if __name__ == "__main__":
            main()
        """).lstrip())
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["0", "32", "32"]


def test_tracing_gc_backend_traces_dict_and_instance_children(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(f"""
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import free, load_i32, malloc, null, store_i32, store_i64, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
        pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
        pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)

        def main() -> None:
            pcc_gc_set_backend({BACKEND_TRICOLOR})
            frame_map = malloc(4)
            root_slots = malloc(8)
            store_i32(frame_map, 0, 1)

            d = pcc_gc_alloc(56, 6, 0)
            key = pcc_gc_alloc(24, 2, 0)
            value = pcc_gc_alloc(24, 2, 0)
            entries = malloc(24)
            store_i64(entries, 0, 1)
            store_ptr(entries, 8, key)
            store_ptr(entries, 16, value)
            store_ptr(d, 40, entries)
            store_i64(d, 48, 1)
            store_ptr(root_slots, 0, d)
            pcc_gc_frame_enter(frame_map, root_slots)
            pcc_gc_step(1024)
            pcc_gc_step(1024)
            print(load_i32(key, 12) & 32)
            print(load_i32(value, 12) & 32)
            pcc_gc_frame_leave(root_slots)

            cls = pcc_gc_alloc(96, 10, 0)
            inst = pcc_gc_alloc(40, 11, 0)
            child = pcc_gc_alloc(24, 2, 0)
            store_i32(cls, 72, 1)
            store_ptr(inst, 16, cls)
            store_ptr(inst, 24, child)
            store_ptr(inst, 32, null())
            store_ptr(root_slots, 0, inst)
            pcc_gc_frame_enter(frame_map, root_slots)
            pcc_gc_step(1024)
            pcc_gc_step(1024)
            print(load_i32(child, 12) & 32)
            pcc_gc_frame_leave(root_slots)
            free(entries)
            free(root_slots)
            free(frame_map)

        if __name__ == "__main__":
            main()
        """).lstrip())
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["32", "32", "32"]


def test_tracing_gc_backend_traces_instance_class_child(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(f"""
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import free, load_i32, malloc, null, store_i32, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
        pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
        pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)

        def main() -> None:
            pcc_gc_set_backend({BACKEND_TRICOLOR})
            frame_map = malloc(4)
            root_slots = malloc(8)
            store_i32(frame_map, 0, 1)

            cls = pcc_gc_alloc(104, 10, 0)
            inst = pcc_gc_alloc(32, 11, 0)
            store_i32(cls, 24, 0)      # n_bases
            store_ptr(cls, 32, null()) # bases
            store_i32(cls, 40, 0)      # n_mro
            store_ptr(cls, 48, null()) # mro
            store_i32(cls, 56, 0)      # n_methods
            store_ptr(cls, 64, null()) # methods
            store_i32(cls, 72, 0)      # n_fields
            store_i32(cls, 88, 32)     # instance_size
            store_i32(cls, 92, 11)     # type_tag_alloc
            store_ptr(inst, 16, cls)
            store_ptr(inst, 24, null())
            store_ptr(root_slots, 0, inst)
            pcc_gc_frame_enter(frame_map, root_slots)

            i = 0
            while i < 8:
                if pcc_gc_step(1024) == 0:
                    break
                i = i + 1
            print(load_i32(inst, 12) & 32)
            print(load_i32(cls, 12) & 32)
            print(load_i32(cls, 12) & 1024)

            pcc_gc_frame_leave(root_slots)
            free(root_slots)
            free(frame_map)

        if __name__ == "__main__":
            main()
        """).lstrip())
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["32", "32", "0"]


def test_tracing_gc_backend_traces_coroutine_children(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(f"""
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import free, load_i32, malloc, store_i32, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_collect = extern("pcc_gc_collect", (c_int32,), c_int64)
        pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
        pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)

        def main() -> None:
            pcc_gc_set_backend({BACKEND_TRICOLOR})
            frame_map = malloc(4)
            root_slots = malloc(8)
            store_i32(frame_map, 0, 1)

            coro = pcc_gc_alloc(64, 20, 0)
            captures = pcc_gc_alloc(24, 2, 0)
            args = pcc_gc_alloc(24, 2, 0)
            result = pcc_gc_alloc(24, 2, 0)
            store_ptr(coro, 32, captures)
            store_ptr(coro, 40, args)
            store_ptr(coro, 48, result)
            store_ptr(root_slots, 0, coro)
            pcc_gc_frame_enter(frame_map, root_slots)
            print(pcc_gc_collect(0))
            print(load_i32(captures, 12) & 32)
            print(load_i32(args, 12) & 32)
            print(load_i32(result, 12) & 32)
            pcc_gc_frame_leave(root_slots)
            free(root_slots)
            free(frame_map)

        if __name__ == "__main__":
            main()
        """).lstrip())
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["0", "32", "32", "32"]


def test_generational_gc_remembered_set_promotes_young_child(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(f"""
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import load_i32, null, ptr_add, store_i32, store_i64

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
        pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)

        def main() -> None:
            pcc_gc_set_backend({BACKEND_GENERATIONAL})
            owner = pcc_gc_alloc(32, 7, 0)
            child = pcc_gc_alloc(24, 2, 0)
            store_i64(owner, 16, 1)
            store_i32(owner, 12, 256)
            pcc_gc_store_ptr(owner, ptr_add(owner, 24), child)
            print(load_i32(owner, 12) & 512)
            print(load_i32(child, 12) & 128)
            print(pcc_gc_step(2))
            print(load_i32(child, 12) & 256)
            print(load_i32(owner, 12) & 512)
            pcc_gc_store_ptr(owner, ptr_add(owner, 24), null())
            pcc_gc_release(child)
            pcc_gc_release(owner)

        if __name__ == "__main__":
            main()
        """).lstrip())
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == [
        "512", "128", "1", "256", "0",
    ]


def test_colored_relocating_gc_read_barrier_clears_candidate(tmp_path):
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "prog.py"
    exe = tmp_path / "prog.out"
    src.write_text(textwrap.dedent(f"""
        from pcc.extern import extern, c_int32, c_int64, c_ptr, c_void
        from pcc.unsafe import free, load_i32, malloc, null, store_ptr

        pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
        pcc_gc_release = extern("pcc_gc_release", (c_ptr,), c_void)
        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_select_relocation_set = extern("pcc_gc_select_relocation_set", (c_int64,), c_int64)
        pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
        pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)

        def main() -> None:
            pcc_gc_set_backend({BACKEND_COLORED_RELOCATING})
            obj = pcc_gc_alloc(24, 2, 0)
            slot = malloc(8)
            store_ptr(slot, 0, null())
            pcc_gc_store_ptr(null(), slot, obj)
            print(pcc_gc_select_relocation_set(1))
            print(load_i32(obj, 12) & 2048)
            pcc_gc_load_ptr(null(), slot)
            print(load_i32(obj, 12) & 2048)
            pcc_gc_store_ptr(null(), slot, null())
            pcc_gc_release(obj)
            free(slot)

        if __name__ == "__main__":
            main()
        """).lstrip())
    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["1", "2048", "0"]
