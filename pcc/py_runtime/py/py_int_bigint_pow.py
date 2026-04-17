"""pcc-Python replacement for py_runtime/src/py_int_bigint_pow.c."""
from pcc.extern import extern, c_abi_export, c_int64, c_ptr
from pcc.unsafe import free, load_i32, null, ptr_is_null, store_i32


py_bigint_alloc = extern("py_bigint_alloc", (c_int64,), c_ptr)
py_bigint_from_i64 = extern("py_bigint_from_i64", (c_int64,), c_ptr)
py_bigint_mul = extern("py_bigint_mul", (c_ptr, c_ptr), c_ptr)


def _load_u32(obj, offset: int) -> int:
    v: int = load_i32(obj, offset)
    if v < 0:
        v = v + 4294967296
    return v


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


@c_abi_export("py_bigint_pow")
def py_bigint_pow(base, exp):
    if load_i32(exp, 16) < 0:
        return null()
    if load_i32(exp, 16) == 0:
        return py_bigint_from_i64(1)

    top_digit: int = load_i32(exp, 20) - 1
    top: int = _load_u32(exp, 24 + top_digit * 4)
    top_bit: int = 31
    while top_bit >= 0 and (top & (1 << top_bit)) == 0:
        top_bit = top_bit - 1

    result = _bigint_copy(base)
    if ptr_is_null(result):
        return result

    di: int = top_digit
    bit: int = top_bit - 1
    while di >= 0:
        digit: int = _load_u32(exp, 24 + di * 4)
        while bit >= 0:
            sq = py_bigint_mul(result, result)
            free(result)
            if ptr_is_null(sq):
                return null()
            result = sq
            if (digit & (1 << bit)) != 0:
                m = py_bigint_mul(result, base)
                free(result)
                if ptr_is_null(m):
                    return null()
                result = m
            bit = bit - 1
        di = di - 1
        bit = 31
    return result
