"""pcc-Python port of py_os_substrate.c.

The stat helpers delegate ABI-sensitive ``struct stat`` reads to
pcc.unsafe intrinsics. The runtime source stays portable and does not encode
Darwin/Linux field offsets directly.
"""

from pcc.extern import (
    extern,
    c_abi_export,
    c_double,
    c_int32,
    c_int64,
    c_ptr,
    c_size_t,
    c_void,
)
from pcc.unsafe import (
    access,
    cstr,
    define_global_ptr_null,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    malloc,
    null,
    ptr_add,
    ptr_is_null,
    stat_kind,
    stat_mtime,
    strlen,
    target_platform_machine,
    target_sys_platform,
)


getcwd = extern("getcwd", (c_ptr, c_size_t), c_ptr)
realpath = extern("realpath", (c_ptr, c_ptr), c_ptr)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_float_from_f64 = extern("py_float_from_f64", (c_double,), c_ptr)
pcc_runtime_monotonic_us = extern("pcc_runtime_monotonic_us", (), c_int64)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
write_sys = extern("write", (c_int32, c_ptr, c_size_t), c_size_t)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2
    return load_i32(obj, 8)


define_global_ptr_null("py_path_getcwd_buf")
define_global_ptr_null("py_path_realpath_buf")


@c_abi_export("py_path_stat_kind")
def py_path_stat_kind(p) -> int:
    return stat_kind(p)


@c_abi_export("py_path_stat_mtime")
def py_path_stat_mtime(p) -> float:
    return stat_mtime(p)


@c_abi_export("py_time_monotonic")
def py_time_monotonic():
    return py_float_from_f64(pcc_runtime_monotonic_us() * 0.000001)


@c_abi_export("py_path_getcwd")
def py_path_getcwd():
    buf = global_load_ptr("py_path_getcwd_buf")
    if ptr_is_null(buf):
        buf = malloc(8192)
        if ptr_is_null(buf):
            return null()
        global_store_ptr("py_path_getcwd_buf", buf)
    result = getcwd(buf, 8192)
    if ptr_is_null(result):
        return null()
    return buf


@c_abi_export("py_path_realpath")
def py_path_realpath(p):
    if ptr_is_null(p):
        return null()
    buf = global_load_ptr("py_path_realpath_buf")
    if ptr_is_null(buf):
        buf = malloc(8192)
        if ptr_is_null(buf):
            return null()
        global_store_ptr("py_path_realpath_buf", buf)
    result = realpath(p, buf)
    if ptr_is_null(result):
        return null()
    return buf


def _str_from_cstr(p):
    if ptr_is_null(p):
        return null()
    return py_str_new(p, strlen(p))


@c_abi_export("py_sys_platform_str")
def py_sys_platform_str():
    return _str_from_cstr(target_sys_platform())


@c_abi_export("py_platform_machine_str")
def py_platform_machine_str():
    return _str_from_cstr(target_platform_machine())


@c_abi_export("py_platform_release_str")
def py_platform_release_str():
    return py_str_new(cstr("0"), 1)


@c_abi_export("py_os_getcwd_str")
def py_os_getcwd_str():
    return _str_from_cstr(py_path_getcwd())


@c_abi_export("py_sys_path_list")
def py_sys_path_list():
    cwd = py_os_getcwd_str()
    if ptr_is_null(cwd):
        cwd = py_str_new(null(), 0)
    lst = py_list_new(0)
    py_list_append(lst, cwd)
    return lst


@c_abi_export("py_os_access")
def py_os_access(path, mode: int) -> int:
    if ptr_is_null(path):
        return 0
    owned = py_obj_str(path)
    if ptr_is_null(owned):
        return 0
    raw = py_str_utf8(owned)
    ok: int = 0
    if not ptr_is_null(raw):
        if access(raw, mode) == 0:
            ok = 1
    py_decref(owned)
    return ok


@c_abi_export("py_os_write")
def py_os_write(fd: int, data) -> int:
    if ptr_is_null(data):
        return -1
    ptr = null()
    length = 0
    curr = data
    while not ptr_is_null(curr):
        tag = _type_of(curr)
        if tag == 4:  # PY_TYPE_STR
            ptr = py_str_utf8(curr)
            length = py_str_byte_len(curr)
            break
        elif tag == 17:  # PY_TYPE_BYTES
            ptr = ptr_add(curr, 24)
            length = load_i64(curr, 16)
            break
        elif tag == 18:  # PY_TYPE_BYTEARRAY
            ptr = ptr_add(curr, 24)
            length = load_i64(curr, 16)
            break
        elif tag == 19:  # PY_TYPE_MEMORYVIEW
            # base pointer is at offset 16
            curr = pcc_gc_load_ptr(curr, ptr_add(curr, 16))
        else:
            return -1

    if ptr_is_null(ptr) or length < 0:
        return -1
    return write_sys(fd, ptr, length)
