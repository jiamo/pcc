"""pcc-Python port of py_str.c.

Only py_str_new remains here; the rest of the string runtime lives in
py_str_accessors.py.
"""
from pcc.extern import c_abi_export
from pcc.unsafe import (
    malloc,
    memmove,
    null,
    ptr_add,
    ptr_is_null,
    store_i8,
    store_i32,
    store_i64,
)


def _str_alloc(byte_len: int):
    if byte_len < 0:
        return null()
    s = malloc(40 + byte_len + 1)
    if ptr_is_null(s) != 0:
        return null()
    store_i64(s, 0, 1)               # refcount
    store_i32(s, 8, 4)               # PY_TYPE_STR
    store_i32(s, 12, 0)              # flags
    store_i64(s, 16, byte_len)       # byte_len
    store_i64(s, 24, -1)             # cp_len
    store_i64(s, 32, -1)             # hash
    store_i8(s, 40 + byte_len, 0)    # NUL terminator
    return s


@c_abi_export("py_str_new")
def py_str_new(utf8, byte_len: int):
    if byte_len < 0:
        byte_len = 0
    s = _str_alloc(byte_len)
    if ptr_is_null(s) != 0:
        return null()
    if ptr_is_null(utf8) == 0:
        if byte_len > 0:
            memmove(ptr_add(s, 40), utf8, byte_len)
    return s
