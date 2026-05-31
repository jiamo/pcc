"""pcc-Python replacement for most public py_int_* operation dispatch."""
from pcc.extern import extern, c_abi_export, c_double, c_int64, c_ptr, c_void
from pcc.unsafe import (
    cstr,
    free,
    is_tagged_int,
    load_i32,
    load_ptr,
    malloc,
    null,
    ptr_is_null,
    store_f64,
    store_i32,
    store_i64,
    untag_int,
)


py_incref = extern("py_incref", (c_ptr,), c_void)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_bigint_from_any = extern("py_bigint_from_any", (c_ptr,), c_ptr)
py_bigint_to_pyobject = extern("py_bigint_to_pyobject", (c_ptr,), c_ptr)
py_bigint_add = extern("py_bigint_add", (c_ptr, c_ptr), c_ptr)
py_bigint_sub = extern("py_bigint_sub", (c_ptr, c_ptr), c_ptr)
py_bigint_mul = extern("py_bigint_mul", (c_ptr, c_ptr), c_ptr)
py_bigint_neg = extern("py_bigint_neg", (c_ptr,), c_ptr)
py_bigint_divmod = extern("py_bigint_divmod", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64)
py_bigint_pow = extern("py_bigint_pow", (c_ptr, c_ptr), c_ptr)
py_bigint_to_double = extern("py_bigint_to_double", (c_ptr,), c_double)
py_bigint_and = extern("py_bigint_and", (c_ptr, c_ptr), c_ptr)
py_bigint_or = extern("py_bigint_or", (c_ptr, c_ptr), c_ptr)
py_bigint_xor = extern("py_bigint_xor", (c_ptr, c_ptr), c_ptr)
py_bigint_shl = extern("py_bigint_shl", (c_ptr, c_int64), c_ptr)
py_bigint_shr = extern("py_bigint_shr", (c_ptr, c_int64), c_ptr)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
pow_c = extern("pow", (c_double, c_double), c_double)


def _load_u32(obj, offset: int) -> int:
    v: int = load_i32(obj, offset)
    if v < 0:
        v = v + 4294967296
    return v


def _both_tagged(a, b) -> bool:
    return is_tagged_int(a) and is_tagged_int(b)


def _wrap_bigint(b):
    if ptr_is_null(b):
        return null()
    return py_bigint_to_pyobject(b)


def _binary_bigint(a, b, op: int):
    ba = py_bigint_from_any(a)
    bb = py_bigint_from_any(b)
    if ptr_is_null(ba) or ptr_is_null(bb):
        free(ba)
        free(bb)
        return null()
    br = null()
    if op == 0:
        br = py_bigint_add(ba, bb)
    elif op == 1:
        br = py_bigint_sub(ba, bb)
    elif op == 2:
        br = py_bigint_mul(ba, bb)
    elif op == 3:
        br = py_bigint_and(ba, bb)
    elif op == 4:
        br = py_bigint_or(ba, bb)
    else:
        br = py_bigint_xor(ba, bb)
    free(ba)
    free(bb)
    return _wrap_bigint(br)


def _heap_int_fits_i64(o) -> bool:
    if ptr_is_null(o):
        return False
    if load_i32(o, 8) != 2:
        return False
    sign: int = load_i32(o, 16)
    if sign == 0:
        return True
    ndigits: int = load_i32(o, 20)
    if ndigits <= 0:
        return True
    if ndigits > 2:
        return False
    low: int = _load_u32(o, 24)
    high: int = 0
    if ndigits == 2:
        high = _load_u32(o, 28)
    if sign > 0:
        return high <= 2147483647
    if high < 2147483648:
        return True
    return high == 2147483648 and low == 0


def _heap_int_i64_value(o) -> int:
    sign: int = load_i32(o, 16)
    if sign == 0:
        return 0
    ndigits: int = load_i32(o, 20)
    if ndigits <= 0:
        return 0
    low: int = _load_u32(o, 24)
    high: int = 0
    if ndigits == 2:
        high = _load_u32(o, 28)
    if sign > 0:
        return high * 4294967296 + low
    if high == 2147483648 and low == 0:
        return -9223372036854775807 - 1
    return 0 - (high * 4294967296 + low)


def _int_fits_i64(o) -> bool:
    if is_tagged_int(o):
        return True
    return _heap_int_fits_i64(o)


def _int_i64_value(o) -> int:
    if is_tagged_int(o):
        return untag_int(o)
    return _heap_int_i64_value(o)


def _int_to_double(o) -> float:
    if is_tagged_int(o):
        return float(untag_int(o))
    return py_bigint_to_double(o)


def _float_new(v: float):
    f = malloc(24)
    if ptr_is_null(f):
        return f
    store_i64(f, 0, 1)
    store_i32(f, 8, 3)
    store_i32(f, 12, 0)
    store_f64(f, 16, v)
    return f


@c_abi_export("py_int_add")
def py_int_add(a, b):
    if _both_tagged(a, b):
        return py_int_from_i64(untag_int(a) + untag_int(b))
    return _binary_bigint(a, b, 0)


@c_abi_export("py_int_sub")
def py_int_sub(a, b):
    if _both_tagged(a, b):
        return py_int_from_i64(untag_int(a) - untag_int(b))
    return _binary_bigint(a, b, 1)


@c_abi_export("py_int_mul")
def py_int_mul(a, b):
    if _both_tagged(a, b):
        av: int = untag_int(a)
        bv: int = untag_int(b)
        if av >= -3037000499 and av <= 3037000499:
            if bv >= -3037000499 and bv <= 3037000499:
                return py_int_from_i64(av * bv)
    return _binary_bigint(a, b, 2)


@c_abi_export("py_int_neg")
def py_int_neg(a):
    if is_tagged_int(a):
        return py_int_from_i64(0 - untag_int(a))
    ba = py_bigint_from_any(a)
    if ptr_is_null(ba):
        return null()
    br = py_bigint_neg(ba)
    free(ba)
    return _wrap_bigint(br)


@c_abi_export("py_int_floordiv")
def py_int_floordiv(a, b):
    if _both_tagged(a, b):
        av: int = untag_int(a)
        bv: int = untag_int(b)
        if bv == 0:
            return null()
        # Tagged-int fast path. ``av`` and ``bv`` are in [-2^62, 2^62-1]
        # so ``av // bv`` cannot overflow i64.
        return py_int_from_i64(av // bv)
    ba = py_bigint_from_any(a)
    bb = py_bigint_from_any(b)
    if ptr_is_null(ba) or ptr_is_null(bb):
        free(ba)
        free(bb)
        return null()
    qslot = malloc(8)
    rslot = malloc(8)
    if ptr_is_null(qslot) or ptr_is_null(rslot):
        free(ba)
        free(bb)
        free(qslot)
        free(rslot)
        return null()
    ok: int = py_bigint_divmod(ba, bb, qslot, rslot)
    free(ba)
    free(bb)
    if ok != 0:
        free(qslot)
        free(rslot)
        return null()
    q = load_ptr(qslot, 0)
    r = load_ptr(rslot, 0)
    free(qslot)
    free(rslot)
    free(r)
    return _wrap_bigint(q)


@c_abi_export("py_int_mod")
def py_int_mod(a, b):
    if _both_tagged(a, b):
        av: int = untag_int(a)
        bv: int = untag_int(b)
        if bv == 0:
            return null()
        # Tagged-int fast path. ``a % b`` in pcc-Python under
        # ``pcc.unsafe`` lowers to a Python-semantics modulo (srem +
        # sign correction in codegen), so we get Python mod sign
        # convention directly.
        return py_int_from_i64(av % bv)
    ba = py_bigint_from_any(a)
    bb = py_bigint_from_any(b)
    if ptr_is_null(ba) or ptr_is_null(bb):
        free(ba)
        free(bb)
        return null()
    qslot = malloc(8)
    rslot = malloc(8)
    if ptr_is_null(qslot) or ptr_is_null(rslot):
        free(ba)
        free(bb)
        free(qslot)
        free(rslot)
        return null()
    ok: int = py_bigint_divmod(ba, bb, qslot, rslot)
    free(ba)
    free(bb)
    if ok != 0:
        free(qslot)
        free(rslot)
        return null()
    q = load_ptr(qslot, 0)
    r = load_ptr(rslot, 0)
    free(qslot)
    free(rslot)
    free(q)
    return _wrap_bigint(r)


@c_abi_export("py_int_truediv")
def py_int_truediv(a, b):
    if is_tagged_int(b):
        if untag_int(b) == 0:
            return null()
    elif load_i32(b, 16) == 0:
        return null()
    return _float_new(_int_to_double(a) / _int_to_double(b))


@c_abi_export("py_int_pow")
def py_int_pow(a, b):
    if is_tagged_int(b):
        ev: int = untag_int(b)
        if ev < 0:
            return _float_new(pow_c(_int_to_double(a), float(ev)))
    elif load_i32(b, 16) < 0:
        return null()

    ba = py_bigint_from_any(a)
    bb = py_bigint_from_any(b)
    if ptr_is_null(ba) or ptr_is_null(bb):
        free(ba)
        free(bb)
        return null()
    br = py_bigint_pow(ba, bb)
    free(ba)
    free(bb)
    return _wrap_bigint(br)


@c_abi_export("py_int_and")
def py_int_and(a, b):
    if _both_tagged(a, b):
        return py_int_from_i64(untag_int(a) & untag_int(b))
    return _binary_bigint(a, b, 3)


@c_abi_export("py_int_or")
def py_int_or(a, b):
    if _both_tagged(a, b):
        return py_int_from_i64(untag_int(a) | untag_int(b))
    return _binary_bigint(a, b, 4)


@c_abi_export("py_int_xor")
def py_int_xor(a, b):
    if _both_tagged(a, b):
        return py_int_from_i64(untag_int(a) ^ untag_int(b))
    return _binary_bigint(a, b, 5)


@c_abi_export("py_int_shl")
def py_int_shl(a, b):
    if not _int_fits_i64(b):
        return null()
    n: int = _int_i64_value(b)
    if n < 0:
        py_raise(py_exc_new(2, cstr("negative shift count")))
        return null()
    if n == 0:
        if is_tagged_int(a):
            return a
        py_incref(a)
        return a
    if is_tagged_int(a) and n < 63:
        factor: int = 1 << n
        av: int = untag_int(a)
        limit: int = 9223372036854775807 // factor
        if av >= (0 - limit) and av <= limit:
            return py_int_from_i64(av * factor)
    ba = py_bigint_from_any(a)
    if ptr_is_null(ba):
        return null()
    br = py_bigint_shl(ba, n)
    free(ba)
    return _wrap_bigint(br)


@c_abi_export("py_int_shr")
def py_int_shr(a, b):
    if not _int_fits_i64(b):
        return null()
    n: int = _int_i64_value(b)
    if n < 0:
        py_raise(py_exc_new(2, cstr("negative shift count")))
        return null()
    if n == 0:
        if is_tagged_int(a):
            return a
        py_incref(a)
        return a
    if is_tagged_int(a):
        av: int = untag_int(a)
        if n >= 63:
            if av < 0:
                return py_int_from_i64(-1)
            return py_int_from_i64(0)
        return py_int_from_i64(av >> n)
    ba = py_bigint_from_any(a)
    if ptr_is_null(ba):
        return null()
    br = py_bigint_shr(ba, n)
    free(ba)
    return _wrap_bigint(br)
