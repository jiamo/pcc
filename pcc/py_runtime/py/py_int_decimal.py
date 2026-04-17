"""pcc-Python replacement for py_runtime/src/py_int_decimal.c."""
from pcc.extern import extern, c_abi_export, c_int64, c_ptr
from pcc.unsafe import (
    free,
    load_i8,
    load_i32,
    malloc,
    memmove,
    null,
    ptr_add,
    ptr_is_null,
    store_i8,
    store_i32,
)


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


def _divmod_small_inplace(a, divisor: int) -> int:
    rem: int = 0
    i: int = load_i32(a, 20) - 1
    while i >= 0:
        cur: int = rem * 4294967296 + _load_u32(a, 24 + i * 4)
        _store_u32(a, 24 + i * 4, cur // divisor)
        rem = cur % divisor
        i = i - 1
    _normalize(a)
    return rem


def _write_digit(buf, pos: int, digit: int) -> int:
    pos = pos - 1
    store_i8(buf, pos, 48 + digit)
    return pos


@c_abi_export("py_bigint_to_cstr")
def py_bigint_to_cstr(b):
    if load_i32(b, 16) == 0:
        s = malloc(2)
        if ptr_is_null(s):
            return s
        store_i8(s, 0, 48)
        store_i8(s, 1, 0)
        return s

    ndigits: int = load_i32(b, 20)
    bufsz: int = ndigits * 10 + 2
    buf = malloc(bufsz)
    if ptr_is_null(buf):
        return buf

    tmp = _bigint_copy(b)
    if ptr_is_null(tmp):
        free(buf)
        return null()
    store_i32(tmp, 16, 1)

    pos: int = bufsz - 1
    store_i8(buf, pos, 0)

    while load_i32(tmp, 20) > 0:
        rem: int = _divmod_small_inplace(tmp, 1000000000)
        more: bool = load_i32(tmp, 20) > 0
        if not more:
            if rem == 0:
                pos = _write_digit(buf, pos, 0)
            else:
                while rem > 0:
                    pos = _write_digit(buf, pos, rem % 10)
                    rem = rem // 10
        else:
            k: int = 0
            while k < 9:
                pos = _write_digit(buf, pos, rem % 10)
                rem = rem // 10
                k = k + 1
    free(tmp)

    if load_i32(b, 16) < 0:
        pos = pos - 1
        store_i8(buf, pos, 45)

    memmove(buf, ptr_add(buf, pos), bufsz - pos)
    return buf


@c_abi_export("py_bigint_from_cstr")
def py_bigint_from_cstr(s):
    if ptr_is_null(s):
        return null()
    p = s
    sign: int = 1
    ch: int = load_i8(p, 0)
    if ch == 43:
        p = ptr_add(p, 1)
    elif ch == 45:
        sign = -1
        p = ptr_add(p, 1)
    if load_i8(p, 0) == 0:
        return null()

    acc = py_bigint_alloc(0)
    if ptr_is_null(acc):
        return acc

    while load_i8(p, 0) != 0:
        chunk: int = 0
        mul: int = 1
        count: int = 0
        ch = load_i8(p, 0)
        while ch != 0 and count < 9:
            if ch < 48 or ch > 57:
                free(acc)
                return null()
            chunk = chunk * 10 + (ch - 48)
            mul = mul * 10
            count = count + 1
            p = ptr_add(p, 1)
            ch = load_i8(p, 0)
        if count == 0:
            free(acc)
            return null()

        la: int = load_i32(acc, 20)
        nxt = py_bigint_alloc(la + 1)
        if ptr_is_null(nxt):
            free(acc)
            return null()

        carry: int = 0
        i: int = 0
        while i < la:
            cur: int = _load_u32(acc, 24 + i * 4) * mul + carry
            _store_u32(nxt, 24 + i * 4, cur & 4294967295)
            carry = cur >> 32
            i = i + 1
        _store_u32(nxt, 24 + la * 4, carry)

        carry = chunk
        i = 0
        while i < load_i32(nxt, 20) and carry != 0:
            cur = _load_u32(nxt, 24 + i * 4) + carry
            _store_u32(nxt, 24 + i * 4, cur & 4294967295)
            carry = cur >> 32
            i = i + 1

        store_i32(nxt, 16, 1)
        _normalize(nxt)
        free(acc)
        acc = nxt

    if load_i32(acc, 20) == 0:
        store_i32(acc, 16, 0)
    else:
        store_i32(acc, 16, sign)
    return acc
