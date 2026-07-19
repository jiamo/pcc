"""Phase 4c.11: pcc-Python port of py_obj.c.

py_bool_from_bit + py_incref + py_decref dispatch. The dealloc
implementations are separate ABI symbols, provided by py_obj_dealloc.py
in the pcc-Python runtime archive and by py_obj_dealloc.c in the C
runtime archives. The immortal singletons live in py_substrate.py for
the pcc-Python archive and in py_substrate.c for the C runtime archives.
This port only owns the "dispatch layer".

PyObjectHeader layout:
    offset  0   refcount     (i64)
    offset  8   type_tag     (i32)
    offset 12   flags        (i32)

PY_FLAG_IMMORTAL = 0x1. Tagged ints are low-bit=1 pointers.

Type tags (from py_runtime.h):
    PY_TYPE_NONE     = 0
    PY_TYPE_BOOL     = 1
    PY_TYPE_INT      = 2
    PY_TYPE_FLOAT    = 3
    PY_TYPE_STR      = 4
    PY_TYPE_LIST     = 5
    PY_TYPE_DICT     = 6
    PY_TYPE_TUPLE    = 7
    PY_TYPE_SET      = 8
    PY_TYPE_FUNC     = 9
    PY_TYPE_CLASS    = 10
    PY_TYPE_INSTANCE = 11
    PY_TYPE_EXC      = 12
    PY_TYPE_FILE     = 13
    PY_TYPE_ITER     = 14
    PY_TYPE_GEN      = 15
    PY_TYPE_MEMORYVIEW = 19
    PY_TYPE_COROUTINE = 20
    PY_TYPE_TASK     = 28
    PY_TYPE_CONTINUATION = 29
    PY_TYPE_VIRTUAL_THREAD = 30
    PY_TYPE_USER     = 100
"""

from pcc.extern import extern, c_abi_export, c_int32, c_int64, c_ptr, c_void
from pcc.unsafe import (
    cstr,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_ptr,
    load_i32,
    load_i64,
    malloc,
    memmove,
    memset,
    null,
    ptr_add,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    store_ptr,
    store_i32,
    store_i64,
)

py_dealloc_int = extern("py_dealloc_int", (c_ptr,), c_void)
py_dealloc_float = extern("py_dealloc_float", (c_ptr,), c_void)
py_dealloc_str = extern("py_dealloc_str", (c_ptr,), c_void)
py_dealloc_list = extern("py_dealloc_list", (c_ptr,), c_void)
py_dealloc_tuple = extern("py_dealloc_tuple", (c_ptr,), c_void)
py_dealloc_dict = extern("py_dealloc_dict", (c_ptr,), c_void)
py_dealloc_set = extern("py_dealloc_set", (c_ptr,), c_void)
py_dealloc_func = extern("py_dealloc_func", (c_ptr,), c_void)
py_dealloc_iter = extern("py_dealloc_iter", (c_ptr,), c_void)
py_dealloc_gen = extern("py_dealloc_gen", (c_ptr,), c_void)
py_dealloc_coroutine = extern("py_dealloc_coroutine", (c_ptr,), c_void)
py_dealloc_continuation = extern("py_dealloc_continuation", (c_ptr,), c_void)
py_dealloc_task = extern("py_dealloc_task", (c_ptr,), c_void)
py_dealloc_memoryview = extern("py_dealloc_memoryview", (c_ptr,), c_void)
py_dealloc_weakref = extern("py_dealloc_weakref", (c_ptr,), c_void)
py_dealloc_generic = extern("py_dealloc_generic", (c_ptr,), c_void)
py_dealloc_thread_lock = extern("py_dealloc_thread_lock", (c_ptr,), c_void)
py_dealloc_thread_rlock = extern("py_dealloc_thread_rlock", (c_ptr,), c_void)
py_dealloc_thread_event = extern("py_dealloc_thread_event", (c_ptr,), c_void)
py_dealloc_thread_condition = extern("py_dealloc_thread_condition", (c_ptr,), c_void)
py_dealloc_thread_semaphore = extern("py_dealloc_thread_semaphore", (c_ptr,), c_void)
py_dealloc_thread_thread = extern("py_dealloc_thread_thread", (c_ptr,), c_void)
py_dealloc_virtual_thread = extern("py_dealloc_virtual_thread", (c_ptr,), c_void)
py_class_dealloc = extern("py_class_dealloc", (c_ptr,), c_void)
py_instance_dealloc = extern("py_instance_dealloc", (c_ptr,), c_void)
py_dealloc_exc = extern("py_dealloc_exc", (c_ptr,), c_void)
pcc_dealloc_with_trash = extern(
    "pcc_dealloc_with_trash",
    (c_ptr, c_int64),
    c_void,
)
pcc_runtime_monotonic_us = extern("pcc_runtime_monotonic_us", (), c_int64)
pcc_gc_record_explicit_pause = extern(
    "pcc_gc_record_explicit_pause", (c_int64, c_int64), c_void
)
py_gc_collect = extern("py_gc_collect", (), c_int64)
py_gc_untrack = extern("py_gc_untrack", (c_ptr,), c_void)
pcc_gc_slot_is_runtime_root = extern("pcc_gc_slot_is_runtime_root", (c_ptr,), c_int64)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_list_remove = extern("py_list_remove", (c_ptr, c_ptr), c_void)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_obj_eq = extern("py_obj_eq", (c_ptr, c_ptr), c_int64)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_weakref_invalidate = extern("py_weakref_invalidate", (c_ptr,), c_void)
pcc_gc_note_alloc = extern("pcc_gc_note_alloc", (c_int64,), c_void)
pcc_gc_note_object_allocated = extern(
    "pcc_gc_note_object_allocated",
    (c_ptr,),
    c_void,
)
pcc_gc_note_object_allocated_sized = extern(
    "pcc_gc_note_object_allocated_sized",
    (c_ptr, c_int64),
    c_void,
)
pcc_gc_try_minor_alloc = extern("pcc_gc_try_minor_alloc", (c_int64,), c_ptr)
pcc_gc_backend4_try_zpage_alloc = extern(
    "pcc_gc_backend4_try_zpage_alloc",
    (c_int64, c_int32),
    c_ptr,
)
pcc_gc_note_object_freeing = extern(
    "pcc_gc_note_object_freeing",
    (c_ptr,),
    c_void,
)
pcc_gc_note_load = extern("pcc_gc_note_load", (), c_void)
pcc_gc_note_relocation_read = extern(
    "pcc_gc_note_relocation_read",
    (c_ptr,),
    c_ptr,
)
pcc_gc_note_store = extern("pcc_gc_note_store", (), c_void)
pcc_gc_note_safepoint = extern("pcc_gc_note_safepoint", (), c_void)
pcc_gc_note_pin = extern("pcc_gc_note_pin", (c_int32,), c_void)
pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_note_frame_enter = extern(
    "pcc_gc_note_frame_enter",
    (c_ptr, c_ptr),
    c_void,
)
pcc_gc_note_frame_leave = extern(
    "pcc_gc_note_frame_leave",
    (c_ptr,),
    c_void,
)
pcc_gc_note_frame_enter_lifo = extern(
    "pcc_gc_note_frame_enter_lifo",
    (c_ptr, c_ptr),
    c_void,
)
pcc_gc_note_frame_leave_lifo = extern(
    "pcc_gc_note_frame_leave_lifo",
    (c_ptr,),
    c_void,
)
pcc_gc_note_write_barrier = extern(
    "pcc_gc_note_write_barrier",
    (c_ptr, c_ptr),
    c_void,
)
pcc_gc_note_slot_write_barrier = extern(
    "pcc_gc_note_slot_write_barrier",
    (c_ptr, c_ptr, c_ptr),
    c_void,
)
pcc_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock",
    (c_ptr,),
    c_int64,
)
pcc_gc_backend4_slot_needs_resolve = extern(
    "pcc_gc_backend4_slot_needs_resolve",
    (c_ptr,),
    c_int64,
)
pcc_gc_object_is_known = extern("pcc_gc_object_is_known", (c_ptr,), c_int64)
pcc_gc_has_tracing_sweep = extern("pcc_gc_has_tracing_sweep", (), c_int64)
pcc_gc_collect_tracing = extern("pcc_gc_collect_tracing", (), c_int64)
pcc_gc_begin_explicit_tracing_collect = extern(
    "pcc_gc_begin_explicit_tracing_collect",
    (),
    c_void,
)
pcc_gc_end_explicit_tracing_collect = extern(
    "pcc_gc_end_explicit_tracing_collect",
    (),
    c_void,
)
pcc_gc_external_resource_poll = extern(
    "pcc_gc_external_resource_poll",
    (),
    c_int64,
)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_refcount_incref = extern("pcc_refcount_incref", (c_ptr,), c_int64)
pcc_refcount_decref = extern("pcc_refcount_decref", (c_ptr,), c_int64)
pcc_refcount_forget = extern("pcc_refcount_forget", (c_ptr,), c_void)
pcc_runtime_log_event_code = extern(
    "pcc_runtime_log_event_code", (c_int32, c_int32, c_int64, c_int64, c_ptr), c_void
)
pcc_debug_note_alloc_size = extern(
    "pcc_debug_note_alloc_size", (c_ptr, c_int64), c_void
)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)


def _gc_backend_fast() -> int:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return pcc_gc_backend()
    return load_i32(global_addr("pcc_gc_backend_selected"), 0)


def _ptr_can_have_header(o) -> bool:
    if ptr_is_null(o) != 0:
        return False
    if is_tagged_int(o) != 0:
        return False
    bits: int = ptr_diff(o, null())
    if bits < 4096:
        return False
    if (bits & 7) != 0:
        return False
    if bits >= 17592186044416 and bits < 35184372088832:
        return False
    if bits >= 281474976710656:
        return False
    return True


def _gc_relocation_candidate(o) -> int:
    if not _ptr_can_have_header(o):
        return 0
    return 1 if (load_i32(o, 12) & 2048) != 0 else 0


def _gc_forwarding_population() -> int:
    return load_i32(global_addr("pcc_gc_forwarding_population"), 0)


def _gc_backend4_should_check_slot(slot) -> int:
    if _gc_forwarding_population() > 0:
        return 1
    if ptr_is_null(global_load_ptr("pcc_gc_relocation_set_head")) != 0:
        return 0
    if pcc_gc_slot_is_runtime_root(slot) != 0:
        return 0
    return 1


# NOTE: pcc-Python initializes module-level integers in the
# auto-generated main(), which the Makefile strips for library .o
# builds. So we inline the type-tag and flag literals at each use
# site instead of declaring them as module constants.
#
#   PY_FLAG_IMMORTAL  = 1
#   PY_TYPE_INT       = 2
#   PY_TYPE_FLOAT     = 3
#   PY_TYPE_STR       = 4
#   PY_TYPE_LIST      = 5
#   PY_TYPE_DICT      = 6
#   PY_TYPE_TUPLE     = 7
#   PY_TYPE_SET       = 8
#   PY_TYPE_CLASS     = 10
#   PY_TYPE_INSTANCE  = 11
#   PY_TYPE_EXC       = 12
#   PY_TYPE_FILE      = 13
#   PY_TYPE_ITER      = 14
#   PY_TYPE_GEN       = 15
#   PY_TYPE_COROUTINE = 20
#   PY_TYPE_TASK      = 28
#   PY_TYPE_USER      = 100


@c_abi_export("py_bool_from_bit")
def py_bool_from_bit(b: int):
    if b != 0:
        return global_load_ptr("py_True")
    return global_load_ptr("py_False")


def _gc_graph_leaf_tag(tag: int) -> int:
    if tag == 0:
        return 1
    if tag == 1:
        return 1
    if tag == 2:
        return 1
    if tag == 3:
        return 1
    if tag == 4:
        return 1
    if tag == 16:
        return 1
    if tag == 17:
        return 1
    if tag == 18:
        return 1
    if tag == 32:
        return 1
    return 0


@c_abi_export("pcc_gc_alloc")
def pcc_gc_alloc(size: int, type_tag: int, flags: int):
    if size < 16:
        return null()
    pcc_thread_safepoint()
    pcc_gc_note_alloc(size)
    if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
        pcc_runtime_log_event_code(1, 1, size, type_tag, null())
    backend: int = _gc_backend_fast()
    obj = null()
    stored_flags: int = flags
    if backend == 3:
        obj = pcc_gc_try_minor_alloc(size)
    elif backend == 4:
        if _gc_graph_leaf_tag(type_tag) == 0:
            obj = pcc_gc_backend4_try_zpage_alloc(size, flags)
            if ptr_is_null(obj) == 0:
                stored_flags = stored_flags | 65536
        else:
            stored_flags = stored_flags & ~65536
    if ptr_is_null(obj) != 0:
        # Mirror the C tier's calloc (py_obj.c): fresh objects are
        # GC-visible before their constructor fills the body; visitors
        # must see NULL slots, not malloc garbage.
        obj = malloc(size)
        if ptr_is_null(obj) == 0:
            memset(obj, 0, size)
            if backend == 4:
                stored_flags = (stored_flags & ~65536) | 262144
            elif backend == 3:
                stored_flags = (stored_flags & ~4096) | 262144
    if ptr_is_null(obj):
        return obj
    store_i64(obj, 0, 1)
    store_i32(obj, 8, type_tag)
    store_i32(obj, 12, stored_flags)
    pcc_debug_note_alloc_size(obj, size)
    pcc_gc_note_object_allocated_sized(obj, size)
    if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
        pcc_runtime_log_event_code(1, 2, size, type_tag, obj)
    return obj


@c_abi_export("pcc_gc_retain")
def pcc_gc_retain(o):
    py_incref(o)
    return o


@c_abi_export("pcc_gc_release")
def pcc_gc_release(o) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    backend: int = _gc_backend_fast()
    if backend == 3:
        if _gc_relocation_candidate(o) != 0:
            resolved = pcc_gc_note_relocation_read(o)
            if resolved != o:
                py_decref(o)
                return
    elif backend == 4:
        if _gc_forwarding_population() > 0 and _gc_relocation_candidate(o) != 0:
            resolved = pcc_gc_note_relocation_read(o)
            if ptr_eq(resolved, o) != 0 and pcc_gc_object_is_known_no_lock(o) == 0:
                return
            o = resolved
    if backend == 3:
        flags: int = load_i32(o, 12)
        if (flags & 4096) != 0 and (flags & 256) != 0 and load_i64(o, 0) <= 0:
            return
    py_decref(o)


@c_abi_export("pcc_gc_load_ptr")
def pcc_gc_load_ptr(owner, slot):
    if ptr_is_null(slot):
        return null()
    v = load_ptr(slot, 0)
    if load_i32(global_addr("pcc_gc_read_barrier_enabled"), 0) == 0:
        return v
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_backend()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend == 3 or backend == 4:
        if backend == 3 and _gc_forwarding_population() <= 0:
            return v
        if backend == 4 and _gc_backend4_should_check_slot(slot) == 0:
            return v
        pcc_gc_note_load()
        needs_resolve: int = 0
        if backend == 4:
            # G-P0-LONGRUN: decide via pointer-value lookups, never a raw
            # header deref of a possibly-stale/unmapped slot value.
            if pcc_gc_backend4_slot_needs_resolve(v) != 0:
                needs_resolve = 1
        elif _gc_relocation_candidate(v) != 0:
            needs_resolve = 1
        if needs_resolve != 0:
            resolved = pcc_gc_note_relocation_read(v)
            if ptr_eq(resolved, v) == 0:
                store_ptr(slot, 0, resolved)
                v = resolved
        return v
    return v


@c_abi_export("pcc_gc_load_borrowed_ptr")
def pcc_gc_load_borrowed_ptr(owner, slot):
    if ptr_is_null(slot):
        return null()
    v = load_ptr(slot, 0)
    if load_i32(global_addr("pcc_gc_read_barrier_enabled"), 0) == 0:
        return v
    backend: int = _gc_backend_fast()
    if backend == 3 or backend == 4:
        if backend == 3 and _gc_forwarding_population() <= 0:
            return v
        if backend == 4 and _gc_backend4_should_check_slot(slot) == 0:
            return v
        pcc_gc_note_load()
        needs_resolve: int = 0
        if backend == 4:
            # G-P0-LONGRUN: decide via pointer-value lookups, never a raw
            # header deref of a possibly-stale/unmapped slot value.
            if pcc_gc_backend4_slot_needs_resolve(v) != 0:
                needs_resolve = 1
        elif _gc_relocation_candidate(v) != 0:
            needs_resolve = 1
        if needs_resolve != 0:
            resolved = pcc_gc_note_relocation_read(v)
            if ptr_eq(resolved, v) == 0:
                store_ptr(slot, 0, resolved)
                v = resolved
        return v
    return v


@c_abi_export("pcc_gc_resolve_owned_ptr")
def pcc_gc_resolve_owned_ptr(value):
    if ptr_is_null(value) != 0:
        return value
    if is_tagged_int(value) != 0:
        return value
    backend: int = _gc_backend_fast()
    if backend != 3 and backend != 4:
        return value
    if _gc_forwarding_population() <= 0:
        return value
    if backend == 3:
        if _gc_relocation_candidate(value) == 0:
            return value
    resolved = pcc_gc_note_relocation_read(value)
    if ptr_eq(resolved, value) == 0:
        return resolved
    return value


@c_abi_export("pcc_gc_store_ptr")
def pcc_gc_store_ptr(owner, slot, value) -> None:
    if ptr_is_null(slot):
        return
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_backend()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend == 1 or backend == 2 or backend == 3 or backend == 4:
        pcc_gc_note_store()
    if backend == 4:
        if _gc_forwarding_population() > 0 and _gc_relocation_candidate(value) != 0:
            value = pcc_gc_note_relocation_read(value)
    elif backend == 3:
        if _gc_forwarding_population() > 0 and _gc_relocation_candidate(value) != 0:
            value = pcc_gc_note_relocation_read(value)
    if backend == 1 or backend == 2 or backend == 3 or backend == 4:
        pcc_gc_note_slot_write_barrier(owner, slot, value)
    if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
        pcc_runtime_log_event_code(2, 3, backend, 0, owner)
    old = load_ptr(slot, 0)
    py_incref(value)
    store_ptr(slot, 0, value)
    py_decref(old)


@c_abi_export("pcc_gc_store_root")
def pcc_gc_store_root(slot, value) -> None:
    if ptr_is_null(slot) != 0:
        return
    backend: int = _gc_backend_fast()
    if backend == 0:
        if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
            pcc_runtime_log_event_code(2, 3, backend, 0, null())
        old = load_ptr(slot, 0)
        py_incref(value)
        store_ptr(slot, 0, value)
        py_decref(old)
        return
    pcc_py_gc_minor_graph_lock()
    if ptr_is_null(slot) == 0:
        if backend == 1 or backend == 2 or backend == 3 or backend == 4:
            pcc_gc_note_store()
        if backend == 4:
            if _gc_forwarding_population() > 0 and _gc_relocation_candidate(value) != 0:
                value = pcc_gc_note_relocation_read(value)
        elif backend == 3:
            if _gc_forwarding_population() > 0 and _gc_relocation_candidate(value) != 0:
                value = pcc_gc_note_relocation_read(value)
        if backend == 1 or backend == 2 or backend == 3 or backend == 4:
            pcc_gc_note_slot_write_barrier(null(), slot, value)
        if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
            pcc_runtime_log_event_code(2, 3, backend, 0, null())
        old = load_ptr(slot, 0)
        py_incref(value)
        store_ptr(slot, 0, value)
        if ptr_is_null(old) == 0 and is_tagged_int(old) == 0:
            if backend == 4 and pcc_gc_object_is_known_no_lock(old) == 0:
                old = null()
        py_decref(old)
    pcc_py_gc_minor_graph_unlock()


@c_abi_export("pcc_gc_frame_enter")
def pcc_gc_frame_enter(frame_map, slots) -> None:
    pcc_gc_note_frame_enter(frame_map, slots)
    return


@c_abi_export("pcc_gc_frame_leave")
def pcc_gc_frame_leave(slots) -> None:
    pcc_gc_note_frame_leave(slots)
    return


@c_abi_export("pcc_gc_frame_enter_lifo")
def pcc_gc_frame_enter_lifo(frame_map, slots) -> None:
    pcc_gc_note_frame_enter_lifo(frame_map, slots)
    return


@c_abi_export("pcc_gc_frame_leave_lifo")
def pcc_gc_frame_leave_lifo(slots) -> None:
    pcc_gc_note_frame_leave_lifo(slots)
    return


@c_abi_export("pcc_gc_safepoint")
def pcc_gc_safepoint() -> None:
    pcc_gc_note_safepoint()
    pcc_thread_safepoint()
    pcc_gc_external_resource_poll()
    return


def _py_gc_callbacks_ensure():
    callbacks = global_load_ptr("py_gc_callbacks")
    if ptr_is_null(callbacks) != 0:
        callbacks = py_list_new(0)
        if ptr_is_null(callbacks) != 0:
            return null()
        global_store_ptr("py_gc_callbacks", callbacks)
        pcc_gc_pin(callbacks)
    return callbacks


@c_abi_export("py_gc_callbacks_list")
def py_gc_callbacks_list():
    callbacks = _py_gc_callbacks_ensure()
    if ptr_is_null(callbacks) == 0:
        py_incref(callbacks)
    return callbacks


@c_abi_export("py_gc_callbacks_append")
def py_gc_callbacks_append(callback) -> None:
    callbacks = _py_gc_callbacks_ensure()
    if ptr_is_null(callbacks) != 0:
        return
    py_list_append(callbacks, callback)
    return


def _pcc_gc_callback_eq(a, b) -> int:
    if ptr_eq(a, b) != 0:
        return 1
    if ptr_is_null(a) != 0:
        return 0
    if ptr_is_null(b) != 0:
        return 0
    if is_tagged_int(a) != 0 or is_tagged_int(b) != 0:
        return py_obj_eq(a, b)
    a = pcc_gc_note_relocation_read(a)
    b = pcc_gc_note_relocation_read(b)
    if ptr_eq(a, b) != 0:
        return 1
    if load_i32(a, 8) == 9:
        if load_i32(b, 8) == 9:
            if ptr_eq(load_ptr(a, 56), load_ptr(b, 56)) != 0:
                a_captures = pcc_gc_load_ptr(a, ptr_add(a, 64))
                b_captures = pcc_gc_load_ptr(b, ptr_add(b, 64))
                return py_obj_eq(a_captures, b_captures)
    return py_obj_eq(a, b)


@c_abi_export("py_gc_callbacks_remove")
def py_gc_callbacks_remove(callback) -> None:
    callbacks = _py_gc_callbacks_ensure()
    if ptr_is_null(callbacks) != 0:
        return
    length: int = load_i64(callbacks, 16)
    items = load_ptr(callbacks, 32)
    i: int = 0
    while i < length:
        existing = pcc_gc_load_ptr(callbacks, ptr_add(items, i * 8))
        if _pcc_gc_callback_eq(existing, callback) != 0:
            if i < length - 1:
                src = ptr_add(items, (i + 1) * 8)
                dst = ptr_add(items, i * 8)
                memmove(dst, src, (length - i - 1) * 8)
            store_i64(callbacks, 16, length - 1)
            py_decref(existing)
            return
        i = i + 1
    py_list_remove(callbacks, callback)
    return


def _pcc_gc_fire_callbacks(phase, phase_len: int) -> None:
    callbacks = global_load_ptr("py_gc_callbacks")
    if ptr_is_null(callbacks) != 0:
        return
    if load_i32(global_addr("py_gc_callbacks_firing"), 0) != 0:
        return
    n: int = py_list_len(callbacks)
    if n <= 0:
        return

    store_i32(global_addr("py_gc_callbacks_firing"), 0, 1)
    phase_obj = py_str_new(phase, phase_len)
    info = py_dict_new()
    if ptr_is_null(phase_obj) != 0 or ptr_is_null(info) != 0:
        if ptr_is_null(phase_obj) == 0:
            py_decref(phase_obj)
        if ptr_is_null(info) == 0:
            py_decref(info)
        store_i32(global_addr("py_gc_callbacks_firing"), 0, 0)
        return

    i: int = 0
    while i < n:
        callback = py_list_get(callbacks, i)
        if ptr_is_null(callback) == 0:
            args = py_tuple_new(2)
            if ptr_is_null(args) == 0:
                py_tuple_set_item(args, 0, phase_obj)
                py_tuple_set_item(args, 1, info)
                result = py_obj_call(callback, args, global_load_ptr("py_None"))
                if ptr_is_null(result) == 0:
                    py_decref(result)
                py_decref(args)
            py_decref(callback)
            py_clear_exception()
        i = i + 1

    py_decref(info)
    py_decref(phase_obj)
    store_i32(global_addr("py_gc_callbacks_firing"), 0, 0)
    return


@c_abi_export("pcc_gc_collect")
def pcc_gc_collect(reason: int) -> int:
    backend: int = _gc_backend_fast()
    # Reentrancy guard (CPython gc.collecting semantics): a gc.collect() invoked
    # from a finalizer (__del__) that runs DURING an in-progress tracing collect
    # must be a no-op. Otherwise the reentrant mark re-whitens objects the outer
    # sweep is mid-iteration on (clobbering its GC_SWEEP_CANDIDATE/1024 flags) and
    # the reentrant sweep frees nodes the outer _sweep_unreachable still holds ->
    # use-after-free segfault on #1/#2/#3/#4. The outer collect keeps
    # pcc_gc_explicit_collect_active set across its whole begin..end (mark+sweep)
    # window, so a non-zero value here means we are reentrant; return 0 before any
    # STW/callbacks (re-entering STW on an already-stopped world would also hang).
    # #0 (refcount+cycle) never sets the flag and is reentrancy-safe already.
    # See gc-5backend-reentrant-collect-during-finalizer-no-libpython.md.
    if backend != 0:
        if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0:
            return 0
    if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
        pcc_runtime_log_event_code(2, 1, reason, backend, null())
    _pcc_gc_fire_callbacks(cstr("start"), 5)
    collected: int = 0
    if backend == 0:
        # G-P3: time backend 0's explicit cycle collect (its only
        # pause-like window) so pause telemetry covers all backends.
        pause_t0: int = pcc_runtime_monotonic_us()
        collected = py_gc_collect()
        pcc_gc_record_explicit_pause(pause_t0, pcc_runtime_monotonic_us())
    else:
        stw: int = pcc_stop_the_world()
        while stw != 0:
            pcc_thread_safepoint()
            stw = pcc_stop_the_world()
        pcc_gc_begin_explicit_tracing_collect()
        while True:
            stepped: int = pcc_gc_step(1024)
            if stepped == 0:
                break
        if pcc_gc_has_tracing_sweep() != 0:
            collected = collected + pcc_gc_collect_tracing()
        pcc_gc_end_explicit_tracing_collect()
        pcc_resume_world()
    # Release callbacks run after the tracing world is resumed. The shared
    # C-kernel registry has a zero-ready fast path and contains no PyObjects.
    pcc_gc_external_resource_poll()
    _pcc_gc_fire_callbacks(cstr("stop"), 4)
    if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
        pcc_runtime_log_event_code(2, 2, collected, backend, null())
    return collected


@c_abi_export("pcc_gc_pin")
def pcc_gc_pin(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    flags: int = load_i32(o, 12)
    store_i32(o, 12, flags | 64)
    pcc_gc_note_pin(1)
    return


@c_abi_export("pcc_gc_unpin")
def pcc_gc_unpin(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    flags: int = load_i32(o, 12)
    store_i32(o, 12, flags & ~64)
    pcc_gc_note_pin(-1)
    return


_pcc_debug_bad_incref = extern("pcc_debug_bad_incref", (c_ptr, c_int32), c_void)


@c_abi_export("py_incref")
def py_incref(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_backend()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if not _ptr_can_have_header(o):
        return
    tag: int = load_i32(o, 8)
    # validity window: 0..32 (32 == PY_TYPE_CPY_HANDLE) or user/class tags
    if tag < 0 or (tag > 32 and tag < 100) or tag > 500:
        return
    flags: int = load_i32(o, 12)
    if (backend == 1 or backend == 2) and flags == 0:
        return
    if backend == 4 and _gc_forwarding_population() > 0 and (flags & 2048) != 0:
        # Count-on-NEW model (gc4 remap design): after relocation the
        # outstanding refcount lives on the NEW copy; resolve first.
        resolved = pcc_gc_note_relocation_read(o)
        if ptr_is_null(resolved) == 0 and ptr_eq(resolved, o) == 0:
            o = resolved
            tag = load_i32(o, 8)
            flags = load_i32(o, 12)
    if (
        (tag == 29 or tag == 30 or tag == 31)
        and (flags & 2) == 0
        and pcc_gc_object_is_known(o) == 0
    ):
        return
    if (flags & 1) != 0:  # PY_FLAG_IMMORTAL
        return
    if backend == 3 and (flags & 4096) != 0 and (flags & 256) != 0:
        if load_i64(o, 0) <= 0:
            return
    new_rc: int = pcc_refcount_incref(o)
    if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
        pcc_runtime_log_event_code(3, 1, new_rc, tag, o)


@c_abi_export("py_decref")
def py_decref(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_backend()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if not _ptr_can_have_header(o):
        return
    tag_dbg: int = load_i32(o, 8)
    # validity window: 0..32 (32 == PY_TYPE_CPY_HANDLE) or user/class tags
    if tag_dbg < 0 or (tag_dbg > 32 and tag_dbg < 100) or tag_dbg > 500:
        return
    flags: int = load_i32(o, 12)
    if (backend == 1 or backend == 2) and flags == 0:
        return
    if backend == 4 and _gc_forwarding_population() > 0 and (flags & 2048) != 0:
        # Count-on-NEW model (gc4 remap design): resolve before
        # decrementing; old copies are immortal shells so an
        # unresolvable stray decref is a no-op below.
        resolved = pcc_gc_note_relocation_read(o)
        if ptr_is_null(resolved) == 0 and ptr_eq(resolved, o) == 0:
            o = resolved
            tag_dbg = load_i32(o, 8)
            flags = load_i32(o, 12)
    if (
        (tag_dbg == 29 or tag_dbg == 30 or tag_dbg == 31)
        and (flags & 2) == 0
        and pcc_gc_object_is_known(o) == 0
    ):
        return
    if (flags & 1) != 0:  # PY_FLAG_IMMORTAL
        return
    if backend == 3 and (flags & 4096) != 0 and (flags & 256) != 0:
        if load_i64(o, 0) <= 0:
            return
    if (
        backend == 4
        and (flags & (2048 | 8192 | 131072)) != 0
        and pcc_gc_object_is_known(o) == 0
    ):
        return
    new_rc: int = pcc_refcount_decref(o)
    if new_rc < 0:
        _pcc_debug_bad_incref(o, tag_dbg)
        return
    if new_rc > 0:
        if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
            pcc_runtime_log_event_code(3, 2, new_rc, tag_dbg, o)
        return
    delay_zpage_freeing_note: int = 0
    if backend == 4 and (flags & 65536) != 0:
        delay_zpage_freeing_note = 1
    # Dedicated deallocation state.  Do not infer it from refcount zero because
    # backend-3 forwarding shells can legitimately carry a zero count.
    flags = flags | 524288
    store_i32(o, 12, flags)
    if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
        pcc_runtime_log_event_code(3, 2, new_rc, tag_dbg, o)
    pcc_refcount_forget(o)
    if load_i32(global_addr("pcc_runtime_log_fast_state"), 0) != 0:
        pcc_runtime_log_event_code(3, 3, 0, tag_dbg, o)

    py_weakref_invalidate(o)
    if delay_zpage_freeing_note == 0:
        pcc_gc_note_object_freeing(o)
    py_gc_untrack(o)
    tag: int = load_i32(o, 8)
    pcc_dealloc_with_trash(o, tag)
    if delay_zpage_freeing_note != 0:
        pcc_gc_note_object_freeing(o)
