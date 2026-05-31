"""Modulo dispatch split out from py_obj_ops_dispatch.

Keep ``py_obj_mod`` in its own archive member so programs that need generic
truthy/add/getitem dispatch do not also pull the string-formatting closure via
``py_str_mod``.
"""

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import cstr, is_tagged_int, load_i32, null, ptr_is_null


py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_int_mod = extern("py_int_mod", (c_ptr, c_ptr), c_ptr)
py_str_mod = extern("py_str_mod", (c_ptr, c_ptr), c_ptr)


def _type_of(o) -> int:
    if is_tagged_int(o) != 0:
        return 2
    return load_i32(o, 8)


@c_abi_export("py_obj_mod")
def py_obj_mod(a, b):
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        py_raise(py_exc_new(3, cstr("unsupported operand type(s) for %")))
        return null()
    at: int = _type_of(a)
    bt: int = _type_of(b)
    if at == 4:
        return py_str_mod(a, b)
    if (at == 2 or at == 1) and (bt == 2 or bt == 1):
        return py_int_mod(a, b)
    py_raise(py_exc_new(3, cstr("unsupported operand type(s) for %")))
    return null()
