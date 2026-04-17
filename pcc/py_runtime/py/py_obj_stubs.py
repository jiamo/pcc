"""Phase 4c.2: pcc-Python replacement for py_runtime/src/py_obj_stubs.c.

Contains:
  py_float_*      : Phase 3 stubs (return NULL / 0.0)
  py_obj_repr     : Phase 3 stub (return NULL)
  py_obj_str      : real implementation — dispatches on type tag

Layout offsets (mirroring PyObjectHeader in py_internal.h):
    0  refcount (int64)
    8  type_tag (int32)
    12 flags    (int32)

Tagged int: low bit of pointer value is 1 → PY_TYPE_INT = 2.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_double, c_int64, c_void
from pcc.unsafe import (
    is_tagged_int,
    load_i32,
    load_f64,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
    store_f64,
    untag_int,
)


py_incref               = extern("py_incref",               (c_ptr,),          c_void)
py_exc_get_message      = extern("py_exc_get_message",      (c_ptr,),          c_ptr)
py_int_to_str_obj       = extern("py_int_to_str_obj",       (c_ptr,),          c_ptr)
py_user_str_dispatch    = extern("py_user_str_dispatch",    (c_ptr,),          c_ptr)
py_mem_alloc            = extern("py_mem_alloc",            (c_int64,),        c_ptr)
py_bigint_to_double     = extern("py_bigint_to_double",     (c_ptr,),          c_double)


def _type_of(obj) -> int:
    # Offsets and type-tag literals inlined to avoid module-level
    # runtime-initialized globals (which require a main() and conflict
    # with library linkage). See py_internal.h / PY_TYPE_* enum.
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)   # PyObjectHeader.type_tag


@c_abi_export("py_float_from_f64")
def py_float_from_f64(v: float):
    # PyFloatObject layout (24 bytes):
    #   0   refcount (i64)
    #   8   type_tag (i32) = PY_TYPE_FLOAT (3)
    #   12  flags    (i32)
    #   16  value    (f64)
    p = py_mem_alloc(24)
    if ptr_is_null(p):
        return null()
    store_i64(p, 0, 1)
    store_i32(p, 8, 3)
    store_i32(p, 12, 0)
    store_f64(p, 16, v)
    return p


@c_abi_export("py_float_to_f64")
def py_float_to_f64(o) -> float:
    if ptr_is_null(o):
        return 0.0
    if is_tagged_int(o):
        i: int = untag_int(o)
        return float(i)
    tag: int = load_i32(o, 8)
    if tag == 3:              # PY_TYPE_FLOAT
        return load_f64(o, 16)
    if tag == 2:              # PY_TYPE_INT (bignum)
        return py_bigint_to_double(o)
    if tag == 1:              # PY_TYPE_BOOL
        return float(load_i32(o, 16))
    return 0.0


@c_abi_export("py_float_add")
def py_float_add(a, b):
    return null()


@c_abi_export("py_obj_repr")
def py_obj_repr(o):
    return null()


@c_abi_export("py_obj_str")
def py_obj_str(o):
    if ptr_is_null(o):
        return null()
    tag: int = _type_of(o)
    if tag == 4:            # PY_TYPE_STR
        py_incref(o)
        return o
    if tag == 2:            # PY_TYPE_INT
        return py_int_to_str_obj(o)
    if tag == 12:           # PY_TYPE_EXC
        msg = py_exc_get_message(o)
        if not ptr_is_null(msg):
            py_incref(msg)
            return msg
        return null()
    dunder = py_user_str_dispatch(o)
    if not ptr_is_null(dunder):
        return dunder
    return null()
