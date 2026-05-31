"""Phase 4c.7: pcc-Python port of py_obj_gc.c.

This mirrors the C runtime's default CPython-style backend: immediate
refcounting plus a side-table cycle collector for tracked containers.

PyGcNode layout (40 bytes):
    offset  0  obj       PyObject*
    offset  8  gc_refs   i64
    offset 16  reachable i32
    offset 24  prev      PyGcNode*
    offset 32  next      PyGcNode*
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_int32, c_void
from pcc.unsafe import (
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


py_decref = extern("py_decref", (c_ptr,), c_void)
py_dealloc_int = extern("py_dealloc_int", (c_ptr,), c_void)
py_dealloc_float = extern("py_dealloc_float", (c_ptr,), c_void)
py_dealloc_str = extern("py_dealloc_str", (c_ptr,), c_void)
py_dealloc_list = extern("py_dealloc_list", (c_ptr,), c_void)
py_dealloc_tuple = extern("py_dealloc_tuple", (c_ptr,), c_void)
py_dealloc_dict = extern("py_dealloc_dict", (c_ptr,), c_void)
py_dealloc_set = extern("py_dealloc_set", (c_ptr,), c_void)
py_dealloc_func = extern("py_dealloc_func", (c_ptr,), c_void)
py_class_dealloc = extern("py_class_dealloc", (c_ptr,), c_void)
py_instance_dealloc = extern("py_instance_dealloc", (c_ptr,), c_void)
py_dealloc_exc = extern("py_dealloc_exc", (c_ptr,), c_void)
py_dealloc_iter = extern("py_dealloc_iter", (c_ptr,), c_void)
py_dealloc_gen = extern("py_dealloc_gen", (c_ptr,), c_void)
py_dealloc_coroutine = extern("py_dealloc_coroutine", (c_ptr,), c_void)
py_dealloc_continuation = extern("py_dealloc_continuation", (c_ptr,), c_void)
py_dealloc_task = extern("py_dealloc_task", (c_ptr,), c_void)
py_dealloc_virtual_thread = extern("py_dealloc_virtual_thread", (c_ptr,), c_void)
py_dealloc_memoryview = extern("py_dealloc_memoryview", (c_ptr,), c_void)
py_dealloc_weakref = extern("py_dealloc_weakref", (c_ptr,), c_void)
py_dealloc_generic = extern("py_dealloc_generic", (c_ptr,), c_void)
py_user_del_dispatch = extern("py_user_del_dispatch", (c_ptr,), c_void)
py_weakref_invalidate = extern("py_weakref_invalidate", (c_ptr,), c_void)
pcc_gc_note_object_freeing = extern("pcc_gc_note_object_freeing", (c_ptr,), c_void)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_gc_trace_continuation_roots = extern("pcc_gc_trace_continuation_roots", (), c_int64)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)

py_gc_index_find = extern("py_gc_index_find", (c_ptr,), c_ptr)
py_gc_index_insert = extern("py_gc_index_insert", (c_ptr, c_ptr), c_int64)
py_gc_index_remove = extern("py_gc_index_remove", (c_ptr,), c_ptr)


def _inc_tracked_count(delta: int) -> None:
    slot = global_addr("py_gc_tracked_count")
    v: int = load_i32(slot, 0)
    store_i32(slot, 0, v + delta)


def _find_node(o):
    if ptr_is_null(o):
        return null()
    if is_tagged_int(o):
        return null()
    return py_gc_index_find(o)


def _unlink_node(n) -> None:
    if ptr_is_null(n):
        return
    prev = load_ptr(n, 24)
    next_node = load_ptr(n, 32)
    if ptr_is_null(prev) == 0:
        store_ptr(prev, 32, next_node)
    else:
        global_store_ptr("py_gc_head", next_node)
    if ptr_is_null(next_node) == 0:
        store_ptr(next_node, 24, prev)
    store_ptr(n, 24, null())
    store_ptr(n, 32, null())
    _inc_tracked_count(-1)


def _is_unreachable(o) -> int:
    n = _find_node(o)
    if ptr_is_null(n):
        return 0
    if load_i32(n, 16) == 0:
        return 1
    return 0


def _subtract_child(child) -> None:
    n = _find_node(child)
    if ptr_is_null(n) == 0:
        refs: int = load_i64(n, 8)
        store_i64(n, 8, refs - 1)


def _continuation_slots(o):
    chunk = load_ptr(o, 24)
    if ptr_is_null(chunk):
        return null()
    return load_ptr(chunk, 16)


def _continuation_slot_count(o) -> int:
    chunk = load_ptr(o, 24)
    if ptr_is_null(chunk):
        return 0
    return load_i64(chunk, 8)


def _visit_subtract(o) -> None:
    if ptr_is_null(o) or is_tagged_int(o):
        return
    tag: int = load_i32(o, 8)
    if tag == 5:                         # list
        length: int = load_i64(o, 16)
        items = load_ptr(o, 32)
        i: int = 0
        while i < length:
            _subtract_child(load_ptr(items, i * 8))
            i = i + 1
        return
    if tag == 7:                         # tuple
        length: int = load_i64(o, 16)
        i: int = 0
        while i < length:
            _subtract_child(load_ptr(o, 24 + i * 8))
            i = i + 1
        return
    if tag == 6:                         # dict
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                key = load_ptr(entries, off + 8)
                if ptr_is_null(key) == 0:
                    _subtract_child(key)
                    _subtract_child(load_ptr(entries, off + 16))
                i = i + 1
        return
    if tag == 8:                         # set
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            dummy = global_load_ptr("py_set_dummy")
            capacity: int = load_i64(o, 24)
            i: int = 0
            while i < capacity:
                key = load_ptr(entries, i * 16 + 8)
                if ptr_is_null(key) == 0:
                    if ptr_eq(key, dummy) == 0:
                        _subtract_child(key)
                i = i + 1
        return
    if tag == 9:                         # func
        _subtract_child(load_ptr(o, 24))
        return
    if tag == 14:                        # iter
        _subtract_child(load_ptr(o, 16))
        return
    if tag == 15:                        # gen
        _subtract_child(load_ptr(o, 24))
        _subtract_child(load_ptr(o, 48))
        return
    if tag == 20:                        # coroutine
        _subtract_child(load_ptr(o, 32))
        _subtract_child(load_ptr(o, 40))
        _subtract_child(load_ptr(o, 48))
        return
    if tag == 29:                        # continuation
        slots = _continuation_slots(o)
        count: int = _continuation_slot_count(o)
        i: int = 0
        while i < count:
            _subtract_child(load_ptr(slots, i * 8))
            i = i + 1
        return
    if tag == 28:                        # task
        _subtract_child(load_ptr(o, 16))
        _subtract_child(load_ptr(o, 24))
        _subtract_child(load_ptr(o, 32))
        return
    if tag == 30:                        # virtual thread
        _subtract_child(load_ptr(o, 16))
        _subtract_child(load_ptr(o, 24))
        return
    if tag == 12:                        # exception
        _subtract_child(load_ptr(o, 24))
        _subtract_child(load_ptr(o, 32))
        _subtract_child(load_ptr(o, 40))
        return
    if tag == 11 or tag >= 100:          # instance / user-tagged instance
        cls = load_ptr(o, 16)
        if ptr_is_null(cls) == 0:
            n_fields: int = load_i32(cls, 72)
            if n_fields < 0:
                n_fields = 0
            i: int = 0
            while i < n_fields:
                _subtract_child(load_ptr(o, 24 + i * 8))
                i = i + 1
            flags: int = load_i32(cls, 12)
            if (flags & 2) == 0:
                _subtract_child(load_ptr(o, 24 + n_fields * 8))


def _append_referent(out, child) -> None:
    if ptr_is_null(child):
        return
    py_list_append(out, child)


def _append_referents_to(o, out) -> None:
    if ptr_is_null(o) or is_tagged_int(o):
        return
    tag: int = load_i32(o, 8)
    if tag == 5:                         # list
        length: int = load_i64(o, 16)
        items = load_ptr(o, 32)
        i: int = 0
        while i < length:
            _append_referent(out, load_ptr(items, i * 8))
            i = i + 1
        return
    if tag == 7:                         # tuple
        length: int = load_i64(o, 16)
        i: int = 0
        while i < length:
            _append_referent(out, load_ptr(o, 24 + i * 8))
            i = i + 1
        return
    if tag == 6:                         # dict
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                key = load_ptr(entries, off + 8)
                if ptr_is_null(key) == 0:
                    _append_referent(out, key)
                    _append_referent(out, load_ptr(entries, off + 16))
                i = i + 1
        return
    if tag == 8:                         # set
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            dummy = global_load_ptr("py_set_dummy")
            capacity: int = load_i64(o, 24)
            i: int = 0
            while i < capacity:
                key = load_ptr(entries, i * 16 + 8)
                if ptr_is_null(key) == 0:
                    if ptr_eq(key, dummy) == 0:
                        _append_referent(out, key)
                i = i + 1
        return
    if tag == 9:                         # func
        _append_referent(out, load_ptr(o, 24))
        return
    if tag == 14:                        # iter
        _append_referent(out, load_ptr(o, 16))
        return
    if tag == 15:                        # gen
        _append_referent(out, load_ptr(o, 24))
        _append_referent(out, load_ptr(o, 48))
        return
    if tag == 20:                        # coroutine
        _append_referent(out, load_ptr(o, 32))
        _append_referent(out, load_ptr(o, 40))
        _append_referent(out, load_ptr(o, 48))
        return
    if tag == 29:                        # continuation
        slots = _continuation_slots(o)
        count: int = _continuation_slot_count(o)
        i: int = 0
        while i < count:
            _append_referent(out, load_ptr(slots, i * 8))
            i = i + 1
        return
    if tag == 28:                        # task
        _append_referent(out, load_ptr(o, 16))
        _append_referent(out, load_ptr(o, 24))
        _append_referent(out, load_ptr(o, 32))
        return
    if tag == 30:                        # virtual thread
        _append_referent(out, load_ptr(o, 16))
        _append_referent(out, load_ptr(o, 24))
        return
    if tag == 12:                        # exception
        _append_referent(out, load_ptr(o, 24))
        _append_referent(out, load_ptr(o, 32))
        _append_referent(out, load_ptr(o, 40))
        return
    if tag == 11 or tag >= 100:          # instance / user-tagged instance
        cls = load_ptr(o, 16)
        if ptr_is_null(cls) == 0:
            n_fields: int = load_i32(cls, 72)
            if n_fields < 0:
                n_fields = 0
            i: int = 0
            while i < n_fields:
                _append_referent(out, load_ptr(o, 24 + i * 8))
                i = i + 1
            flags: int = load_i32(cls, 12)
            if (flags & 2) == 0:
                _append_referent(out, load_ptr(o, 24 + n_fields * 8))


@c_abi_export("py_gc_get_referents")
def py_gc_get_referents(o):
    out = py_list_new(0)
    _append_referents_to(o, out)
    return out


def _list_has_identical_item(lst, target) -> int:
    length: int = load_i64(lst, 16)
    items = load_ptr(lst, 32)
    i: int = 0
    while i < length:
        if load_ptr(items, i * 8) == target:
            return 1
        i = i + 1
    return 0


@c_abi_export("py_gc_get_objects")
def py_gc_get_objects():
    tracked = load_i32(global_addr("py_gc_tracked_count"), 0)
    n = global_load_ptr("py_gc_head")
    out = py_list_new(tracked)
    while ptr_is_null(n) == 0:
        obj = load_ptr(n, 0)
        if ptr_is_null(obj) == 0:
            py_list_append(out, obj)
        n = load_ptr(n, 32)
    return out


@c_abi_export("py_gc_get_referrers")
def py_gc_get_referrers(target):
    n = global_load_ptr("py_gc_head")
    out = py_list_new(0)
    while ptr_is_null(n) == 0:
        obj = load_ptr(n, 0)
        if ptr_is_null(obj) == 0:
            refs = py_gc_get_referents(obj)
            if _list_has_identical_item(refs, target) != 0:
                py_list_append(out, obj)
            py_decref(refs)
        n = load_ptr(n, 32)
    return out


def _mark_reachable(o) -> None:
    n = _find_node(o)
    if ptr_is_null(n):
        return
    if load_i32(n, 16) != 0:
        return
    store_i32(n, 16, 1)
    _visit_mark(o)


def _mark_root_slots(root_slots, root_count: int) -> None:
    if ptr_is_null(root_slots) != 0:
        return
    if root_count < 0 or root_count > 100000:
        return
    i: int = 0
    while i < root_count:
        _mark_reachable(load_ptr(root_slots, i * 8))
        i = i + 1


def _mark_runtime_roots() -> None:
    n = global_load_ptr("py_gc_head")
    while ptr_is_null(n) == 0:
        obj = load_ptr(n, 0)
        if ptr_is_null(obj) == 0:
            if is_tagged_int(obj) == 0:
                flags: int = load_i32(obj, 12)
                if (flags & 64) != 0:
                    _mark_reachable(obj)
        n = load_ptr(n, 32)

    root_slots = global_load_ptr("pcc_gc_root_slots")
    if ptr_is_null(root_slots) == 0:
        _mark_root_slots(root_slots, load_i32(global_addr("pcc_gc_root_count"), 0))

    frame = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(frame) == 0:
        frame_map = load_ptr(frame, 0)
        slots = load_ptr(frame, 8)
        if ptr_is_null(frame_map) == 0:
            _mark_root_slots(slots, load_i32(frame_map, 0))
        frame = load_ptr(frame, 16)

    cont = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(cont) == 0:
        frame_map = load_ptr(cont, 0)
        slots = load_ptr(cont, 8)
        if ptr_is_null(frame_map) == 0:
            _mark_root_slots(slots, load_i32(frame_map, 0))
        cont = load_ptr(cont, 16)

    pcc_gc_trace_continuation_roots()

    sched = global_load_ptr("pcc_gc_scheduler_root_head")
    while ptr_is_null(sched) == 0:
        slot = load_ptr(sched, 0)
        if ptr_is_null(slot) == 0:
            _mark_reachable(load_ptr(slot, 0))
        sched = load_ptr(sched, 8)


def _recompute_reachability() -> None:
    n = global_load_ptr("py_gc_head")
    while ptr_is_null(n) == 0:
        obj = load_ptr(n, 0)
        store_i64(n, 8, load_i64(obj, 0))
        store_i32(n, 16, 0)
        n = load_ptr(n, 32)

    n = global_load_ptr("py_gc_head")
    while ptr_is_null(n) == 0:
        _visit_subtract(load_ptr(n, 0))
        n = load_ptr(n, 32)

    n = global_load_ptr("py_gc_head")
    while ptr_is_null(n) == 0:
        if load_i64(n, 8) > 0:
            _mark_reachable(load_ptr(n, 0))
        n = load_ptr(n, 32)
    _mark_runtime_roots()


def _maybe_finalize_unreachable(unreachable, count: int) -> int:
    finalized: int = 0
    i: int = 0
    while i < count:
        node = load_ptr(unreachable, i * 8)
        obj = load_ptr(node, 0)
        if ptr_is_null(obj) == 0:
            if is_tagged_int(obj) == 0:
                tag: int = load_i32(obj, 8)
                if tag == 11 or tag >= 100:
                    flags_before: int = load_i32(obj, 12)
                    py_user_del_dispatch(obj)
                    flags_after: int = load_i32(obj, 12)
                    if (flags_before & 4) == 0:
                        if (flags_after & 4) != 0:
                            finalized = 1
        i = i + 1
    return finalized


def _visit_mark(o) -> None:
    if ptr_is_null(o) or is_tagged_int(o):
        return
    tag: int = load_i32(o, 8)
    if tag == 5:
        length: int = load_i64(o, 16)
        items = load_ptr(o, 32)
        i: int = 0
        while i < length:
            _mark_reachable(load_ptr(items, i * 8))
            i = i + 1
        return
    if tag == 7:
        length: int = load_i64(o, 16)
        i: int = 0
        while i < length:
            _mark_reachable(load_ptr(o, 24 + i * 8))
            i = i + 1
        return
    if tag == 6:
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                key = load_ptr(entries, off + 8)
                if ptr_is_null(key) == 0:
                    _mark_reachable(key)
                    _mark_reachable(load_ptr(entries, off + 16))
                i = i + 1
        return
    if tag == 8:
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            dummy = global_load_ptr("py_set_dummy")
            capacity: int = load_i64(o, 24)
            i: int = 0
            while i < capacity:
                key = load_ptr(entries, i * 16 + 8)
                if ptr_is_null(key) == 0:
                    if ptr_eq(key, dummy) == 0:
                        _mark_reachable(key)
                i = i + 1
        return
    if tag == 9:
        _mark_reachable(load_ptr(o, 24))
        return
    if tag == 14:
        _mark_reachable(load_ptr(o, 16))
        return
    if tag == 15:
        _mark_reachable(load_ptr(o, 24))
        _mark_reachable(load_ptr(o, 48))
        return
    if tag == 20:
        _mark_reachable(load_ptr(o, 32))
        _mark_reachable(load_ptr(o, 40))
        _mark_reachable(load_ptr(o, 48))
        return
    if tag == 29:
        slots = _continuation_slots(o)
        count: int = _continuation_slot_count(o)
        i: int = 0
        while i < count:
            _mark_reachable(load_ptr(slots, i * 8))
            i = i + 1
        return
    if tag == 28:
        _mark_reachable(load_ptr(o, 16))
        _mark_reachable(load_ptr(o, 24))
        _mark_reachable(load_ptr(o, 32))
        return
    if tag == 30:
        _mark_reachable(load_ptr(o, 16))
        _mark_reachable(load_ptr(o, 24))
        return
    if tag == 12:
        _mark_reachable(load_ptr(o, 24))
        _mark_reachable(load_ptr(o, 32))
        _mark_reachable(load_ptr(o, 40))
        return
    if tag == 11 or tag >= 100:
        cls = load_ptr(o, 16)
        if ptr_is_null(cls) == 0:
            n_fields: int = load_i32(cls, 72)
            if n_fields < 0:
                n_fields = 0
            i: int = 0
            while i < n_fields:
                _mark_reachable(load_ptr(o, 24 + i * 8))
                i = i + 1
            flags: int = load_i32(cls, 12)
            if (flags & 2) == 0:
                _mark_reachable(load_ptr(o, 24 + n_fields * 8))


def _clear_slot(slot) -> None:
    child = load_ptr(slot, 0)
    store_ptr(slot, 0, null())
    if ptr_is_null(child):
        return
    if _is_unreachable(child) != 0:
        return
    py_decref(child)


def _clear_referents(o) -> None:
    if ptr_is_null(o) or is_tagged_int(o):
        return
    tag: int = load_i32(o, 8)
    if tag == 5:
        length: int = load_i64(o, 16)
        items = load_ptr(o, 32)
        i: int = 0
        while i < length:
            _clear_slot(ptr_add(items, i * 8))
            i = i + 1
        store_i64(o, 16, 0)
        return
    if tag == 7:
        length: int = load_i64(o, 16)
        i: int = 0
        while i < length:
            _clear_slot(ptr_add(o, 24 + i * 8))
            i = i + 1
        store_i64(o, 16, 0)
        return
    if tag == 6:
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                key_slot = ptr_add(entries, off + 8)
                key = load_ptr(key_slot, 0)
                if ptr_is_null(key) == 0:
                    _clear_slot(key_slot)
                    _clear_slot(ptr_add(entries, off + 16))
                    store_i64(entries, off, 0)
                i = i + 1
        indices = load_ptr(o, 32)
        if ptr_is_null(indices) == 0:
            capacity: int = load_i64(o, 24)
            j: int = 0
            while j < capacity:
                store_i64(indices, j * 8, -1)
                j = j + 1
        store_i64(o, 16, 0)
        store_i64(o, 48, 0)
        return
    if tag == 8:
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            dummy = global_load_ptr("py_set_dummy")
            capacity: int = load_i64(o, 24)
            i: int = 0
            while i < capacity:
                key_slot = ptr_add(entries, i * 16 + 8)
                key = load_ptr(key_slot, 0)
                if ptr_is_null(key) == 0:
                    store_ptr(key_slot, 0, null())
                    if ptr_eq(key, dummy) == 0:
                        if _is_unreachable(key) == 0:
                            py_decref(key)
                    store_i64(entries, i * 16, 0)
                i = i + 1
        store_i64(o, 16, 0)
        store_i64(o, 32, 0)
        return
    if tag == 9:
        _clear_slot(ptr_add(o, 24))
        return
    if tag == 14:
        _clear_slot(ptr_add(o, 16))
        return
    if tag == 15:
        _clear_slot(ptr_add(o, 24))
        _clear_slot(ptr_add(o, 48))
        return
    if tag == 20:
        _clear_slot(ptr_add(o, 32))
        _clear_slot(ptr_add(o, 40))
        _clear_slot(ptr_add(o, 48))
        return
    if tag == 29:
        slots = _continuation_slots(o)
        count: int = _continuation_slot_count(o)
        i: int = 0
        while i < count:
            _clear_slot(ptr_add(slots, i * 8))
            i = i + 1
        return
    if tag == 28:
        _clear_slot(ptr_add(o, 16))
        _clear_slot(ptr_add(o, 24))
        _clear_slot(ptr_add(o, 32))
        return
    if tag == 30:
        _clear_slot(ptr_add(o, 16))
        _clear_slot(ptr_add(o, 24))
        return
    if tag == 12:
        _clear_slot(ptr_add(o, 24))
        _clear_slot(ptr_add(o, 32))
        _clear_slot(ptr_add(o, 40))
        return
    if tag == 11 or tag >= 100:
        cls = load_ptr(o, 16)
        if ptr_is_null(cls) == 0:
            n_fields: int = load_i32(cls, 72)
            if n_fields < 0:
                n_fields = 0
            i: int = 0
            while i < n_fields:
                _clear_slot(ptr_add(o, 24 + i * 8))
                i = i + 1
            flags: int = load_i32(cls, 12)
            if (flags & 2) == 0:
                _clear_slot(ptr_add(o, 24 + n_fields * 8))


def _dealloc_unreachable(o) -> None:
    tag: int = load_i32(o, 8)
    if tag == 2:
        py_dealloc_int(o)
    elif tag == 3:
        py_dealloc_float(o)
    elif tag == 4:
        py_dealloc_str(o)
    elif tag == 5:
        py_dealloc_list(o)
    elif tag == 7:
        py_dealloc_tuple(o)
    elif tag == 6:
        py_dealloc_dict(o)
    elif tag == 8:
        py_dealloc_set(o)
    elif tag == 9:
        py_dealloc_func(o)
    elif tag == 10:
        py_class_dealloc(o)
    elif tag == 11:
        py_instance_dealloc(o)
    elif tag == 12:
        py_dealloc_exc(o)
    elif tag == 14:
        py_dealloc_iter(o)
    elif tag == 15:
        py_dealloc_gen(o)
    elif tag == 20:
        py_dealloc_coroutine(o)
    elif tag == 29:
        py_dealloc_continuation(o)
    elif tag == 28:
        py_dealloc_task(o)
    elif tag == 30:
        py_dealloc_virtual_thread(o)
    elif tag == 19:
        py_dealloc_memoryview(o)
    elif tag == 21:
        py_dealloc_weakref(o)
    elif tag >= 100:
        py_instance_dealloc(o)
    else:
        py_dealloc_generic(o)


@c_abi_export("py_gc_init")
def py_gc_init() -> None:
    store_i32(global_addr("py_gc_enabled"), 0, 1)
    return


@c_abi_export("py_gc_collect")
def py_gc_collect() -> int:
    collecting_slot = global_addr("py_gc_collecting")
    if load_i32(collecting_slot, 0) != 0:
        return 0
    if pcc_stop_the_world() != 0:
        return 0
    store_i32(collecting_slot, 0, 1)

    tracked: int = load_i32(global_addr("py_gc_tracked_count"), 0)
    if tracked <= 0:
        store_i32(collecting_slot, 0, 0)
        pcc_resume_world()
        return 0

    unreachable = malloc(tracked * 8)
    if ptr_is_null(unreachable):
        store_i32(collecting_slot, 0, 0)
        pcc_resume_world()
        return 0

    _recompute_reachability()

    count: int = 0
    n = global_load_ptr("py_gc_head")
    while ptr_is_null(n) == 0:
        if load_i32(n, 16) == 0:
            # raw refcount 0 => owned by an in-flight py_decref (a thread parked
            # between rc->0 and py_gc_untrack still has it tracked). The refcount
            # path frees it; collecting it here double-frees under threaded
            # explicit gc.collect(). Genuine cycle garbage always has
            # refcount > 0. Mirror of the C runtime guard in py_obj_gc.c.
            obj_n = load_ptr(n, 0)
            if load_i64(obj_n, 0) > 0:
                store_ptr(unreachable, count * 8, n)
                count = count + 1
        n = load_ptr(n, 32)

    if _maybe_finalize_unreachable(unreachable, count) != 0:
        _recompute_reachability()

    i: int = 0
    while i < count:
        node = load_ptr(unreachable, i * 8)
        if load_i32(node, 16) == 0:
            obj = load_ptr(node, 0)
            py_weakref_invalidate(obj)
            _clear_referents(obj)
        i = i + 1

    i = 0
    collected: int = 0
    while i < count:
        node = load_ptr(unreachable, i * 8)
        if load_i32(node, 16) == 0:
            obj = load_ptr(node, 0)
            _unlink_node(node)
            py_gc_index_remove(obj)
            flags: int = load_i32(obj, 12)
            store_i32(obj, 12, flags & ~2)
            store_i64(obj, 0, 0)
            free(node)
            pcc_gc_note_object_freeing(obj)
            _dealloc_unreachable(obj)
            collected = collected + 1
        i = i + 1

    free(unreachable)
    store_i32(collecting_slot, 0, 0)
    pcc_resume_world()
    return collected


@c_abi_export("py_gc_track")
def py_gc_track(o) -> None:
    if ptr_is_null(o):
        return
    if is_tagged_int(o):
        return
    if pcc_gc_backend() == 4 and pcc_threads_enabled() != 0:
        return
    flags: int = load_i32(o, 12)
    if (flags & 2) != 0:
        return
    n = malloc(40)
    if ptr_is_null(n):
        return
    inserted = py_gc_index_insert(o, n)
    if inserted == 0:
        free(n)
        flags = flags | 2
        store_i32(o, 12, flags)    # |= PY_FLAG_GC_TRACKED
        return
    if inserted < 0:
        free(n)
        return
    store_ptr(n, 0, o)
    store_i64(n, 8, 0)
    store_i32(n, 16, 0)
    store_ptr(n, 24, null())
    head = global_load_ptr("py_gc_head")
    store_ptr(n, 32, head)
    if ptr_is_null(head) == 0:
        store_ptr(head, 24, n)
    global_store_ptr("py_gc_head", n)
    _inc_tracked_count(1)
    store_i32(o, 12, flags | 2)     # |= PY_FLAG_GC_TRACKED


@c_abi_export("py_gc_untrack")
def py_gc_untrack(o) -> None:
    if ptr_is_null(o):
        return
    if is_tagged_int(o):
        return
    if pcc_gc_backend() == 4 and pcc_threads_enabled() != 0:
        return
    flags: int = load_i32(o, 12)
    if (flags & 2) == 0:
        return
    n = py_gc_index_remove(o)
    if ptr_is_null(n) == 0:
        _unlink_node(n)
        free(n)
    store_i32(o, 12, flags & ~2)    # &= ~PY_FLAG_GC_TRACKED


@c_abi_export("py_gc_enable")
def py_gc_enable() -> None:
    store_i32(global_addr("py_gc_enabled"), 0, 1)


@c_abi_export("py_gc_disable")
def py_gc_disable() -> None:
    store_i32(global_addr("py_gc_enabled"), 0, 0)


@c_abi_export("py_gc_is_enabled")
def py_gc_is_enabled() -> int:
    if load_i32(global_addr("py_gc_enabled"), 0) != 0:
        return 1
    return 0


@c_abi_export("py_gc_is_tracked")
def py_gc_is_tracked(o) -> int:
    if ptr_is_null(o):
        return 0
    if is_tagged_int(o):
        return 0
    flags: int = load_i32(o, 12)
    if (flags & 2) != 0:
        return 1
    return 0


@c_abi_export("py_gc_get_count")
def py_gc_get_count(generation: int) -> int:
    if generation == 0:
        return load_i32(global_addr("py_gc_tracked_count"), 0)
    return 0


@c_abi_export("py_gc_get_threshold")
def py_gc_get_threshold(generation: int) -> int:
    if generation == 0:
        return load_i32(global_addr("py_gc_threshold0"), 0)
    if generation == 1:
        return load_i32(global_addr("py_gc_threshold1"), 0)
    if generation == 2:
        return load_i32(global_addr("py_gc_threshold2"), 0)
    return 0


@c_abi_export("py_gc_set_threshold")
def py_gc_set_threshold(gen0: int, gen1: int, gen2: int) -> None:
    if gen0 >= 0:
        store_i32(global_addr("py_gc_threshold0"), 0, gen0)
    if gen1 >= 0:
        store_i32(global_addr("py_gc_threshold1"), 0, gen1)
    if gen2 >= 0:
        store_i32(global_addr("py_gc_threshold2"), 0, gen2)


@c_abi_export("py_gc_freeze")
def py_gc_freeze() -> None:
    tracked: int = load_i32(global_addr("py_gc_tracked_count"), 0)
    if tracked <= 0:
        tracked = 1
    store_i32(global_addr("py_gc_freeze_count"), 0, tracked)


@c_abi_export("py_gc_unfreeze")
def py_gc_unfreeze() -> None:
    store_i32(global_addr("py_gc_freeze_count"), 0, 0)


@c_abi_export("py_gc_get_freeze_count")
def py_gc_get_freeze_count() -> int:
    return load_i32(global_addr("py_gc_freeze_count"), 0)
