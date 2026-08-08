"""pcc-Python port of py_file.c."""

from pcc.extern import (
    extern,
    c_abi_export,
    c_int32,
    c_int64,
    c_ptr,
    c_size_t,
    c_void,
)
from pcc.py_runtime.py.py_abi_constants import PY_TYPE_FILE, PY_TYPE_INT
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_LIST,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
)
from pcc.unsafe import (
    cstr,
    free,
    global_load_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
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
)

fclose = extern("fclose", (c_ptr,), c_int32)
ferror = extern("ferror", (c_ptr,), c_int32)
fflush = extern("fflush", (c_ptr,), c_int32)
fgetc = extern("fgetc", (c_ptr,), c_int32)
fopen = extern("fopen", (c_ptr, c_ptr), c_ptr)
fread = extern("fread", (c_ptr, c_size_t, c_size_t, c_ptr), c_size_t)
# LP64 targets (aarch64-darwin / x86_64-linux): C ``long`` is 64-bit, so
# fseek/ftell take/return c_int64 here.
fseek = extern("fseek", (c_ptr, c_int64, c_int32), c_int32)
ftell = extern("ftell", (c_ptr,), c_int64)
fwrite = extern("fwrite", (c_ptr, c_size_t, c_size_t, c_ptr), c_size_t)
fileno = extern("fileno", (c_ptr,), c_int32)

py_decref = extern("py_decref", (c_ptr,), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_bool_from_bit = extern("py_bool_from_bit", (c_int32,), c_ptr)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_set = extern("py_list_set", (c_ptr, c_int64, c_ptr), c_void)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_str_splitlines_keepends = extern(
    "py_str_splitlines_keepends", (c_ptr, c_int32), c_ptr
)
py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT
    return load_i32(obj, 8)


def _coerce_str(o):
    if ptr_is_null(o):
        return null()
    if _type_of(o) == PY_TYPE_STR:
        return o
    return py_obj_str(o)


def _checked_file(file):
    if ptr_is_null(file):
        return null()
    if _type_of(file) != PY_TYPE_FILE:
        return null()
    if load_i32(file, 24) != 0:
        return null()
    if ptr_is_null(load_ptr(file, 16)):
        return null()
    return file


def _mode_is_binary(mode_s) -> int:
    if ptr_is_null(mode_s):
        return 0
    data = py_str_utf8(mode_s)
    n: int = py_str_byte_len(mode_s)
    i: int = 0
    while i < n:
        if (load_i8(data, i) & 255) == 98:
            return 1
        i = i + 1
    return 0


def _file_binary(file) -> int:
    return load_i32(file, 28)


def _file_bytes_or_str(file, data, n: int):
    if _file_binary(file) != 0:
        return py_bytes_new(data, n)
    return py_str_new(data, n)


@c_abi_export("py_file_open")
def py_file_open(path, mode):
    path_s = _coerce_str(path)
    path_owned = null()
    if not ptr_is_null(path_s) and not ptr_eq(path_s, path):
        path_owned = path_s

    mode_s = null()
    mode_owned = null()
    none = global_load_ptr("py_None")
    if ptr_is_null(mode) or ptr_eq(mode, none) != 0:
        mode_s = py_str_new(cstr("r"), 1)
        mode_owned = mode_s
    else:
        mode_s = _coerce_str(mode)
        if not ptr_is_null(mode_s) and not ptr_eq(mode_s, mode):
            mode_owned = mode_s

    if ptr_is_null(path_s) or ptr_is_null(mode_s):
        py_decref(path_owned)
        py_decref(mode_owned)
        return null()

    binary: int = _mode_is_binary(mode_s)
    fp = fopen(py_str_utf8(path_s), py_str_utf8(mode_s))
    py_decref(path_owned)
    py_decref(mode_owned)
    if ptr_is_null(fp):
        # 14 == PY_EXC_OSERROR. Keep the C and pcc-Python runtime mirrors on
        # the same NULL-plus-exception failure contract.
        py_raise_owned(py_exc_new(14, cstr("could not open file")))
        return null()

    out = pcc_gc_alloc(40, PY_TYPE_FILE, 0)
    if ptr_is_null(out):
        fclose(fp)
        return null()
    store_ptr(out, 16, fp)
    store_i32(out, 24, 0)
    store_i32(out, 28, binary)
    return out


@c_abi_export("py_file_read_all")
def py_file_read_all(file):
    f = _checked_file(file)
    if ptr_is_null(f):
        return null()
    fp = load_ptr(f, 16)

    buf = null()
    length: int = 0
    cap: int = 0
    tmp = malloc(4096)
    if ptr_is_null(tmp):
        return null()
    while True:
        n: int = fread(tmp, 1, 4096, fp)
        if n > 0:
            if length + n + 1 > cap:
                new_cap: int = cap
                if new_cap == 0:
                    new_cap = 4096
                while new_cap < length + n + 1:
                    new_cap = new_cap * 2
                grown = realloc(buf, new_cap)
                if ptr_is_null(grown):
                    free(tmp)
                    free(buf)
                    return null()
                buf = grown
                cap = new_cap
            memcpy(ptr_add(buf, length), tmp, n)
            length = length + n
        if n < 4096:
            if ferror(fp) != 0:
                free(tmp)
                free(buf)
                return null()
            break
    data = buf
    if ptr_is_null(data):
        data = cstr("")
    out = _file_bytes_or_str(f, data, length)
    free(tmp)
    free(buf)
    return out


@c_abi_export("py_file_read")
def py_file_read(file, limit: int):
    if limit < 0:
        return py_file_read_all(file)
    f = _checked_file(file)
    if ptr_is_null(f):
        return null()
    fp = load_ptr(f, 16)
    buf = null()
    n: int = 0
    if limit > 0:
        buf = malloc(limit)
        if ptr_is_null(buf):
            return null()
        n = fread(buf, 1, limit, fp)
        if n < limit and ferror(fp) != 0:
            free(buf)
            return null()
    data = buf
    if ptr_is_null(data):
        data = cstr("")
    out = _file_bytes_or_str(f, data, n)
    free(buf)
    return out


@c_abi_export("py_file_write")
def py_file_write(file, text):
    f = _checked_file(file)
    if ptr_is_null(f):
        return null()
    s = _coerce_str(text)
    owned = null()
    if not ptr_is_null(s) and not ptr_eq(s, text):
        owned = s
    if ptr_is_null(s):
        py_decref(owned)
        return null()
    n: int = py_str_byte_len(s)
    wrote: int = 0
    if n > 0:
        wrote = fwrite(py_str_utf8(s), 1, n, load_ptr(f, 16))
    py_decref(owned)
    return py_int_from_i64(wrote)


def _checked_open_file(file):
    """Shared open-file precondition for readline/seek/tell/flush.

    NULL / non-file receivers return null silently (matching the older
    read/write helpers); a closed file raises ValueError exactly like
    CPython ("I/O operation on closed file.").
    """
    if ptr_is_null(file):
        return null()
    if _type_of(file) != PY_TYPE_FILE:
        return null()
    if load_i32(file, 24) != 0 or ptr_is_null(load_ptr(file, 16)):
        # 2 == PY_EXC_VALUEERROR
        py_raise_owned(py_exc_new(2, cstr("I/O operation on closed file.")))
        return null()
    return file


@c_abi_export("py_file_readline")
def py_file_readline(file, limit: int):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fp = load_ptr(f, 16)

    buf = null()
    length: int = 0
    cap: int = 0
    while True:
        if limit >= 0 and length >= limit:
            break
        ch: int = fgetc(fp)
        if ch < 0:
            if ferror(fp) != 0:
                free(buf)
                return null()
            break
        if length + 2 > cap:
            new_cap: int = 128
            if cap != 0:
                new_cap = cap * 2
            grown = realloc(buf, new_cap)
            if ptr_is_null(grown):
                free(buf)
                return null()
            buf = grown
            cap = new_cap
        store_i8(buf, length, ch)
        length = length + 1
        if ch == 10:
            break
    data = buf
    if ptr_is_null(data):
        data = cstr("")
    out = _file_bytes_or_str(f, data, length)
    free(buf)
    return out


@c_abi_export("py_file_seek")
def py_file_seek(file, offset: int, whence: int):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fp = load_ptr(f, 16)
    # SEEK_SET/SEEK_CUR/SEEK_END are 0/1/2 on both LP64 targets; unknown
    # whence values fall back to SEEK_SET like the C mirror.
    w: int = 0
    if whence == 1:
        w = 1
    if whence == 2:
        w = 2
    rc: int = fseek(fp, offset, w)
    pos: int = -1
    if rc == 0:
        pos = ftell(fp)
    if rc != 0 or pos < 0:
        # 14 == PY_EXC_OSERROR
        py_raise_owned(py_exc_new(14, cstr("Invalid argument")))
        return null()
    return py_int_from_i64(pos)


@c_abi_export("py_file_tell")
def py_file_tell(file):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    pos: int = ftell(load_ptr(f, 16))
    if pos < 0:
        # 14 == PY_EXC_OSERROR
        py_raise_owned(py_exc_new(14, cstr("Invalid argument")))
        return null()
    return py_int_from_i64(pos)


@c_abi_export("py_file_flush")
def py_file_flush(file):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fflush(load_ptr(f, 16))
    none = global_load_ptr("py_None")
    py_incref(none)
    return none


@c_abi_export("py_file_fileno")
def py_file_fileno(file):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fd: int = fileno(load_ptr(f, 16))
    if fd < 0:
        py_raise_owned(py_exc_new(14, cstr("could not get file descriptor")))
        return null()
    return py_int_from_i64(fd)


@c_abi_export("py_file_close")
def py_file_close(file) -> None:
    if ptr_is_null(file):
        return
    if _type_of(file) != PY_TYPE_FILE:
        return
    if load_i32(file, 24) == 0:
        fp = load_ptr(file, 16)
        if not ptr_is_null(fp):
            fclose(fp)
            store_ptr(file, 16, null())
            store_i32(file, 24, 1)


# Keep fileinput state indexes as integer literals at use sites below. The
# pcc-Python runtime path cannot safely rely on module-level integer constants
# during early bootstrap module initialization.


def _state_get(state, index: int):
    return py_list_get(state, index)


def _state_get_i64(state, index: int) -> int:
    item = py_list_get(state, index)
    out: int = 0
    none = global_load_ptr("py_None")
    if not ptr_is_null(item) and ptr_eq(item, none) == 0:
        out = py_int_value_i64(item)
    py_decref(item)
    return out


def _state_set_i64(state, index: int, value: int) -> None:
    obj = py_int_from_i64(value)
    py_list_set(state, index, obj)


def _files_len(files) -> int:
    if ptr_is_null(files):
        return 0
    tag: int = _type_of(files)
    if tag == PY_TYPE_STR:
        return 1
    if tag == PY_TYPE_LIST:
        return py_list_len(files)
    if tag == PY_TYPE_TUPLE:
        return py_tuple_len(files)
    return 0


def _files_get(files, index: int):
    if ptr_is_null(files):
        return null()
    tag: int = _type_of(files)
    if tag == PY_TYPE_STR:
        if index != 0:
            return null()
        py_incref(files)
        return files
    if tag == PY_TYPE_LIST:
        return py_list_get(files, index)
    if tag == PY_TYPE_TUPLE:
        return py_tuple_get(files, index)
    return null()


def _fileinput_open_text(filename):
    mode = py_str_new(cstr("r"), 1)
    file = py_file_open(filename, mode)
    py_decref(mode)
    return file


def _open_next(state) -> int:
    files = _state_get(state, 0)
    idx: int = _state_get_i64(state, 2)
    nfiles: int = _files_len(files)
    while idx < nfiles:
        filename = _files_get(files, idx)
        _state_set_i64(state, 2, idx + 1)
        idx = idx + 1
        if ptr_is_null(filename):
            continue
        py_list_set(state, 6, filename)
        file = _fileinput_open_text(filename)
        if ptr_is_null(file):
            py_decref(files)
            return 0
        text = py_file_read_all(file)
        py_file_close(file)
        py_decref(file)
        if ptr_is_null(text):
            py_decref(files)
            return 0
        lines = py_str_splitlines_keepends(text, 1)
        py_decref(text)
        if ptr_is_null(lines):
            py_decref(files)
            return 0
        py_list_set(state, 3, lines)
        _state_set_i64(state, 4, 0)
        if py_list_len(lines) > 0:
            py_decref(files)
            return 1
    py_decref(files)
    return 0


@c_abi_export("py_fileinput_new")
def py_fileinput_new(files, openhook):
    state = py_list_new(7)
    zero = py_int_from_i64(0)
    empty = py_list_new(0)
    none = global_load_ptr("py_None")
    if ptr_is_null(files):
        py_list_append(state, none)
    else:
        py_list_append(state, files)
    if ptr_is_null(openhook):
        py_list_append(state, none)
    else:
        py_list_append(state, openhook)
    py_list_append(state, zero)
    py_list_append(state, empty)
    py_list_append(state, zero)
    py_list_append(state, zero)
    py_list_append(state, none)
    return state


@c_abi_export("py_fileinput_readline")
def py_fileinput_readline(state):
    if ptr_is_null(state):
        return py_str_new(cstr(""), 0)
    while True:
        lines = _state_get(state, 3)
        line_idx: int = _state_get_i64(state, 4)
        nlines: int = py_list_len(lines)
        if line_idx < nlines:
            line = py_list_get(lines, line_idx)
            py_decref(lines)
            _state_set_i64(state, 4, line_idx + 1)
            total: int = _state_get_i64(state, 5)
            _state_set_i64(state, 5, total + 1)
            return line
        py_decref(lines)
        if _open_next(state) == 0:
            return py_str_new(cstr(""), 0)


@c_abi_export("py_fileinput_filename")
def py_fileinput_filename(state):
    filename = _state_get(state, 6)
    if ptr_is_null(filename):
        none = global_load_ptr("py_None")
        py_incref(none)
        return none
    return filename


@c_abi_export("py_fileinput_lineno")
def py_fileinput_lineno(state):
    return py_int_from_i64(_state_get_i64(state, 5))


@c_abi_export("py_fileinput_filelineno")
def py_fileinput_filelineno(state):
    return py_int_from_i64(_state_get_i64(state, 4))


@c_abi_export("py_fileinput_isfirstline")
def py_fileinput_isfirstline(state):
    first: int = 0
    if _state_get_i64(state, 4) == 1:
        first = 1
    return py_bool_from_bit(first)


@c_abi_export("py_fileinput_close")
def py_fileinput_close(state):
    none = global_load_ptr("py_None")
    py_incref(none)
    return none
