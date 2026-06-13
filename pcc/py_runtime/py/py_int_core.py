"""pcc-Python replacement for py_runtime/src/py_int_core.c.

This slice owns the int object boundary: tagged-int encode/decode,
small heap bignum allocation, canonical PyObject conversion, and the
double conversion used by true division. The heavier arithmetic still
lives in py_int.c for now.
"""
from pcc.extern import c_abi_export
from pcc.unsafe import (
    free,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    malloc,
    null,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    tag_int,
    untag_int,
)


def _load_u32(obj, offset: int) -> int:
    v: int = load_i32(obj, offset)
    if v < 0:
        v = v + 4294967296
    return v


def _store_u32(obj, offset: int, value: int) -> None:
    store_i32(obj, offset, value)


def _bigint_copy(a):
    ndigits: int = load_i32(a, 20)
    r = py_bigint_alloc(ndigits)
    if ptr_is_null(r):
        return r
    store_i32(r, 16, load_i32(a, 16))
    i: int = 0
    while i < ndigits:
        store_i32(r, 24 + i * 4, load_i32(a, 24 + i * 4))
        i = i + 1
    return r


def _bigint_abs_cmp(a, b) -> int:
    na: int = load_i32(a, 20)
    nb: int = load_i32(b, 20)
    if na != nb:
        if na < nb:
            return -1
        return 1
    i: int = na - 1
    while i >= 0:
        av: int = _load_u32(a, 24 + i * 4)
        bv: int = _load_u32(b, 24 + i * 4)
        if av != bv:
            if av < bv:
                return -1
            return 1
        i = i - 1
    return 0


def _bigint_tagged_fit_value(b) -> int:
    sign: int = load_i32(b, 16)
    if sign == 0:
        return 0
    ndigits: int = load_i32(b, 20)
    if ndigits <= 0:
        return 0
    if ndigits > 2:
        return 0

    low: int = _load_u32(b, 24)
    high: int = 0
    if ndigits == 2:
        high = _load_u32(b, 28)

    if sign > 0:
        if high > 1073741823:
            return 0
        return high * 4294967296 + low

    if high > 1073741824:
        return 0
    if high == 1073741824:
        if low != 0:
            return 0
        return -4611686018427387904
    return 0 - (high * 4294967296 + low)


@c_abi_export("py_int_bit_length")
def py_int_bit_length(n) -> int:
    # int.bit_length(): bits to represent abs(value), 0 for 0. Exact for bignums:
    # (ndigits-1)*32 + bits in the top base-2^32 digit. Mirrors py_int_bit_length
    # in py_int_core.c. tagged-int path only sees i63-range values (negatable).
    if ptr_is_null(n) != 0:
        return 0
    if is_tagged_int(n) != 0:
        a: int = untag_int(n)
        if a < 0:
            a = 0 - a
        bits: int = 0
        while a > 0:
            bits = bits + 1
            a = a >> 1
        return bits
    tag: int = load_i32(n, 8)
    if tag == 2:                            # PY_TYPE_INT bignum
        ndigits: int = load_i32(n, 20)
        if ndigits <= 0:
            return 0
        top: int = _load_u32(n, 24 + (ndigits - 1) * 4)
        top_bits: int = 0
        while top > 0:
            top_bits = top_bits + 1
            top = top >> 1
        return (ndigits - 1) * 32 + top_bits
    return 0


@c_abi_export("py_int_bit_count")
def py_int_bit_count(n) -> int:
    # int.bit_count(): number of set bits in abs(value), 0 for 0. CPython counts
    # the magnitude, so negatives match their absolute value:
    # (-255).bit_count() == 8. Exact for bignums: popcount each base-2^32 limb
    # (limbs store the magnitude; sign is separate). Mirrors py_int_bit_count in
    # py_int_core.c. tagged-int path only sees i63-range values (negatable).
    if ptr_is_null(n) != 0:
        return 0
    if is_tagged_int(n) != 0:
        a: int = untag_int(n)
        if a < 0:
            a = 0 - a
        bits: int = 0
        while a > 0:
            bits = bits + (a & 1)
            a = a >> 1
        return bits
    tag: int = load_i32(n, 8)
    if tag == 2:                            # PY_TYPE_INT bignum
        ndigits: int = load_i32(n, 20)
        total: int = 0
        i: int = 0
        while i < ndigits:
            d: int = _load_u32(n, 24 + i * 4)
            while d > 0:
                total = total + (d & 1)
                d = d >> 1
            i = i + 1
        return total
    return 0


def _bigint_fits_tagged(b) -> bool:
    sign: int = load_i32(b, 16)
    if sign == 0:
        return True
    ndigits: int = load_i32(b, 20)
    if ndigits <= 0:
        return True
    if ndigits > 2:
        return False

    low: int = _load_u32(b, 24)
    high: int = 0
    if ndigits == 2:
        high = _load_u32(b, 28)

    if sign > 0:
        return high <= 1073741823
    if high < 1073741824:
        return True
    return high == 1073741824 and low == 0


def _bigint_i64_clamped(b) -> int:
    sign: int = load_i32(b, 16)
    if sign == 0:
        return 0
    ndigits: int = load_i32(b, 20)
    if ndigits <= 0:
        return 0
    if ndigits > 2:
        if sign < 0:
            return -9223372036854775807 - 1
        return 9223372036854775807

    low: int = _load_u32(b, 24)
    high: int = 0
    if ndigits == 2:
        high = _load_u32(b, 28)

    if sign > 0:
        if high > 2147483647:
            return 9223372036854775807
        return high * 4294967296 + low

    if high > 2147483648:
        return -9223372036854775807 - 1
    if high == 2147483648:
        if low != 0:
            return -9223372036854775807 - 1
        return -9223372036854775807 - 1
    return 0 - (high * 4294967296 + low)


@c_abi_export("py_bigint_alloc")
def py_bigint_alloc(ndigits: int):
    if ndigits < 0:
        ndigits = 0
    b = malloc(24 + ndigits * 4)
    if ptr_is_null(b):
        return b
    store_i64(b, 0, 1)
    store_i32(b, 8, 2)
    store_i32(b, 12, 0)
    store_i32(b, 16, 0)
    store_i32(b, 20, ndigits)
    i: int = 0
    while i < ndigits:
        store_i32(b, 24 + i * 4, 0)
        i = i + 1
    return b


@c_abi_export("py_bigint_from_i64")
def py_bigint_from_i64(v: int):
    b = py_bigint_alloc(2)
    if ptr_is_null(b):
        return b
    if v == 0:
        store_i32(b, 16, 0)
        store_i32(b, 20, 0)
        return b

    min_i64: int = -9223372036854775807 - 1
    if v == min_i64:
        store_i32(b, 16, -1)
        _store_u32(b, 24, 0)
        _store_u32(b, 28, 2147483648)
        store_i32(b, 20, 2)
        return b

    u: int = v
    if v < 0:
        store_i32(b, 16, -1)
        u = 0 - v
    else:
        store_i32(b, 16, 1)

    low: int = u & 4294967295
    high: int = u >> 32
    _store_u32(b, 24, low)
    _store_u32(b, 28, high)
    if high != 0:
        store_i32(b, 20, 2)
    else:
        store_i32(b, 20, 1)
    return b


@c_abi_export("py_bigint_to_pyobject")
def py_bigint_to_pyobject(b):
    if ptr_is_null(b):
        return b
    if _bigint_fits_tagged(b):
        v: int = _bigint_tagged_fit_value(b)
        free(b)
        return tag_int(v)
    return b


@c_abi_export("py_bigint_from_any")
def py_bigint_from_any(o):
    if is_tagged_int(o):
        return py_bigint_from_i64(untag_int(o))
    if ptr_is_null(o):
        return null()
    tag = load_i32(o, 8)
    # bool is-a int (CPython): True -> 1, False -> 0, so bool operands flow
    # through every int op (sum, +, *, ...) instead of failing as non-int.
    if tag == 1:
        if ptr_eq(o, global_load_ptr("py_True")) != 0:
            return py_bigint_from_i64(1)
        return py_bigint_from_i64(0)
    if tag != 2:
        return null()
    return _bigint_copy(o)


@c_abi_export("py_int_new_heap")
def py_int_new_heap(v: int):
    return py_bigint_from_i64(v)


@c_abi_export("py_int_value_i64")
def py_int_value_i64(o) -> int:
    if is_tagged_int(o):
        return untag_int(o)
    return _bigint_i64_clamped(o)


@c_abi_export("py_int_from_i64")
def py_int_from_i64(v: int):
    if v >= -4611686018427387904 and v <= 4611686018427387903:
        return tag_int(v)
    return py_bigint_from_i64(v)


@c_abi_export("py_bigint_to_double")
def py_bigint_to_double(b) -> float:
    if load_i32(b, 16) == 0:
        return 0.0
    r: float = 0.0
    i: int = load_i32(b, 20) - 1
    while i >= 0:
        r = r * 4294967296.0 + float(_load_u32(b, 24 + i * 4))
        i = i - 1
    if load_i32(b, 16) < 0:
        return 0.0 - r
    return r


@c_abi_export("py_bigint_neg")
def py_bigint_neg(a):
    r = _bigint_copy(a)
    if ptr_is_null(r):
        return r
    store_i32(r, 16, 0 - load_i32(r, 16))
    return r


@c_abi_export("py_bigint_cmp")
def py_bigint_cmp(a, b) -> int:
    sa: int = load_i32(a, 16)
    sb: int = load_i32(b, 16)
    if sa != sb:
        if sa < sb:
            return -1
        return 1
    if sa == 0:
        return 0
    mag: int = _bigint_abs_cmp(a, b)
    if sa > 0:
        return mag
    return 0 - mag


@c_abi_export("py_int_cmp")
def py_int_cmp(a, b) -> int:
    if is_tagged_int(a) and is_tagged_int(b):
        av: int = untag_int(a)
        bv: int = untag_int(b)
        if av < bv:
            return -1
        if av > bv:
            return 1
        return 0
    ba = py_bigint_from_any(a)
    bb = py_bigint_from_any(b)
    r: int = 0
    if not ptr_is_null(ba) and not ptr_is_null(bb):
        r = py_bigint_cmp(ba, bb)
    free(ba)
    free(bb)
    return r
