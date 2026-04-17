"""Phase 4c: pcc-Python port of py_int_parse.c.

String-to-int parsing for int(str) / int(str, base). The arithmetic core
stays in py_int.c for now; this module only parses an int64 payload and
delegates canonical tagged-vs-heap construction to py_int_from_i64.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64
from pcc.unsafe import load_i8, null, ptr_is_null


py_int_from_i64 = extern("py_int_from_i64", (c_int64,),       c_ptr)


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
                    return null()
                if value == div_limit:
                    if d > rem_limit:
                        return null()
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
