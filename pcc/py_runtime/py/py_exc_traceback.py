"""pcc-Python port of py_exc_traceback.c.

Traceback frame growth and cold unhandled-exception formatting. Output
matches the C runtime's stderr text, but uses pcc.unsafe.write instead
of variadic fprintf.
"""
from pcc.extern import c_abi_export, c_ptr, extern
from pcc.unsafe import (
    cstr,
    free,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_ptr,
    malloc,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    realloc,
    store_i8,
    store_i32,
    store_ptr,
    strlen,
    write,
)

pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)


def _is_exception(obj) -> int:
    if ptr_is_null(obj) != 0:
        return 0
    if is_tagged_int(obj):
        return 0
    if _type_of(obj) != 12:        # PY_TYPE_EXC
        return 0
    return 1


def _write_raw(p) -> None:
    if ptr_is_null(p) != 0:
        return
    n: int = strlen(p)
    if n > 0:
        write(2, p, n)


def _write_raw_or(p, fallback) -> None:
    if ptr_is_null(p) != 0:
        _write_raw(fallback)
        return
    _write_raw(p)


def _write_i64(v: int) -> None:
    if v == 0:
        write(2, cstr("0"), 1)
        return
    if v < 0:
        write(2, cstr("-"), 1)
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
        write(2, ptr_add(buf, i), 1)
        i = i - 1
    free(buf)


def _write_heading(e) -> None:
    cls_name = cstr("Exception")
    cls = pcc_gc_load_ptr(e, ptr_add(e, 16))
    if ptr_is_null(cls) == 0:
        name = load_ptr(cls, 16)
        if ptr_is_null(name) == 0:
            cls_name = name

    msg = pcc_gc_load_ptr(e, ptr_add(e, 24))
    none = global_load_ptr("py_None")
    if ptr_is_null(msg) == 0:
        if ptr_eq(msg, none) == 0:
            if _type_of(msg) == 4:          # PY_TYPE_STR
                _write_raw(cls_name)
                write(2, cstr(": "), 2)
                _write_raw(ptr_add(msg, 40))
                write(2, cstr("\n"), 1)
                return
    _write_raw(cls_name)
    write(2, cstr("\n"), 1)


@c_abi_export("py_exc_append_frame")
def py_exc_append_frame(exc, func_name, filename, line: int) -> None:
    if _is_exception(exc) == 0:
        return

    n_frames: int = load_i32(exc, 56)
    cap_frames: int = load_i32(exc, 60)
    if n_frames == cap_frames:
        new_cap: int = 8
        if cap_frames != 0:
            new_cap = cap_frames * 2
        newbuf = realloc(load_ptr(exc, 48), new_cap * 24)
        if ptr_is_null(newbuf) != 0:
            return
        store_ptr(exc, 48, newbuf)
        store_i32(exc, 60, new_cap)

    traceback = load_ptr(exc, 48)
    fr = ptr_add(traceback, n_frames * 24)
    store_ptr(fr, 0, func_name)
    store_ptr(fr, 8, filename)
    store_i32(fr, 16, line)
    store_i32(fr, 20, 0)
    store_i32(exc, 56, n_frames + 1)


@c_abi_export("py_exc_print_unhandled")
def py_exc_print_unhandled(exc) -> None:
    if _is_exception(exc) == 0:
        write(2, cstr("Unhandled non-exception object"), 30)
        if ptr_is_null(exc) != 0:
            write(2, cstr(" (null)\n"), 8)
            return
        if is_tagged_int(exc):
            write(2, cstr(" (tagged int)\n"), 14)
            return
        tag: int = _type_of(exc)
        write(2, cstr(" (tag="), 6)
        _write_i64(tag)
        write(2, cstr(")"), 1)
        if tag == 4:             # PY_TYPE_STR
            write(2, cstr(": "), 2)
            _write_raw(ptr_add(exc, 40))
        write(2, cstr("\n"), 1)
        return

    cause = pcc_gc_load_ptr(exc, ptr_add(exc, 32))
    context = pcc_gc_load_ptr(exc, ptr_add(exc, 40))
    if _is_exception(cause) != 0:
        py_exc_print_unhandled(cause)
        write(
            2,
            cstr(
                "\nThe above exception was the direct cause of the "
                "following exception:\n\n"
            ),
            71,
        )
    elif _is_exception(context) != 0:
        py_exc_print_unhandled(context)
        write(
            2,
            cstr(
                "\nDuring handling of the above exception, another "
                "exception occurred:\n\n"
            ),
            70,
        )

    write(2, cstr("Traceback (most recent call last):\n"), 35)
    traceback = load_ptr(exc, 48)
    n_frames: int = load_i32(exc, 56)
    i: int = 0
    while i < n_frames:
        fr = ptr_add(traceback, i * 24)
        func_name = load_ptr(fr, 0)
        filename = load_ptr(fr, 8)
        line: int = load_i32(fr, 16)
        write(2, cstr("  File \""), 8)
        _write_raw_or(filename, cstr("<unknown>"))
        write(2, cstr("\", line "), 8)
        _write_i64(line)
        write(2, cstr(", in "), 5)
        _write_raw_or(func_name, cstr("<module>"))
        write(2, cstr("\n"), 1)
        i = i + 1
    _write_heading(exc)
