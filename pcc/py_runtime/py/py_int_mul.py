"""pcc-Python replacement for py_runtime/src/py_int_mul.c."""
from pcc.extern import extern, c_abi_export, c_int64, c_ptr
from pcc.unsafe import load_i32, ptr_is_null, store_i32


py_bigint_alloc = extern("py_bigint_alloc", (c_int64,), c_ptr)

def _load_u32(obj, offset: int) -> int:
    v: int = load_i32(obj, offset)
    if v < 0:
        v = v + 4294967296
    return v


def _store_u32(obj, offset: int, value: int) -> None:
    store_i32(obj, offset, value)


def _normalize(b) -> None:
    ndigits: int = load_i32(b, 20)
    while ndigits > 0 and _load_u32(b, 24 + (ndigits - 1) * 4) == 0:
        ndigits = ndigits - 1
    store_i32(b, 20, ndigits)
    if ndigits == 0:
        store_i32(b, 16, 0)
    elif load_i32(b, 16) == 0:
        store_i32(b, 16, 1)


def _mul_u32_low(a: int, b: int) -> int:
    al: int = a & 65535
    ah: int = a >> 16
    bl: int = b & 65535
    bh: int = b >> 16
    p0: int = al * bl
    p1: int = al * bh + ah * bl + (p0 >> 16)
    return ((p1 & 65535) << 16) | (p0 & 65535)


def _mul_u32_high(a: int, b: int) -> int:
    al: int = a & 65535
    ah: int = a >> 16
    bl: int = b & 65535
    bh: int = b >> 16
    p0: int = al * bl
    p1: int = al * bh + ah * bl + (p0 >> 16)
    return ah * bh + (p1 >> 16)


@c_abi_export("py_bigint_mul")
def py_bigint_mul(a, b):
    if load_i32(a, 16) == 0 or load_i32(b, 16) == 0:
        return py_bigint_alloc(0)

    la: int = load_i32(a, 20)
    lb: int = load_i32(b, 20)
    r = py_bigint_alloc(la + lb)
    if ptr_is_null(r):
        return r

    i: int = 0
    while i < la:
        carry: int = 0
        av: int = _load_u32(a, 24 + i * 4)
        j: int = 0
        while j < lb:
            bv: int = _load_u32(b, 24 + j * 4)
            low: int = _mul_u32_low(av, bv)
            high: int = _mul_u32_high(av, bv)
            off: int = 24 + (i + j) * 4
            total: int = _load_u32(r, off) + low + carry
            _store_u32(r, off, total & 4294967295)
            carry = high + (total >> 32)
            j = j + 1
        _store_u32(r, 24 + (i + lb) * 4, carry & 4294967295)
        i = i + 1

    if load_i32(a, 16) == load_i32(b, 16):
        store_i32(r, 16, 1)
    else:
        store_i32(r, 16, -1)
    _normalize(r)
    return r
