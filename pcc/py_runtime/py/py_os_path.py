"""pcc-Python port of py_os_path.c.

Narrow os.path runtime helpers: join, basename, and exists. Non-string
path objects are coerced through py_obj_str before reading UTF-8 bytes.
"""
from pcc.extern import (
    extern, c_abi_export, c_double, c_ptr, c_int32, c_int64, c_void,
)
from pcc.unsafe import (
    access,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    memmove,
    null,
    ptr_add,
    ptr_is_null,
    store_i8,
)


py_decref         = extern("py_decref",         (c_ptr,),         c_void)
py_obj_str        = extern("py_obj_str",        (c_ptr,),         c_ptr)
py_str_new        = extern("py_str_new",        (c_ptr, c_int64), c_ptr)
py_float_from_f64 = extern("py_float_from_f64", (c_double,),      c_ptr)
# Low-level platform-portable stat helpers (defined in C; see
# src/py_os_substrate.c). Avoids encoding struct stat layout — which
# differs between Linux and macOS — in pcc-Python.
py_path_stat_kind  = extern("py_path_stat_kind",  (c_ptr,),         c_int32)
py_path_stat_mtime = extern("py_path_stat_mtime", (c_ptr,),         c_double)
py_path_getcwd     = extern("py_path_getcwd",     (),               c_ptr)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)


def _coerce_path_str(o):
    if ptr_is_null(o) != 0:
        return null(), null()
    if _type_of(o) == 4:           # PY_TYPE_STR
        return o, null()
    owned = py_obj_str(o)
    return owned, owned


def _path_seq_len(parts) -> int:
    if ptr_is_null(parts) != 0:
        return -1
    if is_tagged_int(parts):
        return -1
    tag: int = _type_of(parts)
    if tag == 5:                  # PY_TYPE_LIST
        return load_i64(parts, 16)
    if tag == 7:                  # PY_TYPE_TUPLE
        return load_i64(parts, 16)
    return -1


def _path_seq_borrow(parts, i: int):
    if ptr_is_null(parts) != 0:
        return null()
    if is_tagged_int(parts):
        return null()
    tag: int = _type_of(parts)
    if tag == 5:                  # PY_TYPE_LIST
        items = load_ptr(parts, 32)
        return load_ptr(items, i * 8)
    if tag == 7:                  # PY_TYPE_TUPLE
        return load_ptr(parts, 24 + i * 8)
    return null()


@c_abi_export("py_os_path_join")
def py_os_path_join(parts):
    n: int = _path_seq_len(parts)
    if n < 0:
        return null()
    if n == 0:
        return py_str_new(null(), 0)

    # First pass: validate/coerce path parts, find the last absolute
    # component, and compute final byte length. Do not shuttle raw
    # char* buffers through Python tuples; tuple assignment would treat
    # them as PyObject* and corrupt memory through refcounting.
    start_idx: int = 0
    total: int = 0
    last_char: int = 0
    i: int = 0
    while i < n:
        item, owned = _coerce_path_str(_path_seq_borrow(parts, i))
        if ptr_is_null(item) != 0:
            if ptr_is_null(owned) == 0:
                py_decref(owned)
            return null()

        part = ptr_add(item, 40)
        part_len: int = load_i64(item, 16)

        if part_len > 0 and load_i8(part, 0) == 47:     # '/'
            if i > 0:
                start_idx = i
                total = 0
        if i > start_idx:
            if total > 0 and last_char != 47:
                total = total + 1
                last_char = 47
        total = total + part_len
        if part_len > 0:
            last_char = load_i8(part, part_len - 1)
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        i = i + 1

    out = py_str_new(null(), total)
    if ptr_is_null(out) != 0:
        return null()
    dst = ptr_add(out, 40)

    # Second pass: copy bytes into the final PyStrObject.
    off: int = 0
    j: int = start_idx
    while j < n:
        item2, owned2 = _coerce_path_str(_path_seq_borrow(parts, j))
        if ptr_is_null(item2) != 0:
            if ptr_is_null(owned2) == 0:
                py_decref(owned2)
            return null()
        part2 = ptr_add(item2, 40)
        part2_len: int = load_i64(item2, 16)
        if j > start_idx:
            if off > 0 and load_i8(dst, off - 1) != 47:
                store_i8(dst, off, 47)
                off = off + 1
        if part2_len > 0:
            memmove(ptr_add(dst, off), part2, part2_len)
            off = off + part2_len
        if ptr_is_null(owned2) == 0:
            py_decref(owned2)
        j = j + 1
    return out


@c_abi_export("py_os_path_dirname")
def py_os_path_dirname(path):
    item, owned = _coerce_path_str(path)
    if ptr_is_null(item) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return null()

    data = ptr_add(item, 40)
    n: int = load_i64(item, 16)

    last: int = -1
    i: int = 0
    while i < n:
        if load_i8(data, i) == 47:
            last = i
        i = i + 1

    head_len: int = last + 1
    if head_len == 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return py_str_new(null(), 0)

    all_slash: int = 1
    j: int = 0
    while j < head_len:
        if load_i8(data, j) != 47:
            all_slash = 0
        j = j + 1

    out_len: int = head_len
    if all_slash == 0:
        while out_len > 0 and load_i8(data, out_len - 1) == 47:
            out_len = out_len - 1

    out = py_str_new(data, out_len)
    if ptr_is_null(owned) == 0:
        py_decref(owned)
    return out


@c_abi_export("py_os_path_basename")
def py_os_path_basename(path):
    item, owned = _coerce_path_str(path)
    if ptr_is_null(item) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return null()

    data = ptr_add(item, 40)
    end: int = load_i64(item, 16)
    while end > 0 and load_i8(data, end - 1) == 47:
        end = end - 1
    if end == 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return py_str_new(null(), 0)

    start: int = end
    while start > 0 and load_i8(data, start - 1) != 47:
        start = start - 1
    out = py_str_new(ptr_add(data, start), end - start)
    if ptr_is_null(owned) == 0:
        py_decref(owned)
    return out


@c_abi_export("py_os_path_isfile")
def py_os_path_isfile(path) -> int:
    item, owned = _coerce_path_str(path)
    if ptr_is_null(item) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return 0
    raw = ptr_add(item, 40)
    kind: int = py_path_stat_kind(raw)
    if ptr_is_null(owned) == 0:
        py_decref(owned)
    if kind == 1:
        return 1
    return 0


@c_abi_export("py_os_path_isdir")
def py_os_path_isdir(path) -> int:
    item, owned = _coerce_path_str(path)
    if ptr_is_null(item) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return 0
    raw = ptr_add(item, 40)
    kind: int = py_path_stat_kind(raw)
    if ptr_is_null(owned) == 0:
        py_decref(owned)
    if kind == 2:
        return 1
    return 0


@c_abi_export("py_os_path_abspath")
def py_os_path_abspath(path):
    item, owned = _coerce_path_str(path)
    if ptr_is_null(item) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return null()
    data = ptr_add(item, 40)
    in_len: int = load_i64(item, 16)

    # Already absolute → copy unchanged.
    if in_len > 0:
        if load_i8(data, 0) == 47:
            out = py_str_new(data, in_len)
            if ptr_is_null(owned) == 0:
                py_decref(owned)
            return out

    cwd_ptr = py_path_getcwd()
    if ptr_is_null(cwd_ptr) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return null()
    cwd_len: int = 0
    while load_i8(cwd_ptr, cwd_len) != 0:
        cwd_len = cwd_len + 1

    if in_len == 0:
        out = py_str_new(cwd_ptr, cwd_len)
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return out

    total: int = cwd_len + 1 + in_len
    out = py_str_new(null(), total)
    if ptr_is_null(out) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return null()
    dst = ptr_add(out, 40)
    memmove(dst, cwd_ptr, cwd_len)
    store_i8(dst, cwd_len, 47)
    memmove(ptr_add(dst, cwd_len + 1), data, in_len)
    if ptr_is_null(owned) == 0:
        py_decref(owned)
    return out


@c_abi_export("py_os_path_getmtime")
def py_os_path_getmtime(path):
    item, owned = _coerce_path_str(path)
    if ptr_is_null(item) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return null()
    raw = ptr_add(item, 40)
    t: float = py_path_stat_mtime(raw)
    if ptr_is_null(owned) == 0:
        py_decref(owned)
    return py_float_from_f64(t)


@c_abi_export("py_os_path_exists")
def py_os_path_exists(path) -> int:
    item, owned = _coerce_path_str(path)
    if ptr_is_null(item) != 0:
        if ptr_is_null(owned) == 0:
            py_decref(owned)
        return 0

    raw = ptr_add(item, 40)
    ok: int = 0
    if access(raw, 0) == 0:        # F_OK
        ok = 1
    if ptr_is_null(owned) == 0:
        py_decref(owned)
    return ok
