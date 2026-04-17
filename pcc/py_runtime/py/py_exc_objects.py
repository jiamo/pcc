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
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import (
    free,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_ptr,
    malloc,
    memset,
    null,
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


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)


@c_abi_export("py_exc_alloc")
def py_exc_alloc(cls, msg):
    e = malloc(64)                # sizeof(PyExceptionObject)
    if ptr_is_null(e):
        return null()
    memset(e, 0, 64)
    # Header: refcount=1, type_tag=PY_TYPE_EXC(12), flags=0
    store_i64(e, 0, 1)
    store_i32(e, 8, 12)
    store_i32(e, 12, 0)
    if ptr_is_null(cls):
        cls = py_exc_builtin_class(1)   # PY_EXC_EXCEPTION
    py_incref(cls)
    store_ptr(e, 16, cls)        # ->exc_class
    if not ptr_is_null(msg):
        n: int = strlen(msg)
        s = py_str_new(msg, n)
        store_ptr(e, 24, s)      # ->message (owned)
    else:
        none = global_load_ptr("py_None")
        py_incref(none)
        store_ptr(e, 24, none)
    # cause/context/traceback/n_frames/cap_frames already zeroed.
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
    return py_exc_alloc(cls, msg)


@c_abi_export("py_exc_new_with_class")
def py_exc_new_with_class(cls, msg):
    if ptr_is_null(cls):
        return _default_exc_alloc(msg)
    tag: int = _type_of(cls)
    if tag != 10:                       # PY_TYPE_CLASS
        return _default_exc_alloc(msg)
    return py_exc_alloc(cls, msg)


@c_abi_export("py_exc_set_cause")
def py_exc_set_cause(exc, cause) -> None:
    if ptr_is_null(exc):
        return
    if _type_of(exc) != 12:
        return
    old = load_ptr(exc, 32)      # ->cause
    if not ptr_is_null(cause):
        py_incref(cause)
    store_ptr(exc, 32, cause)
    if not ptr_is_null(old):
        py_decref(old)


@c_abi_export("py_exc_set_context")
def py_exc_set_context(exc, context) -> None:
    if ptr_is_null(exc):
        return
    if _type_of(exc) != 12:
        return
    old = load_ptr(exc, 40)      # ->context
    if not ptr_is_null(context):
        py_incref(context)
    store_ptr(exc, 40, context)
    if not ptr_is_null(old):
        py_decref(old)


@c_abi_export("py_exc_get_message")
def py_exc_get_message(exc):
    if ptr_is_null(exc):
        return null()
    if _type_of(exc) != 12:
        return null()
    return load_ptr(exc, 24)     # ->message (borrowed)


@c_abi_export("py_dealloc_exc")
def py_dealloc_exc(o) -> None:
    # ->exc_class
    cls = load_ptr(o, 16)
    if not ptr_is_null(cls):
        py_decref(cls)
    # ->message
    msg = load_ptr(o, 24)
    if not ptr_is_null(msg):
        py_decref(msg)
    # ->cause
    cause = load_ptr(o, 32)
    if not ptr_is_null(cause):
        py_decref(cause)
    # ->context
    ctx = load_ptr(o, 40)
    if not ptr_is_null(ctx):
        py_decref(ctx)
    # ->traceback (malloc'd array, free not decref)
    tb = load_ptr(o, 48)
    if not ptr_is_null(tb):
        free(tb)
    free(o)
