"""Phase 4c.10: pcc-Python port of py_print_sys.c.

sys.stdout.write / sys.stderr.write helpers. Non-str arguments are
coerced through py_obj_str (Python string conversion), the UTF-8
bytes are written directly via the platform write() intrinsic, and the
returned value is a py-int with the byte count.

PyStrObject layout (from py_internal.h):
    offset  0   PyObjectHeader   (16 bytes)
    offset 16   byte_len         (i64)
    data lives inline at offset 40 — but we use py_str_utf8 to get
    the pointer rather than load_ptr + add, since the whole point
    of the runtime ABI is that py_str_utf8 is stable.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.unsafe import (
    global_load_ptr,
    is_tagged_int,
    load_i32,
    null,
    ptr_is_null,
    write,
)

py_decref            = extern("py_decref",            (c_ptr,),                   c_void)
py_obj_str           = extern("py_obj_str",           (c_ptr,),                   c_ptr)
py_str_utf8          = extern("py_str_utf8",          (c_ptr,),                   c_ptr)
py_str_byte_len      = extern("py_str_byte_len",      (c_ptr,),                   c_int64)
py_int_from_i64      = extern("py_int_from_i64",      (c_int64,),                 c_ptr)


# PY_TYPE_STR (=4) is inlined at every use site because pcc-Python
# initializes module-level integers in the auto-generated main(),
# which the Makefile strips for library .o builds.


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return 2       # PY_TYPE_INT
    return load_i32(obj, 8)


def _write_fd(fd: int, text):
    owned = null()
    item = text
    if ptr_is_null(item):
        item = global_load_ptr("py_None")
    if _type_of(item) != 4:        # PY_TYPE_STR
        owned = py_obj_str(item)
        item = owned
    if ptr_is_null(item):
        if not ptr_is_null(owned):
            py_decref(owned)
        return py_int_from_i64(0)
    raw = py_str_utf8(item)
    n: int = py_str_byte_len(item)
    wrote: int = 0
    if not ptr_is_null(raw):
        if n > 0:
            wrote = write(fd, raw, n)
    if not ptr_is_null(owned):
        py_decref(owned)
    return py_int_from_i64(wrote)


@c_abi_export("py_sys_stdout_write")
def py_sys_stdout_write(text):
    return _write_fd(1, text)


@c_abi_export("py_sys_stderr_write")
def py_sys_stderr_write(text):
    return _write_fd(2, text)
