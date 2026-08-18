"""pcc-Python port of py_exc_traceback.c.

Traceback frame growth, fail-closed runtime-contract errors, and cold
unhandled-exception formatting. Output matches the C runtime's stderr text,
but uses pcc.unsafe.write instead of variadic fprintf.
"""

__pcc_runtime_port__ = True

from pcc.py_runtime.py.py_abi_constants import (
    PYCLASSOBJECT_NAME_OFFSET,
    PY_TYPE_EXC,
    PY_TYPE_INSTANCE,
    PY_TYPE_INT,
    PY_TYPE_STR,
    PY_TYPE_USER_CLASS_START,
)
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr,
    free,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memcpy,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    realloc,
    store_i8,
    store_i32,
    store_i64,
    store_ptr,
    strlen,
    write,
)

pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
py_isinstance = extern("py_isinstance", (c_ptr, c_ptr), c_int64)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_tls_exc_get = extern("py_tls_exc_get", (), c_ptr)
py_tls_exc_set = extern("py_tls_exc_set", (c_ptr,), c_void)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT       # PY_TYPE_INT
    return load_i32(obj, 8)


def _is_exception(obj) -> int:
    if ptr_is_null(obj) != 0:
        return 0
    if is_tagged_int(obj):
        return 0
    if _type_of(obj) != PY_TYPE_EXC:        # PY_TYPE_EXC
        return 0
    return 1


def _instance_like(obj) -> int:
    if ptr_is_null(obj) != 0:
        return 0
    if is_tagged_int(obj):
        return 0
    tag: int = _type_of(obj)
    if tag == PY_TYPE_INSTANCE:             # PY_TYPE_INSTANCE
        return 1
    if tag >= PY_TYPE_USER_CLASS_START:
        return 1
    return 0


def _is_user_exception(obj) -> int:
    if _instance_like(obj) == 0:
        return 0
    base = py_exc_builtin_class(0)       # PY_EXC_BASE
    if ptr_is_null(base) != 0:
        return 0
    return py_isinstance(obj, base)


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
        name = load_ptr(cls, PYCLASSOBJECT_NAME_OFFSET)
        if ptr_is_null(name) == 0:
            cls_name = name

    msg = pcc_gc_load_ptr(e, ptr_add(e, 24))
    none = global_load_ptr("py_None")
    if ptr_is_null(msg) == 0:
        if ptr_eq(msg, none) == 0:
            if _type_of(msg) == PY_TYPE_STR:          # PY_TYPE_STR
                _write_raw(cls_name)
                write(2, cstr(": "), 2)
                _write_raw(ptr_add(msg, 40))
                write(2, cstr("\n"), 1)
                return
    _write_raw(cls_name)
    write(2, cstr("\n"), 1)


def _write_user_exception_heading(exc) -> None:
    cls_name = cstr("Exception")
    cls = pcc_gc_load_ptr(exc, ptr_add(exc, 16))
    if ptr_is_null(cls) == 0:
        name = load_ptr(cls, PYCLASSOBJECT_NAME_OFFSET)
        if ptr_is_null(name) == 0:
            cls_name = name

    saved_exc = py_current_exception()
    if ptr_is_null(saved_exc) == 0:
        py_incref(saved_exc)
        py_tls_exc_set(null())
    msg = py_obj_str(exc)
    if ptr_is_null(py_tls_exc_get()) == 0:
        py_clear_exception()
    if ptr_is_null(saved_exc) == 0:
        py_tls_exc_set(saved_exc)
        py_decref(saved_exc)
    if ptr_is_null(msg) == 0:
        if _type_of(msg) == PY_TYPE_STR:          # PY_TYPE_STR
            raw = py_str_utf8(msg)
            if ptr_is_null(raw) == 0:
                if strlen(raw) > 0:
                    _write_raw(cls_name)
                    write(2, cstr(": "), 2)
                    _write_raw(raw)
                    write(2, cstr("\n"), 1)
                    py_decref(msg)
                    return
        py_decref(msg)
    _write_raw(cls_name)
    write(2, cstr("\n"), 1)


@c_abi_export("py_exc_append_frame")
def py_exc_append_frame(exc, func_name, filename, line: int) -> None:
    py_exc_append_frame_source(exc, func_name, filename, null(), line)


@c_abi_export("py_exc_append_frame_source")
def py_exc_append_frame_source(
    exc, func_name, filename, source_line, line: int
) -> None:
    if _is_exception(exc) == 0:
        return

    n_frames: int = load_i32(exc, 56)
    cap_frames: int = load_i32(exc, 60)
    if n_frames == cap_frames:
        new_cap: int = 8
        if cap_frames != 0:
            new_cap = cap_frames * 2
        newbuf = realloc(load_ptr(exc, 48), new_cap * 32)
        if ptr_is_null(newbuf) != 0:
            return
        store_ptr(exc, 48, newbuf)
        store_i32(exc, 60, new_cap)

    traceback = load_ptr(exc, 48)
    fr = ptr_add(traceback, n_frames * 32)
    store_ptr(fr, 0, func_name)
    store_ptr(fr, 8, filename)
    store_ptr(fr, 16, source_line)
    store_i32(fr, 24, line)
    store_i32(fr, 28, 0)
    store_i32(exc, 56, n_frames + 1)


@c_abi_export("py_exc_append_frame_indexed")
def py_exc_append_frame_indexed(
    exc, func_name, filename, lines, sources, index: int
) -> None:
    # Mirror of py_exc_append_frame_indexed in py_exc_traceback.c: one shared
    # landing per function/target reads the raise site's (line, source) pair
    # from the module tables by index.
    py_exc_append_frame_source(
        exc,
        func_name,
        filename,
        load_ptr(sources, index * 8),
        load_i32(lines, index * 4),
    )


@c_abi_export("py_runtime_error_if_unset")
def py_runtime_error_if_unset(helper_name: c_ptr, message: c_ptr) -> c_ptr:
    if py_err_occurred() != 0:
        return null()
    if ptr_is_null(helper_name) != 0:
        helper_name = cstr("<pcc runtime>")
    if ptr_is_null(message) != 0:
        message = cstr(
            "runtime helper returned NULL without setting an exception"
        )
    exc = py_exc_new(7, message)  # PY_EXC_RUNTIMEERROR
    if ptr_is_null(exc) == 0:
        py_exc_append_frame_source(
            exc,
            helper_name,
            cstr("<pcc runtime>"),
            cstr("runtime contract: NULL result without an exception"),
            0,
        )
        py_raise_owned(exc)
    return null()


@c_abi_export("py_exc_print_unhandled")
def py_exc_print_unhandled(exc) -> None:
    if _is_exception(exc) == 0:
        if _is_user_exception(exc) != 0:
            _write_user_exception_heading(exc)
            return
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
        if tag == PY_TYPE_STR:             # PY_TYPE_STR
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
    i: int = n_frames - 1
    while i >= 0:
        fr = ptr_add(traceback, i * 32)
        func_name = load_ptr(fr, 0)
        filename = load_ptr(fr, 8)
        source_line = load_ptr(fr, 16)
        line: int = load_i32(fr, 24)
        write(2, cstr("  File \""), 8)
        _write_raw_or(filename, cstr("<unknown>"))
        write(2, cstr("\", line "), 8)
        _write_i64(line)
        write(2, cstr(", in "), 5)
        _write_raw_or(func_name, cstr("<module>"))
        write(2, cstr("\n"), 1)
        if ptr_is_null(source_line) == 0:
            if strlen(source_line) > 0:
                write(2, cstr("    "), 4)
                _write_raw(source_line)
                write(2, cstr("\n"), 1)
        i = i - 1
    _write_heading(exc)


# ---- traceback.format_exc() / traceback.print_exc() -----------------
#
# Mirrors the PccTbBuf helpers in py_exc_traceback.c: a growable heap
# buffer {buf ptr @0, len i64 @8, cap i64 @16} that collects the
# CPython-style traceback text. Frames are emitted in reverse trail
# order (pcc appends the raise site first; CPython prints the outermost
# frame first under "most recent call last").


def _tb_buf_new():
    b = malloc(24)
    if ptr_is_null(b) != 0:
        return null()
    store_ptr(b, 0, null())
    store_i64(b, 8, 0)
    store_i64(b, 16, 0)
    return b


def _tb_reserve(b, extra: int) -> int:
    length: int = load_i64(b, 8)
    cap: int = load_i64(b, 16)
    if length + extra + 1 <= cap:
        return 1
    new_cap: int = cap
    if new_cap == 0:
        new_cap = 256
    while new_cap < length + extra + 1:
        new_cap = new_cap * 2
    nb = realloc(load_ptr(b, 0), new_cap)
    if ptr_is_null(nb) != 0:
        return 0
    store_ptr(b, 0, nb)
    store_i64(b, 16, new_cap)
    return 1


def _tb_append_n(b, s, n: int) -> None:
    if ptr_is_null(s) != 0:
        return
    if n <= 0:
        return
    if _tb_reserve(b, n) == 0:
        return
    buf = load_ptr(b, 0)
    length: int = load_i64(b, 8)
    memcpy(ptr_add(buf, length), s, n)
    store_i64(b, 8, length + n)
    store_i8(buf, length + n, 0)


def _tb_append(b, s) -> None:
    if ptr_is_null(s) != 0:
        return
    _tb_append_n(b, s, strlen(s))


def _tb_append_i64(b, v: int) -> None:
    if v == 0:
        _tb_append_n(b, cstr("0"), 1)
        return
    if v < 0:
        _tb_append_n(b, cstr("-"), 1)
        v = 0 - v
    tmp = malloc(32)
    if ptr_is_null(tmp) != 0:
        return
    n: int = 0
    while v > 0:
        digit: int = v % 10
        store_i8(tmp, n, 48 + digit)
        n = n + 1
        v = v // 10
    i: int = n - 1
    while i >= 0:
        _tb_append_n(b, ptr_add(tmp, i), 1)
        i = i - 1
    free(tmp)


def _tb_append_exc_heading(b, e) -> None:
    cls_name = cstr("Exception")
    cls = pcc_gc_load_ptr(e, ptr_add(e, 16))
    if ptr_is_null(cls) == 0:
        name = load_ptr(cls, PYCLASSOBJECT_NAME_OFFSET)
        if ptr_is_null(name) == 0:
            cls_name = name

    msg = pcc_gc_load_ptr(e, ptr_add(e, 24))
    none = global_load_ptr("py_None")
    if ptr_is_null(msg) == 0:
        if ptr_eq(msg, none) == 0:
            if _type_of(msg) == PY_TYPE_STR:          # PY_TYPE_STR
                _tb_append(b, cls_name)
                _tb_append_n(b, cstr(": "), 2)
                _tb_append(b, ptr_add(msg, 40))
                _tb_append_n(b, cstr("\n"), 1)
                return
    _tb_append(b, cls_name)
    _tb_append_n(b, cstr("\n"), 1)


def _tb_append_user_exc_heading(b, exc) -> None:
    cls_name = cstr("Exception")
    cls = pcc_gc_load_ptr(exc, ptr_add(exc, 16))
    if ptr_is_null(cls) == 0:
        name = load_ptr(cls, PYCLASSOBJECT_NAME_OFFSET)
        if ptr_is_null(name) == 0:
            cls_name = name

    saved_exc = py_current_exception()
    if ptr_is_null(saved_exc) == 0:
        py_incref(saved_exc)
        py_tls_exc_set(null())
    msg = py_obj_str(exc)
    if ptr_is_null(py_tls_exc_get()) == 0:
        py_clear_exception()
    if ptr_is_null(saved_exc) == 0:
        py_tls_exc_set(saved_exc)
        py_decref(saved_exc)
    if ptr_is_null(msg) == 0:
        if _type_of(msg) == PY_TYPE_STR:          # PY_TYPE_STR
            raw = py_str_utf8(msg)
            if ptr_is_null(raw) == 0:
                if strlen(raw) > 0:
                    _tb_append(b, cls_name)
                    _tb_append_n(b, cstr(": "), 2)
                    _tb_append(b, raw)
                    _tb_append_n(b, cstr("\n"), 1)
                    py_decref(msg)
                    return
        py_decref(msg)
    _tb_append(b, cls_name)
    _tb_append_n(b, cstr("\n"), 1)


def _tb_format_into(b, exc, depth: int) -> None:
    if ptr_is_null(exc) != 0:
        _tb_append(b, cstr("NoneType: None\n"))
        return
    if is_tagged_int(exc):
        _tb_append(b, cstr("NoneType: None\n"))
        return
    if _is_exception(exc) == 0:
        if _is_user_exception(exc) != 0:
            # User exception subclass instances raised as-is carry no
            # PyFrameRecord trail; emit the heading under the CPython
            # banner so callers still see the exception identity.
            _tb_append(b, cstr("Traceback (most recent call last):\n"))
            _tb_append_user_exc_heading(b, exc)
            return
        _tb_append(b, cstr("NoneType: None\n"))
        return

    # Chained causes oldest-first, CPython-style. Depth-capped so a
    # pathological __context__ cycle cannot recurse forever.
    if depth < 8:
        cause = pcc_gc_load_ptr(exc, ptr_add(exc, 32))
        context = pcc_gc_load_ptr(exc, ptr_add(exc, 40))
        if _is_exception(cause) != 0:
            _tb_format_into(b, cause, depth + 1)
            _tb_append(
                b,
                cstr(
                    "\nThe above exception was the direct cause of the "
                    "following exception:\n\n"
                ),
            )
        elif _is_exception(context) != 0:
            _tb_format_into(b, context, depth + 1)
            _tb_append(
                b,
                cstr(
                    "\nDuring handling of the above exception, another "
                    "exception occurred:\n\n"
                ),
            )

    _tb_append(b, cstr("Traceback (most recent call last):\n"))
    traceback = load_ptr(exc, 48)
    n_frames: int = load_i32(exc, 56)
    i: int = n_frames - 1
    while i >= 0:
        fr = ptr_add(traceback, i * 32)
        func_name = load_ptr(fr, 0)
        filename = load_ptr(fr, 8)
        source_line = load_ptr(fr, 16)
        line: int = load_i32(fr, 24)
        _tb_append_n(b, cstr("  File \""), 8)
        if ptr_is_null(filename) != 0:
            _tb_append_n(b, cstr("<unknown>"), 9)
        else:
            _tb_append(b, filename)
        _tb_append_n(b, cstr("\", line "), 8)
        _tb_append_i64(b, line)
        _tb_append_n(b, cstr(", in "), 5)
        if ptr_is_null(func_name) != 0:
            _tb_append_n(b, cstr("<module>"), 8)
        else:
            _tb_append(b, func_name)
        _tb_append_n(b, cstr("\n"), 1)
        if ptr_is_null(source_line) == 0:
            if strlen(source_line) > 0:
                _tb_append_n(b, cstr("    "), 4)
                _tb_append(b, source_line)
                _tb_append_n(b, cstr("\n"), 1)
        i = i - 1
    _tb_append_exc_heading(b, exc)


@c_abi_export("py_exc_traceback_format_exc")
def py_exc_traceback_format_exc(exc):
    b = _tb_buf_new()
    if ptr_is_null(b) != 0:
        return py_str_new(cstr(""), 0)
    _tb_format_into(b, exc, 0)
    buf = load_ptr(b, 0)
    length: int = load_i64(b, 8)
    if ptr_is_null(buf) != 0:
        free(b)
        return py_str_new(cstr(""), 0)
    result = py_str_new(buf, length)
    free(buf)
    free(b)
    return result


@c_abi_export("py_exc_traceback_print_exc")
def py_exc_traceback_print_exc(exc) -> None:
    b = _tb_buf_new()
    if ptr_is_null(b) != 0:
        return
    _tb_format_into(b, exc, 0)
    buf = load_ptr(b, 0)
    length: int = load_i64(b, 8)
    if ptr_is_null(buf) == 0:
        if length > 0:
            write(2, buf, length)
        free(buf)
    free(b)
