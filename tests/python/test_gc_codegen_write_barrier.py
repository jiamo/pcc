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
            "pcc_gc_store_ptr(lst, &l->items[i], right)",
            "pcc_gc_store_ptr(lst, &l->items[j], left)",
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


def test_capi_internal_owner_slots_follow_gc_slot_contract():
    """Every shim-owned PyObject field must trace, release, store, and load."""
    src = _read(RUNTIME_SRC / "py_capi_shim.c")

    type_expectations = {
        "pcc_capi_contextvar_type": (
            "pcc_capi_contextvar_traverse",
            "pcc_capi_contextvar_dealloc",
        ),
        "pcc_capi_seqiter_type": (
            "pcc_capi_seqiter_traverse",
            "pcc_capi_seqiter_dealloc",
        ),
        "pcc_capi_slice_obj_type": (
            "pcc_capi_slice_traverse",
            "pcc_capi_slice_dealloc",
        ),
    }
    for type_name, (traverse, dealloc) in type_expectations.items():
        block = src.split(f"static PyTypeObject {type_name} =", 1)[1]
        block = block.split("};", 1)[0]
        assert "Py_TPFLAGS_HAVE_GC" in block
        assert "PCC_TPFLAGS_MANAGED_DEALLOC" in block
        assert f".tp_traverse = {traverse}" in block
        assert f".tp_dealloc = {dealloc}" in block

    for needle in (
        "pcc_capi_visit_slot(&cv->def, visit, arg)",
        "pcc_capi_visit_slot(&cv->value, visit, arg)",
        "pcc_gc_store_ptr(obj, &cv->def, def)",
        "pcc_gc_load_ptr(var, &cv->value)",
        "pcc_gc_load_ptr(var, &cv->def)",
        "pcc_gc_note_slot_write_barrier(var, &cv->value, value)",
        "pcc_gc_store_ptr(self, &cv->value, previous)",
        "pcc_capi_visit_slot(&it->seq, visit, arg)",
        "pcc_gc_store_ptr(obj, &it->seq, seq)",
        "pcc_gc_load_ptr(obj, &it->seq)",
        "pcc_capi_visit_slot(&slice->start, visit, arg)",
        "pcc_capi_visit_slot(&slice->stop, visit, arg)",
        "pcc_capi_visit_slot(&slice->step, visit, arg)",
        "pcc_gc_store_ptr(obj, &s->start, start)",
        "pcc_gc_store_ptr(obj, &s->stop, stop)",
        "pcc_gc_store_ptr(obj, &s->step, step)",
        "pcc_gc_load_ptr(r, &s->start)",
        "pcc_gc_load_ptr(r, &s->stop)",
        "pcc_gc_load_ptr(r, &s->step)",
    ):
        assert needle in src, f"py_capi_shim.c missing {needle!r}"


def test_tls_exception_accessors_heal_forwarded_owner_reference():
    c_src = _read(RUNTIME_SRC / "py_exc_tls.c")
    helper = c_src.split(
        "static PyObject *py_resolve_current_exception(void)", 1
    )[1].split("void py_raise(PyObject *exc)", 1)[0]
    for needle in (
        "pcc_gc_note_relocation_read(cur)",
        "py_incref(resolved)",
        "py_tls_exc_set(resolved)",
        "py_decref(cur)",
    ):
        assert needle in helper
    assert "PyObject *cur = py_resolve_current_exception();" in c_src
    assert "return py_resolve_current_exception();" in c_src

    py_src = _read(RUNTIME_PY / "py_exc_tls.py")
    helper = py_src.split("def _resolve_current_exception():", 1)[1].split(
        '@c_abi_export("py_current_exception")', 1
    )[0]
    for needle in (
        "pcc_gc_note_relocation_read(cur)",
        "py_incref(resolved)",
        "py_tls_exc_set(resolved)",
        "py_decref(cur)",
    ):
        assert needle in helper
    assert py_src.count("cur = _resolve_current_exception()") == 2
    assert "return _resolve_current_exception()" in py_src

    c_traceback = _read(RUNTIME_SRC / "py_exc_traceback.c")
    assert c_traceback.count("saved_exc = py_current_exception()") == 2
    c_context = _read(RUNTIME_SRC / "py_context.c")
    assert "stashed = py_current_exception()" in c_context
    py_traceback = _read(RUNTIME_PY / "py_exc_traceback.py")
    assert py_traceback.count("saved_exc = py_current_exception()") == 2


def test_pcc_python_ports_mirror_gc_store_ptr_paths():
    expectations = {
        RUNTIME_PY / "py_list.py": [
            "pcc_gc_store_ptr(lst, ptr_add(items, length * 8), item)",
            "pcc_gc_store_ptr(lst, ptr_add(items, idx * 8), item)",
            "pcc_gc_store_ptr(lst, ptr_add(items, i * 8), right)",
            "pcc_gc_store_ptr(lst, ptr_add(items, j * 8), left)",
        ],
        RUNTIME_PY / "py_tuple.py": [
            "pcc_gc_store_ptr(tuple_ptr, slot, item)",
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
            "pcc_gc_store_ptr(fn, ptr_add(fn, 64), captures)",
        ],
    }
    for path, needles in expectations.items():
        src = _read(path)
        assert 'extern("pcc_gc_store_ptr"' in src
        for needle in needles:
            assert needle in src, f"{path} missing {needle!r}"


def test_list_reverse_retains_borrowed_items_across_barrier_swap():
    c_src = _read(RUNTIME_SRC / "py_list.c")
    c_block = c_src.split("void py_list_reverse(PyObject *lst)", 1)[1].split("i++;", 1)[0]
    c_order = [
        "PyObject *left = pcc_gc_load_ptr(lst, &l->items[i]);",
        "PyObject *right = pcc_gc_load_ptr(lst, &l->items[j]);",
        "py_incref(left);",
        "py_incref(right);",
        "pcc_gc_store_ptr(lst, &l->items[i], right);",
        "pcc_gc_store_ptr(lst, &l->items[j], left);",
        "py_decref(left);",
        "py_decref(right);",
    ]
    pos = -1
    for needle in c_order:
        next_pos = c_block.find(needle)
        assert next_pos > pos, f"py_list.c reverse swap order missing/out of order: {needle!r}"
        pos = next_pos

    py_src = _read(RUNTIME_PY / "py_list.py")
    py_block = py_src.split("def py_list_reverse(lst) -> None:", 1)[1].split("i = i + 1", 1)[0]
    py_order = [
        "left = pcc_gc_load_ptr(lst, ptr_add(items, i * 8))",
        "right = pcc_gc_load_ptr(lst, ptr_add(items, j * 8))",
        "py_incref(left)",
        "py_incref(right)",
        "pcc_gc_store_ptr(lst, ptr_add(items, i * 8), right)",
        "pcc_gc_store_ptr(lst, ptr_add(items, j * 8), left)",
        "py_decref(left)",
        "py_decref(right)",
    ]
    pos = -1
    for needle in py_order:
        next_pos = py_block.find(needle)
        assert next_pos > pos, f"py_list.py reverse swap order missing/out of order: {needle!r}"
        pos = next_pos


def test_hash_rehash_move_stores_route_through_slot_write_barrier():
    """Hash-table rehash moves owned element pointers to a fresh table.

    A rehash relocates each live key (dict also its value) from the old
    backing array into a newly allocated one via a raw move-store (no
    incref/decref, since the reference is moved not copied).  That raw store
    still has to notify the collector's slot barrier so backend #3's
    generational remembered set and backend #4's relocation slot tracking
    observe the *new* slot address; otherwise a live young key reachable only
    through the moved slot can be missed by a minor/relocating collection.

    py_dict_rehash already decomposes the move as ``raw store`` +
    ``pcc_gc_note_slot_write_barrier``.  py_set_rehash is its sibling and must
    do the same; this guards both the C runtime and the pcc-Python ports so
    the mirrors do not drift.
    """
    dict_c = _read(RUNTIME_SRC / "py_dict.c")
    for needle in (
        "pcc_gc_note_slot_write_barrier((PyObject *)d, &ne->key, entry_key)",
        "pcc_gc_note_slot_write_barrier((PyObject *)d, &ne->value, entry_value)",
    ):
        assert needle in dict_c, f"py_dict.c rehash missing {needle!r}"

    set_c = _read(RUNTIME_SRC / "py_set.c")
    set_c_needle = (
        "pcc_gc_note_slot_write_barrier((PyObject *)s, &s->entries[slot].key, k)"
    )
    assert set_c_needle in set_c, f"py_set.c rehash missing {set_c_needle!r}"

    dict_py = _read(RUNTIME_PY / "py_dict.py")
    for needle in (
        "pcc_gc_note_slot_write_barrier(d, ptr_add(new_entries, new_off + 8), k)",
        "pcc_gc_note_slot_write_barrier(d, ptr_add(new_entries, new_off + 16), v)",
    ):
        assert needle in dict_py, f"py_dict.py _rehash missing {needle!r}"

    set_py = _read(RUNTIME_PY / "py_set.py")
    assert '"pcc_gc_note_slot_write_barrier"' in set_py, (
        "py_set.py must extern pcc_gc_note_slot_write_barrier"
    )
    set_py_needle = (
        "pcc_gc_note_slot_write_barrier(s, ptr_add(new_entries, dest_off + 8), k)"
    )
    assert set_py_needle in set_py, f"py_set.py _rehash missing {set_py_needle!r}"


def test_list_extend_element_store_matches_append_slot_write_barrier():
    """list.extend must route grown-slot element stores through the barrier.

    ``py_list_append`` writes each appended element into freshly grown capacity
    by NULL-initing the slot (``py_list_new``/grow leave ``items[]`` unzeroed,
    so the slot holds garbage) and then calling ``pcc_gc_store_ptr`` — which
    internally emits ``pcc_gc_note_slot_write_barrier`` and does incref-new /
    decref-old.  ``py_list_extend``'s fast paths (list source and tuple source)
    used to bypass that with ``py_incref(v)`` + a raw ``items[len++] = v`` store,
    so backend #3's remembered set / backend #4's relocation slot tracking never
    observed the new slot — an asymmetry with its own sibling ``py_list_append``.

    This guards that both extend fast paths now use append's exact idiom
    (NULL-init the fresh slot, then barrier-store, no separate ``py_incref`` so
    the net accounting stays +1 owned ref) in both the C runtime and the
    pcc-Python port, and that the old raw store is gone.  The iterator fallback
    branch already delegates to ``py_list_append`` and is unaffected.
    """
    # --- C runtime: py_list.c -------------------------------------------
    c_src = _read(RUNTIME_SRC / "py_list.c")

    # The template sibling must still route append's grown-slot store through
    # the barrier helper (this is the idiom extend copies).
    assert "l->items[l->length] = NULL;" in c_src
    assert "pcc_gc_store_ptr(lst, &l->items[l->length], item);" in c_src

    c_extend = c_src.split("void py_list_extend(PyObject *a, PyObject *b)", 1)[1]
    c_extend = c_extend.split("void py_list_insert", 1)[0]

    # Both fast paths (list + tuple source) NULL-init then barrier-store.
    assert c_extend.count("la->items[la->length] = NULL;") == 2, (
        "py_list_extend must NULL-init the fresh slot in both fast paths"
    )
    assert c_extend.count("pcc_gc_store_ptr(a, &la->items[la->length], v);") == 2, (
        "py_list_extend must barrier-store in both fast paths"
    )
    # The old raw store (and its double-incref) must be gone.
    assert "la->items[la->length++] = v;" not in c_extend, (
        "py_list_extend still has the raw, unbarriered element store"
    )
    assert "py_incref(v);" not in c_extend, (
        "py_list_extend must not py_incref(v) once pcc_gc_store_ptr increfs it"
    )

    # --- pcc-Python port: py_list.py ------------------------------------
    py_src = _read(RUNTIME_PY / "py_list.py")
    assert 'extern("pcc_gc_store_ptr"' in py_src

    # Template sibling (append) idiom in the port.
    assert "store_ptr(items, length * 8, null())" in py_src
    assert "pcc_gc_store_ptr(lst, ptr_add(items, length * 8), item)" in py_src

    py_extend = py_src.split("def py_list_extend(a, b) -> None:", 1)[1]
    py_extend = py_extend.split("def py_list_insert", 1)[0]

    assert py_extend.count("store_ptr(a_items, (la + i) * 8, null())") == 2, (
        "py_list.py extend must NULL-init the fresh slot in both fast paths"
    )
    assert py_extend.count(
        "pcc_gc_store_ptr(a, ptr_add(a_items, (la + i) * 8), v)"
    ) == 2, "py_list.py extend must barrier-store in both fast paths"
    assert "store_ptr(a_items, (la + i) * 8, v)" not in py_extend, (
        "py_list.py extend still has the raw, unbarriered element store"
    )
    assert "py_incref(v)" not in py_extend, (
        "py_list.py extend must not py_incref(v) once pcc_gc_store_ptr increfs it"
    )


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
        """).lstrip(), encoding="utf-8")

    compile_python(
        str(src),
        str(exe),
        ir_scaffold_mode="on",
        libpython_mode="off",
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["0", "True"]
