from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_SRC = REPO_ROOT / "pcc" / "py_runtime" / "src"
RUNTIME_PY = REPO_ROOT / "pcc" / "py_runtime" / "py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_runtime_store_sites_route_through_gc_store_ptr():
    """Guard the concrete heap-store helpers codegen emits calls to.

    pcc's Python frontend mostly lowers container / instance writes through
    runtime helpers.  Those helpers are the hot PyObject* store sites that must
    feed non-refcount backends.  This test rejects the old raw incref/store /
    decref pattern at the main helper boundaries.
    """
    expectations = {
        RUNTIME_SRC / "py_list.c": [
            "pcc_gc_store_ptr(lst, &l->items[l->length], item)",
            "pcc_gc_store_ptr(lst, &l->items[idx], item)",
        ],
        RUNTIME_SRC / "py_tuple.c": [
            "pcc_gc_store_ptr(tuple, &t->items[i], item)",
        ],
        RUNTIME_SRC / "py_dict.c": [
            "pcc_gc_store_ptr((PyObject *)d, &e->key, key)",
            "pcc_gc_store_ptr((PyObject *)d, &e->value, value)",
            "pcc_gc_store_ptr(dict, &e->value, value)",
        ],
        RUNTIME_SRC / "py_set.c": [
            "pcc_gc_store_ptr(set, &e->key, item)",
        ],
        RUNTIME_SRC / "py_class.c": [
            "pcc_gc_store_ptr((PyObject *)inst, &inst->fields[idx], value)",
            "pcc_gc_store_ptr((PyObject *)inst, dyn_slot, dyn)",
        ],
        RUNTIME_SRC / "py_func.c": [
            "pcc_gc_store_ptr((PyObject *)f, &f->captures",
        ],
    }
    for path, needles in expectations.items():
        src = _read(path)
        for needle in needles:
            assert needle in src, f"{path} missing {needle!r}"


def test_pcc_python_ports_mirror_gc_store_ptr_paths():
    expectations = {
        RUNTIME_PY / "py_list.py": [
            "pcc_gc_store_ptr(lst, ptr_add(items, length * 8), item)",
            "pcc_gc_store_ptr(lst, ptr_add(items, idx * 8), item)",
        ],
        RUNTIME_PY / "py_tuple.py": [
            "pcc_gc_store_ptr(tuple_ptr, ptr_add(tuple_ptr, slot_offset), item)",
        ],
        RUNTIME_PY / "py_dict.py": [
            "pcc_gc_store_ptr(d, ptr_add(entries, entry_off + 8), key)",
            "pcc_gc_store_ptr(d, ptr_add(entries, entry_off + 16), value)",
        ],
        RUNTIME_PY / "py_set.py": [
            "pcc_gc_store_ptr(s, ptr_add(entries, slot_off + 8), item)",
        ],
        RUNTIME_PY / "py_class.py": [
            "pcc_gc_store_ptr(inst, ptr_add(fields_base, idx * 8), value)",
            "pcc_gc_store_ptr(inst, dyn_slot, dyn)",
        ],
        RUNTIME_PY / "py_func.py": [
            "pcc_gc_store_ptr(fn, ptr_add(fn, 24), captures)",
        ],
    }
    for path, needles in expectations.items():
        src = _read(path)
        assert 'extern("pcc_gc_store_ptr"' in src
        for needle in needles:
            assert needle in src, f"{path} missing {needle!r}"


def test_generated_heap_stores_increment_barrier_counter_only_for_tracing_backend(tmp_path):
    """Compile real Python code and observe runtime barrier telemetry.

    backend 0 must remain the refcount-cycle fast path: store-barrier counter
    stays at zero.  backend 1 must route the same generated list/dict/instance
    stores through the GC store path and increment the counter.
    """
    from pcc.py_frontend.pipeline import compile_python

    src = tmp_path / "barrier_prog.py"
    exe = tmp_path / "barrier_prog.out"
    src.write_text(textwrap.dedent("""
        from pcc.extern import extern, c_int64, c_ptr, c_void

        pcc_gc_set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
        pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
        pcc_gc_telemetry_reset = extern("pcc_gc_telemetry_reset", (), c_void)
        py_list_new = extern("py_list_new", (c_int64,), c_ptr)
        py_set_new = extern("py_set_new", (), c_ptr)
        py_set_add = extern("py_set_add", (c_ptr, c_ptr), c_void)
        py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
        py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)

        class Box:
            pass

        def exercise() -> None:
            xs = []
            xs.append([])
            xs[0] = []
            d = {}
            d["value"] = []
            b = Box()
            b.payload = []

            # Direct runtime calls cover set and tuple construction, which are
            # not always emitted by the frontend for minimal syntax programs.
            value = py_list_new(0)
            s = py_set_new()
            py_set_add(s, value)
            t = py_tuple_new(1)
            py_tuple_set_item(t, 0, value)

        def run_backend(backend: int) -> int:
            pcc_gc_set_backend(backend)
            pcc_gc_telemetry_reset()
            exercise()
            return pcc_gc_telemetry(1)  # PCC_GC_COUNTER_WRITE_BARRIERS

        def main() -> None:
            print(run_backend(0))
            print(run_backend(1) > 0)

        if __name__ == "__main__":
            main()
        """).lstrip())

    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["0", "True"]

