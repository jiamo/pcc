"""Modulo dispatch split out from py_obj_ops_dispatch.

Keep ``py_obj_mod`` in its own archive member so programs that need generic
truthy/add/getitem dispatch do not also pull the string-formatting closure via
``py_str_mod``.
"""

__pcc_runtime_port__ = True

from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_BOOL,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_INSTANCE,
    PY_TYPE_INT,
    PY_TYPE_STR,
    PY_TYPE_USER_CLASS_START,
)

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import cstr, is_tagged_int, load_i32, null, ptr_is_null


py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_int_mod = extern("py_int_mod", (c_ptr, c_ptr), c_ptr)
py_str_mod = extern("py_str_mod", (c_ptr, c_ptr), c_ptr)
py_bytes_mod = extern("py_bytes_mod", (c_ptr, c_ptr), c_ptr)
py_user_binop_dispatch = extern("py_user_binop_dispatch", (c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_ptr)


def _type_of(o) -> int:
    if is_tagged_int(o) != 0:
        return PY_TYPE_INT
    return load_i32(o, 8)


@c_abi_export("py_obj_mod")
def py_obj_mod(a, b):
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        py_raise_owned(py_exc_new(3, cstr("unsupported operand type(s) for %")))
        return null()
    at: int = _type_of(a)
    bt: int = _type_of(b)
    if at == PY_TYPE_STR:
        return py_str_mod(a, b)
    if at == PY_TYPE_BYTES or at == PY_TYPE_BYTEARRAY:            # PY_TYPE_BYTES / PY_TYPE_BYTEARRAY
        return py_bytes_mod(a, b)
    if (at == PY_TYPE_INT or at == PY_TYPE_BOOL) and (bt == PY_TYPE_INT or bt == PY_TYPE_BOOL):
        return py_int_mod(a, b)
    if (
        at == PY_TYPE_INSTANCE
        or at >= PY_TYPE_USER_CLASS_START
        or bt == PY_TYPE_INSTANCE
        or bt >= PY_TYPE_USER_CLASS_START
    ):
        return py_user_binop_dispatch(
            a,
            b,
            cstr("__mod__"),
            cstr("__rmod__"),
            cstr("unsupported operand type(s) for %"),
        )
    py_raise_owned(py_exc_new(3, cstr("unsupported operand type(s) for %")))
    return null()
