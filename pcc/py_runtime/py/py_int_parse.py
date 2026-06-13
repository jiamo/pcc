"""Phase 4c: pcc-Python port of py_int_parse.c.

String-to-int parsing for int(str) / int(str, base). The arithmetic core
stays in py_int.c for now; this module only parses an int64 payload and
delegates canonical tagged-vs-heap construction to py_int_from_i64.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import cstr, load_i8, store_i8, malloc, free, strlen, null, ptr_is_null


py_int_from_i64 = extern("py_int_from_i64", (c_int64,),       c_ptr)
py_int_mul      = extern("py_int_mul",      (c_ptr, c_ptr),   c_ptr)
py_int_add      = extern("py_int_add",      (c_ptr, c_ptr),   c_ptr)
py_decref       = extern("py_decref",       (c_ptr,),         c_void)
py_raise        = extern("py_raise",        (c_ptr,),         c_void)
py_exc_new      = extern("py_exc_new",      (c_int64, c_ptr), c_ptr)


def _byte_at(s, i: int) -> int:
    return load_i8(s, i) & 0xFF


def _is_space(c: int) -> int:
    if c == 32:
        return 1
    if c == 9:
        return 1
    if c == 10:
        return 1
    if c == 13:
        return 1
    return 0


def _digit_value(c: int) -> int:
    if c >= 48:
        if c <= 57:
            return c - 48
    if c >= 97:
        if c <= 122:
            return c - 97 + 10
    if c >= 65:
        if c <= 90:
            return c - 65 + 10
    return -1


def _has_prefix(s, i: int, lo: int, hi: int) -> int:
    if _byte_at(s, i) != 48:
        return 0
    c: int = _byte_at(s, i + 1)
    if c == lo:
        return 1
    if c == hi:
        return 1
    return 0


def _parse_bigint(s, start: int, base: int, negative: int):
    # Bigint fallback for decimals/values that exceed int64. Accumulates with
    # the general int ops (py_int_mul / py_int_add), which handle bignum.
    # ``start`` is the first digit position (after sign/prefix).
    base_obj = py_int_from_i64(base)
    acc = py_int_from_i64(0)
    i: int = start
    done: int = 0
    while done == 0:
        ch: int = _byte_at(s, i)
        if ch == 0:
            done = 1
        else:
            d: int = _digit_value(ch)
            if d < 0:
                done = 1
            elif d >= base:
                done = 1
            else:
                prod = py_int_mul(acc, base_obj)
                py_decref(acc)
                dobj = py_int_from_i64(d)
                acc = py_int_add(prod, dobj)
                py_decref(prod)
                py_decref(dobj)
                i = i + 1
    py_decref(base_obj)
    # Trailing: optional whitespace then NUL, matching the int64 path.
    while _is_space(_byte_at(s, i)) != 0:
        i = i + 1
    if _byte_at(s, i) != 0:
        py_decref(acc)
        return null()
    if negative != 0:
        neg_one = py_int_from_i64(-1)
        res = py_int_mul(acc, neg_one)
        py_decref(acc)
        py_decref(neg_one)
        return res
    return acc


@c_abi_export("py_int_from_cstr")
def py_int_from_cstr(s, base: int):
    # NULL input matches the C helper's parse-error contract.
    if ptr_is_null(s):
        return null()

    i: int = 0
    while _is_space(_byte_at(s, i)) != 0:
        i = i + 1

    negative: int = 0
    c: int = _byte_at(s, i)
    if c == 43 or c == 45:
        if c == 45:
            negative = 1
        i = i + 1

    if base == 0:
        if _has_prefix(s, i, 120, 88) != 0:
            base = 16
            i = i + 2
        elif _has_prefix(s, i, 98, 66) != 0:
            base = 2
            i = i + 2
        elif _has_prefix(s, i, 111, 79) != 0:
            base = 8
            i = i + 2
        elif _byte_at(s, i) == 48:
            base = 8
        else:
            base = 10
    elif base == 16:
        if _has_prefix(s, i, 120, 88) != 0:
            i = i + 2
    elif base == 2:
        if _has_prefix(s, i, 98, 66) != 0:
            i = i + 2
    elif base == 8:
        if _has_prefix(s, i, 111, 79) != 0:
            i = i + 2

    if base < 2:
        return null()
    if base > 36:
        return null()

    limit: int = 9223372036854775807
    max_div: int = limit // base
    max_rem: int = limit - max_div * base
    neg_div: int = max_div
    neg_rem: int = max_rem
    if negative != 0:
        if neg_rem == base - 1:
            neg_div = neg_div + 1
            neg_rem = 0
        else:
            neg_rem = neg_rem + 1

    digits_start: int = i
    value: int = 0
    saw_digit: int = 0
    min_exact: int = 0
    done: int = 0
    while done == 0:
        ch: int = _byte_at(s, i)
        if ch == 0:
            done = 1
        else:
            d: int = _digit_value(ch)
            if d < 0:
                done = 1
            elif d >= base:
                done = 1
            else:
                div_limit: int = max_div
                rem_limit: int = max_rem
                if negative != 0:
                    div_limit = neg_div
                    rem_limit = neg_rem
                if value > div_limit:
                    return _parse_bigint(s, digits_start, base, negative)
                if value == div_limit:
                    if d > rem_limit:
                        return _parse_bigint(s, digits_start, base, negative)
                    if negative != 0:
                        if value > max_div:
                            min_exact = 1
                        elif value == max_div:
                            if d > max_rem:
                                min_exact = 1
                saw_digit = 1
                i = i + 1
                if min_exact != 0:
                    done = 1
                else:
                    value = value * base + d

    if saw_digit == 0:
        return null()

    while _is_space(_byte_at(s, i)) != 0:
        i = i + 1
    if _byte_at(s, i) != 0:
        return null()

    if negative != 0:
        if min_exact != 0:
            min_i64: int = -9223372036854775807
            min_i64 = min_i64 - 1
            return py_int_from_i64(min_i64)
        return py_int_from_i64(0 - value)
    return py_int_from_i64(value)


def _repr_append_byte(buf, n: int, c: int, quote: int) -> int:
    # Append the CPython-style repr of source byte ``c`` into ``buf`` at
    # position ``n``, using ``quote`` (a byte value) as the active quote so only
    # the active quote is backslash-escaped. Mirrors repr_append_byte in
    # py_int_parse.c. ``buf`` must have room for the worst case (4 bytes).
    if c == 92 or c == quote:  # '\\' or the active quote
        store_i8(buf, n, 92)
        store_i8(buf, n + 1, c)
        return n + 2
    if c == 10:  # '\n'
        store_i8(buf, n, 92)
        store_i8(buf, n + 1, 110)  # 'n'
        return n + 2
    if c == 13:  # '\r'
        store_i8(buf, n, 92)
        store_i8(buf, n + 1, 114)  # 'r'
        return n + 2
    if c == 9:  # '\t'
        store_i8(buf, n, 92)
        store_i8(buf, n + 1, 116)  # 't'
        return n + 2
    if c < 32 or c == 127:
        store_i8(buf, n, 92)       # '\\'
        store_i8(buf, n + 1, 120)  # 'x'
        hi: int = (c >> 4) & 15
        lo: int = c & 15
        if hi < 10:
            store_i8(buf, n + 2, 48 + hi)
        else:
            store_i8(buf, n + 2, 87 + hi)  # 'a'(97) - 10
        if lo < 10:
            store_i8(buf, n + 3, 48 + lo)
        else:
            store_i8(buf, n + 3, 87 + lo)
        return n + 4
    store_i8(buf, n, c)
    return n + 1


def _build_bad_literal_message(s, base: int):
    # Build "invalid literal for int() with base <base>: <repr(s)>" into a
    # freshly malloc'd NUL-terminated buffer; caller frees. ``base`` is the
    # ORIGINAL base argument (0 renders as "base 0"). Mirrors
    # build_bad_literal_message in py_int_parse.c. Returns null() on OOM.
    slen: int = strlen(s)
    # CPython quote selection: single quote unless the string has a single
    # quote but no double quote.
    quote: int = 39  # '\''
    has_single: int = 0
    has_double: int = 0
    i: int = 0
    while i < slen:
        b: int = _byte_at(s, i)
        if b == 39:
            has_single = 1
        elif b == 34:
            has_double = 1
        i = i + 1
    if has_single != 0:
        if has_double == 0:
            quote = 34  # '"'

    # Prefix: "invalid literal for int() with base " (36 bytes).
    prefix_len: int = 36
    cap: int = prefix_len + 24 + 2 + slen * 4 + 1
    buf = malloc(cap)
    if ptr_is_null(buf):
        return null()
    prefix = cstr("invalid literal for int() with base ")
    n: int = 0
    while n < prefix_len:
        store_i8(buf, n, _byte_at(prefix, n))
        n = n + 1

    # Render base as signed decimal (handles 0 and defensively negatives).
    neg: int = 0
    ub: int = base
    if base < 0:
        neg = 1
        ub = 0 - base
    # Collect decimal digits (reverse), then append forward.
    digit_buf = malloc(24)
    d: int = 0
    if ub == 0:
        store_i8(digit_buf, 0, 48)  # '0'
        d = 1
    else:
        while ub != 0:
            store_i8(digit_buf, d, 48 + (ub % 10))
            ub = ub // 10
            d = d + 1
    if neg != 0:
        store_i8(buf, n, 45)  # '-'
        n = n + 1
    while d > 0:
        d = d - 1
        store_i8(buf, n, _byte_at(digit_buf, d))
        n = n + 1
    free(digit_buf)

    store_i8(buf, n, 58)      # ':'
    n = n + 1
    store_i8(buf, n, 32)      # ' '
    n = n + 1
    store_i8(buf, n, quote)
    n = n + 1
    i = 0
    while i < slen:
        n = _repr_append_byte(buf, n, _byte_at(s, i), quote)
        i = i + 1
    store_i8(buf, n, quote)
    n = n + 1
    store_i8(buf, n, 0)       # NUL
    return buf


@c_abi_export("py_int_from_cstr_or_raise")
def py_int_from_cstr_or_raise(s, base: int):
    # int(str) builtin: parse like py_int_from_cstr but raise ValueError on
    # invalid input instead of returning NULL (which the frontend would unbox to
    # 0 -> int('xyz') silently became 0). Mirrors py_int_from_cstr_or_raise in
    # py_int_parse.c; py_int_from_cstr stays NULL-returning for other callers.
    #
    # CPython raises two distinct ValueError messages, reproduced here:
    #   - bad base (not 0 and outside 2..36):
    #       "int() base must be >= 2 and <= 36, or 0"
    #   - unparseable literal (valid base):
    #       "invalid literal for int() with base <base>: <repr(s)>"
    # ``base`` is the ORIGINAL argument (0 stays "base 0"); the repr is a
    # CPython-accurate repr of the whole original string.
    if base != 0:
        if base < 2 or base > 36:
            py_raise(py_exc_new(2, cstr("int() base must be >= 2 and <= 36, or 0")))
            return null()
    v = py_int_from_cstr(s, base)
    if ptr_is_null(v) == 0:
        return v
    src = s
    if ptr_is_null(s):
        src = cstr("")
    msg = _build_bad_literal_message(src, base)
    if ptr_is_null(msg) == 0:
        py_raise(py_exc_new(2, msg))  # PY_EXC_VALUEERROR
        free(msg)
    else:
        py_raise(py_exc_new(2, cstr("invalid literal for int()")))
    return null()
