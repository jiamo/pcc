"""pcc-Python port of py_print_fmt.c.

High-level print formatting for runtime PyObject* values. Native scalar
float printing is still emitted directly by codegen; this module covers
object-path print(), list/tuple repr, and print_many.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    cstr,
    free,
    global_load_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i8,
    strlen,
    write,
)


py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_bigint_to_cstr = extern("py_bigint_to_cstr", (c_ptr,), c_ptr)
fflush = extern("fflush", (c_ptr,), c_int32)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)


def _write_cstr(p) -> None:
    if ptr_is_null(p) != 0:
        return
    n: int = strlen(p)
    if n > 0:
        write(1, p, n)


def _write_lit(p, n: int) -> None:
    write(1, p, n)


def _flush_stdio() -> None:
    # Keep ordering stable with codegen-emitted printf() calls for
    # native scalar print paths. fflush(NULL) flushes all output streams.
    fflush(null())


def _write_i64(v: int) -> None:
    if v == 0:
        _write_lit(cstr("0"), 1)
        return
    if v < 0:
        _write_lit(cstr("-"), 1)
        v = 0 - v
    buf = malloc(32)
    if ptr_is_null(buf) != 0:
        return
    n: int = 0
    while v > 0:
        digit: int = v % 10
        store_i8(buf, n, 48 + digit)
        n = n + 1
        v = v // 10
    i: int = n - 1
    while i >= 0:
        write(1, ptr_add(buf, i), 1)
        i = i - 1
    free(buf)


def _write_hex_nibble(v: int) -> None:
    ch: int = 0
    if v < 10:
        ch = 48 + v
    else:
        ch = 87 + v
    buf = malloc(1)
    if ptr_is_null(buf) != 0:
        return
    store_i8(buf, 0, ch)
    write(1, buf, 1)
    free(buf)


def _write_hex2(v: int) -> None:
    _write_hex_nibble((v >> 4) & 15)
    _write_hex_nibble(v & 15)


def _format_int(o) -> None:
    if is_tagged_int(o):
        _write_i64(py_int_value_i64(o))
        return
    s = py_bigint_to_cstr(o)
    if ptr_is_null(s) != 0:
        return
    _write_cstr(s)
    free(s)


def _format_str(o) -> None:
    n: int = load_i64(o, 16)
    if n > 0:
        write(1, ptr_add(o, 40), n)


def _format_str_repr(o) -> None:
    _write_lit(cstr("'"), 1)
    data = ptr_add(o, 40)
    n: int = load_i64(o, 16)
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 255
        if c == 92:                  # '\\'
            _write_lit(cstr("\\\\"), 2)
        elif c == 39:                # "'"
            _write_lit(cstr("\\'"), 2)
        elif c == 10:
            _write_lit(cstr("\\n"), 2)
        elif c == 13:
            _write_lit(cstr("\\r"), 2)
        elif c == 9:
            _write_lit(cstr("\\t"), 2)
        elif c < 32 or c == 127:
            _write_lit(cstr("\\x"), 2)
            _write_hex2(c)
        else:
            write(1, ptr_add(data, i), 1)
        i = i + 1
    _write_lit(cstr("'"), 1)


def _format_list(o) -> None:
    _write_lit(cstr("["), 1)
    length: int = load_i64(o, 16)
    items = load_ptr(o, 32)
    i: int = 0
    while i < length:
        if i > 0:
            _write_lit(cstr(", "), 2)
        _format_repr(load_ptr(items, i * 8))
        i = i + 1
    _write_lit(cstr("]"), 1)


def _format_tuple(o) -> None:
    _write_lit(cstr("("), 1)
    length: int = load_i64(o, 16)
    i: int = 0
    while i < length:
        if i > 0:
            _write_lit(cstr(", "), 2)
        _format_repr(load_ptr(o, 24 + i * 8))
        i = i + 1
    if length == 1:
        _write_lit(cstr(","), 1)
    _write_lit(cstr(")"), 1)


def _format_repr(o) -> None:
    if ptr_is_null(o) != 0:
        _write_lit(cstr("<null>"), 6)
        return
    if is_tagged_int(o):
        _format_int(o)
        return
    tag: int = load_i32(o, 8)
    if tag == 4:                    # PY_TYPE_STR
        _format_str_repr(o)
        return
    _format(o)


def _format(o) -> None:
    if ptr_is_null(o) != 0:
        _write_lit(cstr("<null>"), 6)
        return
    if is_tagged_int(o):
        _format_int(o)
        return

    tag: int = load_i32(o, 8)
    if tag == 0:                    # PY_TYPE_NONE
        _write_lit(cstr("None"), 4)
    elif tag == 1:                  # PY_TYPE_BOOL
        if ptr_eq(o, global_load_ptr("py_True")) != 0:
            _write_lit(cstr("True"), 4)
        else:
            _write_lit(cstr("False"), 5)
    elif tag == 2:                  # PY_TYPE_INT
        _format_int(o)
    elif tag == 4:                  # PY_TYPE_STR
        _format_str(o)
    elif tag == 5:                  # PY_TYPE_LIST
        _format_list(o)
    elif tag == 7:                  # PY_TYPE_TUPLE
        _format_tuple(o)
    else:
        _write_lit(cstr("<object tag="), 12)
        _write_i64(tag)
        _write_lit(cstr(">"), 1)


@c_abi_export("py_print")
def py_print(o) -> None:
    _flush_stdio()
    _format(o)
    _write_lit(cstr("\n"), 1)


@c_abi_export("py_print_many")
def py_print_many(args_tuple, sep, end) -> None:
    _flush_stdio()
    sep_data = cstr(" ")
    sep_len: int = 1
    end_data = cstr("\n")
    end_len: int = 1
    none = global_load_ptr("py_None")

    if ptr_is_null(sep) == 0:
        if ptr_eq(sep, none) == 0:
            if _type_of(sep) == 4:
                sep_data = ptr_add(sep, 40)
                sep_len = load_i64(sep, 16)
    if ptr_is_null(end) == 0:
        if ptr_eq(end, none) == 0:
            if _type_of(end) == 4:
                end_data = ptr_add(end, 40)
                end_len = load_i64(end, 16)

    if ptr_is_null(args_tuple) != 0:
        write(1, end_data, end_len)
        return

    length: int = load_i64(args_tuple, 16)
    i: int = 0
    while i < length:
        if i > 0:
            write(1, sep_data, sep_len)
        _format(load_ptr(args_tuple, 24 + i * 8))
        i = i + 1
    write(1, end_data, end_len)
