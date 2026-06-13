"""pcc-Python port of py_obj_dealloc.c.

Type-specific object deallocators. The refcount dispatch in py_obj.py
still calls these symbols by name; the pcc-Python runtime archive
replaces the C object with this module while preserving the ABI.
"""
from pcc.extern import extern, c_abi_export, c_int64, c_ptr, c_void
from pcc.unsafe import (
    define_global_i32,
    define_global_ptr_null,
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
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
py_dealloc_func = extern("py_dealloc_func", (c_ptr,), c_void)
py_dealloc_iter = extern("py_dealloc_iter", (c_ptr,), c_void)
py_dealloc_gen = extern("py_dealloc_gen", (c_ptr,), c_void)
py_dealloc_coroutine = extern("py_dealloc_coroutine", (c_ptr,), c_void)
py_dealloc_continuation = extern("py_dealloc_continuation", (c_ptr,), c_void)
py_dealloc_task = extern("py_dealloc_task", (c_ptr,), c_void)
py_dealloc_memoryview = extern("py_dealloc_memoryview", (c_ptr,), c_void)
py_dealloc_weakref = extern("py_dealloc_weakref", (c_ptr,), c_void)
py_file_close = extern("py_file_close", (c_ptr,), c_void)
py_dealloc_thread_lock = extern("py_dealloc_thread_lock", (c_ptr,), c_void)
py_dealloc_thread_rlock = extern("py_dealloc_thread_rlock", (c_ptr,), c_void)
py_dealloc_thread_event = extern("py_dealloc_thread_event", (c_ptr,), c_void)
py_dealloc_thread_condition = extern(
    "py_dealloc_thread_condition", (c_ptr,), c_void,
)
py_dealloc_thread_semaphore = extern(
    "py_dealloc_thread_semaphore", (c_ptr,), c_void,
)
py_dealloc_thread_thread = extern("py_dealloc_thread_thread", (c_ptr,), c_void)
py_dealloc_virtual_thread = extern("py_dealloc_virtual_thread", (c_ptr,), c_void)
py_class_dealloc = extern("py_class_dealloc", (c_ptr,), c_void)
py_instance_dealloc = extern("py_instance_dealloc", (c_ptr,), c_void)
py_dealloc_exc = extern("py_dealloc_exc", (c_ptr,), c_void)
pcc_capi_dealloc_cext_object = extern(
    "pcc_capi_dealloc_cext_object",
    (c_ptr, c_int64),
    c_int64,
)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,), c_int64)
pcc_debug_bad_dict_slot = extern(
    "pcc_debug_bad_dict_slot", (c_ptr, c_int64, c_int64, c_ptr, c_int64), c_void,
)

define_global_i32("pcc_dealloc_depth", 0)
define_global_ptr_null("pcc_dealloc_trash_head")
define_global_ptr_null("pcc_dealloc_trash_tail")


def _dealloc_should_defer(tag: int) -> bool:
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return False
    if tag == 5:     # PY_TYPE_LIST
        return True
    if tag == 7:     # PY_TYPE_TUPLE
        return True
    if tag == 6:     # PY_TYPE_DICT
        return True
    if tag == 8:     # PY_TYPE_SET
        return True
    if tag == 11:    # PY_TYPE_INSTANCE
        return True
    if tag == 12:    # PY_TYPE_EXC
        return True
    if tag == 14:    # PY_TYPE_ITER
        return True
    if tag == 15:    # PY_TYPE_GEN
        return True
    if tag == 20:    # PY_TYPE_COROUTINE
        return True
    if tag == 29:    # PY_TYPE_CONTINUATION
        return True
    if tag == 28:    # PY_TYPE_TASK
        return True
    if tag == 30:    # PY_TYPE_VIRTUAL_THREAD
        return True
    if tag >= 100:   # PY_TYPE_USER
        return True
    return False


def _debug_check_dict_dealloc_slot(
    dict_obj,
    index: int,
    offset: int,
    obj,
) -> None:
    if ptr_is_null(obj) != 0:
        return
    if is_tagged_int(obj) != 0:
        return
    flags: int = load_i32(obj, 12)
    if (flags & 1) != 0:  # PY_FLAG_IMMORTAL
        return
    if pcc_gc_backend() == 3:
        if (flags & 4096) != 0 and (flags & 256) != 0:
            if load_i64(obj, 0) <= 0:
                return
    if load_i64(obj, 0) <= 0:
        pcc_debug_bad_dict_slot(dict_obj, index, offset, obj, load_i32(obj, 8))


def _dealloc_dispatch(o, tag: int) -> None:
    if tag == 2:        # PY_TYPE_INT
        py_dealloc_int(o)
        return
    if tag == 3:        # PY_TYPE_FLOAT
        py_dealloc_float(o)
        return
    if tag == 4:        # PY_TYPE_STR
        py_dealloc_str(o)
        return
    if tag == 5:        # PY_TYPE_LIST
        py_dealloc_list(o)
        return
    if tag == 7:        # PY_TYPE_TUPLE
        py_dealloc_tuple(o)
        return
    if tag == 6:        # PY_TYPE_DICT
        py_dealloc_dict(o)
        return
    if tag == 8:        # PY_TYPE_SET
        py_dealloc_set(o)
        return
    if tag == 9:        # PY_TYPE_FUNC
        py_dealloc_func(o)
        return
    if tag == 14:       # PY_TYPE_ITER
        py_dealloc_iter(o)
        return
    if tag == 15:       # PY_TYPE_GEN
        py_dealloc_gen(o)
        return
    if tag == 20:       # PY_TYPE_COROUTINE
        py_dealloc_coroutine(o)
        return
    if tag == 29:       # PY_TYPE_CONTINUATION
        py_dealloc_continuation(o)
        return
    if tag == 28:       # PY_TYPE_TASK
        py_dealloc_task(o)
        return
    if tag == 30:       # PY_TYPE_VIRTUAL_THREAD
        py_dealloc_virtual_thread(o)
        return
    if tag == 19:       # PY_TYPE_MEMORYVIEW
        py_dealloc_memoryview(o)
        return
    if tag == 21:       # PY_TYPE_WEAKREF
        py_dealloc_weakref(o)
        return
    if tag == 13:       # PY_TYPE_FILE
        py_dealloc_file(o)
        return
    if tag == 22:       # PY_TYPE_THREAD_LOCK
        py_dealloc_thread_lock(o)
        return
    if tag == 23:       # PY_TYPE_THREAD_RLOCK
        py_dealloc_thread_rlock(o)
        return
    if tag == 24:       # PY_TYPE_THREAD_EVENT
        py_dealloc_thread_event(o)
        return
    if tag == 25:       # PY_TYPE_THREAD_CONDITION
        py_dealloc_thread_condition(o)
        return
    if tag == 26:       # PY_TYPE_THREAD_SEMAPHORE
        py_dealloc_thread_semaphore(o)
        return
    if tag == 27:       # PY_TYPE_THREAD
        py_dealloc_thread_thread(o)
        return
    if tag == 10:       # PY_TYPE_CLASS
        py_class_dealloc(o)
        return
    if tag == 11:       # PY_TYPE_INSTANCE
        py_instance_dealloc(o)
        return
    if tag == 12:       # PY_TYPE_EXC
        py_dealloc_exc(o)
        return
    if pcc_capi_dealloc_cext_object(o, tag) != 0:
        return
    if tag >= 100:      # PY_TYPE_USER
        py_instance_dealloc(o)
        return
    py_dealloc_generic(o)


def _trash_enqueue(o, tag: int) -> bool:
    node = malloc(24)
    if ptr_is_null(node) != 0:
        return False
    store_ptr(node, 0, o)
    store_i64(node, 8, tag)
    store_ptr(node, 16, null())
    tail = global_load_ptr("pcc_dealloc_trash_tail")
    if ptr_is_null(tail) == 0:
        store_ptr(tail, 16, node)
    else:
        global_store_ptr("pcc_dealloc_trash_head", node)
    global_store_ptr("pcc_dealloc_trash_tail", node)
    return True


def _trash_drain() -> None:
    while True:
        node = global_load_ptr("pcc_dealloc_trash_head")
        if ptr_is_null(node) != 0:
            return
        nxt = load_ptr(node, 16)
        global_store_ptr("pcc_dealloc_trash_head", nxt)
        if ptr_is_null(nxt) != 0:
            global_store_ptr("pcc_dealloc_trash_tail", null())
        obj = load_ptr(node, 0)
        tag: int = load_i64(node, 8)
        free(node)
        _dealloc_dispatch(obj, tag)


@c_abi_export("pcc_dealloc_with_trash")
def pcc_dealloc_with_trash(o, tag: int) -> None:
    depth_slot = global_addr("pcc_dealloc_depth")
    depth: int = load_i32(depth_slot, 0)
    if depth > 0:
        if _dealloc_should_defer(tag):
            # zpage-resident objects must dealloc IMMEDIATELY: their
            # zpage accounting was already decremented in
            # note_object_freeing, so a deferred dealloc can find its
            # own header/fields memset by a page recycle triggered by
            # a same-cascade death on the same page (gc4 exit UAF).
            if (load_i32(o, 12) & 65536) == 0:
                if _trash_enqueue(o, tag):
                    return
    store_i32(depth_slot, 0, depth + 1)
    _dealloc_dispatch(o, tag)
    depth_after: int = load_i32(depth_slot, 0)
    if depth_after == 1:
        _trash_drain()
    store_i32(depth_slot, 0, depth_after - 1)


@c_abi_export("py_dealloc_int")
def py_dealloc_int(o) -> None:
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dealloc_float")
def py_dealloc_float(o) -> None:
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dealloc_str")
def py_dealloc_str(o) -> None:
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dealloc_list")
def py_dealloc_list(o) -> None:
    length: int = load_i64(o, 16)
    items = load_ptr(o, 32)
    if pcc_gc_backend() == 4:
        store_i64(o, 16, 0)
        store_i64(o, 24, 0)
        store_ptr(o, 32, null())
    i: int = 0
    while ptr_is_null(items) == 0 and i < length:
        item = pcc_gc_load_ptr(o, ptr_add(items, i * 8))
        if ptr_is_null(item) == 0:
            py_decref(item)
        i = i + 1
    if pcc_gc_backend() != 4:
        store_i64(o, 16, 0)
        store_i64(o, 24, 0)
        store_ptr(o, 32, null())
    free(items)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dealloc_tuple")
def py_dealloc_tuple(o) -> None:
    length: int = load_i64(o, 16)
    i: int = 0
    while i < length:
        item = pcc_gc_load_ptr(o, ptr_add(o, 24 + i * 8))
        if ptr_is_null(item) == 0:
            py_decref(item)
        i = i + 1
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dealloc_dict")
def py_dealloc_dict(o) -> None:
    entries = load_ptr(o, 40)
    if ptr_is_null(entries) == 0:
        entries_used: int = load_i64(o, 48)
        i: int = 0
        while i < entries_used:
            off: int = i * 24
            key = load_ptr(entries, off + 8)
            if ptr_is_null(key) == 0:
                key = pcc_gc_load_ptr(o, ptr_add(entries, off + 8))
                value = pcc_gc_load_ptr(o, ptr_add(entries, off + 16))
                _debug_check_dict_dealloc_slot(o, i, 8, key)
                _debug_check_dict_dealloc_slot(o, i, 16, value)
                py_decref(key)
                if ptr_is_null(value) == 0:
                    py_decref(value)
            i = i + 1
        free(entries)
    free(load_ptr(o, 32))
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dealloc_set")
def py_dealloc_set(o) -> None:
    entries = load_ptr(o, 40)
    if ptr_is_null(entries) == 0:
        dummy = global_load_ptr("py_set_dummy")
        capacity: int = load_i64(o, 24)
        i: int = 0
        while i < capacity:
            key = load_ptr(entries, i * 16 + 8)
            if ptr_is_null(key) == 0:
                if ptr_eq(key, dummy) == 0:
                    key = pcc_gc_load_ptr(o, ptr_add(entries, i * 16 + 8))
                    py_decref(key)
            i = i + 1
        free(entries)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dealloc_file")
def py_dealloc_file(o) -> None:
    py_file_close(o)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_dealloc_generic")
def py_dealloc_generic(o) -> None:
    pcc_gc_free_object_memory(o)
