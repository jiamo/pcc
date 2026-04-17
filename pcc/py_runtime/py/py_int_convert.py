"""Phase 4c: pcc-Python port of py_int_convert.c.

Converts tagged or heap ints to int64 for runtime callers that need a
native scalar and an overflow flag. Tagged-int decoding still uses the
existing C helper until the unsafe pointer/integer intrinsic exists.

PyIntObject layout:
    offset  0   PyObjectHeader
    offset 16   sign       (i32)
    offset 20   ndigits    (i32)
    offset 24   digits[]   (u32 little-endian)
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64
from pcc.unsafe import is_tagged_int, load_i32, ptr_is_null, store_i32


py_int_value_i64     = extern("py_int_value_i64",     (c_ptr,),                  c_int64)


def _set_overflow(slot, value: int) -> None:
    if not ptr_is_null(slot):
        store_i32(slot, 0, value)


def _load_u32(obj, offset: int) -> int:
    v: int = load_i32(obj, offset)
    if v < 0:
        v = v + 4294967296
    return v


@c_abi_export("py_int_to_i64")
def py_int_to_i64(o, overflow) -> int:
    _set_overflow(overflow, 0)
    if ptr_is_null(o):
        _set_overflow(overflow, 1)
        return 0
    if is_tagged_int(o):
        return py_int_value_i64(o)
    if load_i32(o, 8) != 2:
        _set_overflow(overflow, 1)
        return 0

    sign: int = load_i32(o, 16)
    if sign == 0:
        return 0
    ndigits: int = load_i32(o, 20)
    if ndigits <= 0:
        return 0
    if ndigits > 2:
        _set_overflow(overflow, 1)
        return 0

    low: int = _load_u32(o, 24)
    high: int = 0
    if ndigits == 2:
        high = _load_u32(o, 28)

    if sign > 0:
        if high > 2147483647:
            _set_overflow(overflow, 1)
            return 0
        return high * 4294967296 + low

    if high > 2147483648:
        _set_overflow(overflow, 1)
        return 0
    if high == 2147483648:
        if low != 0:
            _set_overflow(overflow, 1)
            return 0
        min_i64: int = -9223372036854775807
        return min_i64 - 1
    return 0 - (high * 4294967296 + low)
