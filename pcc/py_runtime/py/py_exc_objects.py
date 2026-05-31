"""Phase 4c.5: pcc-Python port of py_exc_objects.c.

Exception-object construction, accessor, and deallocation. Handles:
    py_exc_alloc, py_exc_new, py_exc_new_with_class,
    py_exc_set_cause, py_exc_set_context, py_exc_get_message,
    py_dealloc_exc

PyExceptionObject layout (from py_internal.h):
    offset  0   PyObjectHeader    (i64 refcount + i32 tag + i32 flags = 16 bytes)
    offset 16   exc_class         (ptr)
    offset 24   message           (ptr)
    offset 32   cause             (ptr)
    offset 40   context           (ptr)
    offset 48   traceback         (ptr)
    offset 56   n_frames          (i32)
    offset 60   cap_frames        (i32)
    total size: 64 bytes

Constants (inlined per pcc-Python convention):
    PY_TYPE_CLASS = 10
    PY_TYPE_EXC   = 12
    PY_EXC_EXCEPTION = 1
"""
from pcc.extern import extern, c_abi_export, c_int32, c_ptr, c_int64, c_void
from pcc.unsafe import (
    free,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
    strlen,
)

py_incref            = extern("py_incref",            (c_ptr,),                    c_void)
py_decref            = extern("py_decref",            (c_ptr,),                    c_void)
py_str_new           = extern("py_str_new",           (c_ptr, c_int64),            c_ptr)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,),                  c_ptr)
pcc_gc_alloc         = extern("pcc_gc_alloc",         (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_load_ptr      = extern("pcc_gc_load_ptr",      (c_ptr, c_ptr), c_ptr)
pcc_gc_store_ptr     = extern("pcc_gc_store_ptr",     (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
pcc_runtime_log_event_code = extern(
    "pcc_runtime_log_event_code", (c_int32, c_int32, c_int64, c_int64, c_ptr), c_void,
)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)


@c_abi_export("py_exc_alloc")
def py_exc_alloc(cls, msg):
    e = pcc_gc_alloc(64, 12, 0)   # sizeof(PyExceptionObject)
    if ptr_is_null(e):
        return null()
    # Header is initialized by pcc_gc_alloc; clear the payload tail.
    memset(ptr_add(e, 16), 0, 48)
    store_i64(e, 0, 1)
    store_i32(e, 8, 12)
    if ptr_is_null(cls):
        cls = py_exc_builtin_class(1)   # PY_EXC_EXCEPTION
    pcc_gc_store_ptr(e, ptr_add(e, 16), cls)        # ->exc_class
    if not ptr_is_null(msg):
        n: int = strlen(msg)
        s = py_str_new(msg, n)
        pcc_gc_store_ptr(e, ptr_add(e, 24), s)      # ->message
        if not ptr_is_null(s):
            py_decref(s)
    else:
        none = global_load_ptr("py_None")
        pcc_gc_store_ptr(e, ptr_add(e, 24), none)
    # cause/context/traceback/n_frames/cap_frames already zeroed.
    pcc_runtime_log_event_code(6, 1, 12, 0, e)
    return e


def _default_exc_alloc(msg):
    # Direct PY_EXC_EXCEPTION default-class lookup. (Previously we
    # avoided calling py_exc_new here because of an int32/int64
    # signature mismatch; now resolved.)
    default_cls = py_exc_builtin_class(1)   # PY_EXC_EXCEPTION
    return py_exc_alloc(default_cls, msg)


@c_abi_export("py_exc_new")
def py_exc_new(type_tag: int, msg):
    cls = py_exc_builtin_class(type_tag)
    out = py_exc_alloc(cls, msg)
    pcc_runtime_log_event_code(6, 2, type_tag, 0, out)
    return out


@c_abi_export("py_exc_new_with_value")
def py_exc_new_with_value(type_tag: int, value):
    cls = py_exc_builtin_class(type_tag)
    e = py_exc_alloc(cls, null())
    if ptr_is_null(e):
        return null()
    if ptr_is_null(value):
        value = global_load_ptr("py_None")
    pcc_gc_store_ptr(e, ptr_add(e, 24), value)
    pcc_runtime_log_event_code(6, 8, type_tag, 0, e)
    return e


@c_abi_export("py_exc_new_with_class")
def py_exc_new_with_class(cls, msg):
    if ptr_is_null(cls):
        return _default_exc_alloc(msg)
    tag: int = _type_of(cls)
    if tag != 10:                       # PY_TYPE_CLASS
        return _default_exc_alloc(msg)
    out = py_exc_alloc(cls, msg)
    pcc_runtime_log_event_code(6, 9, tag, 0, out)
    return out


@c_abi_export("py_exc_set_cause")
def py_exc_set_cause(exc, cause) -> None:
    if ptr_is_null(exc):
        return
    if _type_of(exc) != 12:
        return
    pcc_gc_store_ptr(exc, ptr_add(exc, 32), cause)      # ->cause
    pcc_runtime_log_event_code(6, 5, 0 if ptr_is_null(cause) else 1, 0, exc)


@c_abi_export("py_exc_set_context")
def py_exc_set_context(exc, context) -> None:
    if ptr_is_null(exc):
        return
    if _type_of(exc) != 12:
        return
    pcc_gc_store_ptr(exc, ptr_add(exc, 40), context)      # ->context
    pcc_runtime_log_event_code(6, 6, 0 if ptr_is_null(context) else 1, 0, exc)


@c_abi_export("py_exc_get_message")
def py_exc_get_message(exc):
    if ptr_is_null(exc):
        return null()
    if _type_of(exc) != 12:
        return null()
    return pcc_gc_load_ptr(exc, ptr_add(exc, 24))     # ->message (borrowed)


@c_abi_export("py_exc_get_cause")
def py_exc_get_cause(exc):
    if ptr_is_null(exc):
        return null()
    if _type_of(exc) != 12:
        return null()
    cause = pcc_gc_load_ptr(exc, ptr_add(exc, 32))
    if ptr_is_null(cause):
        cause = global_load_ptr("py_None")
    py_incref(cause)
    return cause


@c_abi_export("py_exc_get_context")
def py_exc_get_context(exc):
    if ptr_is_null(exc):
        return null()
    if _type_of(exc) != 12:
        return null()
    context = pcc_gc_load_ptr(exc, ptr_add(exc, 40))
    if ptr_is_null(context):
        context = global_load_ptr("py_None")
    py_incref(context)
    return context


@c_abi_export("py_exc_traceback_len")
def py_exc_traceback_len(exc) -> int:
    if ptr_is_null(exc):
        return 0
    if _type_of(exc) != 12:
        return 0
    return load_i32(exc, 56)


@c_abi_export("py_dealloc_exc")
def py_dealloc_exc(o) -> None:
    pcc_runtime_log_event_code(6, 7, 12, 0, o)
    # ->exc_class
    cls = pcc_gc_load_ptr(o, ptr_add(o, 16))
    if not ptr_is_null(cls):
        py_decref(cls)
    # ->message
    msg = pcc_gc_load_ptr(o, ptr_add(o, 24))
    if not ptr_is_null(msg):
        py_decref(msg)
    # ->cause
    cause = pcc_gc_load_ptr(o, ptr_add(o, 32))
    if not ptr_is_null(cause):
        py_decref(cause)
    # ->context
    ctx = pcc_gc_load_ptr(o, ptr_add(o, 40))
    if not ptr_is_null(ctx):
        py_decref(ctx)
    # ->traceback (malloc'd array, free not decref)
    tb = load_ptr(o, 48)
    if not ptr_is_null(tb):
        free(tb)
    pcc_gc_free_object_memory(o)
