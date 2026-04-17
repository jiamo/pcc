"""Phase 4c.11: pcc-Python port of py_obj.c.

py_bool_from_bit + py_incref + py_decref dispatch. The dealloc
implementations are separate ABI symbols, provided by py_obj_dealloc.py
in the pcc-Python runtime archive and by py_obj_dealloc.c in the C
runtime archives. The immortal singletons live in py_substrate.py for
the pcc-Python archive and in py_substrate.c for the C runtime archives.
This port only owns the "dispatch layer".

PyObjectHeader layout:
    offset  0   refcount     (i64)
    offset  8   type_tag     (i32)
    offset 12   flags        (i32)

PY_FLAG_IMMORTAL = 0x1. Tagged ints are low-bit=1 pointers.

Type tags (from py_runtime.h):
    PY_TYPE_NONE     = 0
    PY_TYPE_BOOL     = 1
    PY_TYPE_INT      = 2
    PY_TYPE_FLOAT    = 3
    PY_TYPE_STR      = 4
    PY_TYPE_LIST     = 5
    PY_TYPE_DICT     = 6
    PY_TYPE_TUPLE    = 7
    PY_TYPE_SET      = 8
    PY_TYPE_FUNC     = 9
    PY_TYPE_CLASS    = 10
    PY_TYPE_INSTANCE = 11
    PY_TYPE_EXC      = 12
    PY_TYPE_USER     = 100
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_void
from pcc.unsafe import (
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    ptr_is_null,
    store_i64,
)

py_dealloc_int        = extern("py_dealloc_int",        (c_ptr,),                   c_void)
py_dealloc_float      = extern("py_dealloc_float",      (c_ptr,),                   c_void)
py_dealloc_str        = extern("py_dealloc_str",        (c_ptr,),                   c_void)
py_dealloc_list       = extern("py_dealloc_list",       (c_ptr,),                   c_void)
py_dealloc_tuple      = extern("py_dealloc_tuple",      (c_ptr,),                   c_void)
py_dealloc_dict       = extern("py_dealloc_dict",       (c_ptr,),                   c_void)
py_dealloc_set        = extern("py_dealloc_set",        (c_ptr,),                   c_void)
py_dealloc_generic    = extern("py_dealloc_generic",    (c_ptr,),                   c_void)
py_class_dealloc      = extern("py_class_dealloc",      (c_ptr,),                   c_void)
py_instance_dealloc   = extern("py_instance_dealloc",   (c_ptr,),                   c_void)
py_dealloc_exc        = extern("py_dealloc_exc",        (c_ptr,),                   c_void)


# NOTE: pcc-Python initializes module-level integers in the
# auto-generated main(), which the Makefile strips for library .o
# builds. So we inline the type-tag and flag literals at each use
# site instead of declaring them as module constants.
#
#   PY_FLAG_IMMORTAL  = 1
#   PY_TYPE_INT       = 2
#   PY_TYPE_FLOAT     = 3
#   PY_TYPE_STR       = 4
#   PY_TYPE_LIST      = 5
#   PY_TYPE_DICT      = 6
#   PY_TYPE_TUPLE     = 7
#   PY_TYPE_SET       = 8
#   PY_TYPE_CLASS     = 10
#   PY_TYPE_INSTANCE  = 11
#   PY_TYPE_EXC       = 12
#   PY_TYPE_USER      = 100


@c_abi_export("py_bool_from_bit")
def py_bool_from_bit(b: int):
    if b != 0:
        return global_load_ptr("py_True")
    return global_load_ptr("py_False")


@c_abi_export("py_incref")
def py_incref(o) -> None:
    if ptr_is_null(o):
        return
    if is_tagged_int(o):
        return
    flags: int = load_i32(o, 12)
    if (flags & 1) != 0:           # PY_FLAG_IMMORTAL
        return
    rc: int = load_i64(o, 0)
    store_i64(o, 0, rc + 1)


@c_abi_export("py_decref")
def py_decref(o) -> None:
    if ptr_is_null(o):
        return
    if is_tagged_int(o):
        return
    flags: int = load_i32(o, 12)
    if (flags & 1) != 0:           # PY_FLAG_IMMORTAL
        return
    rc: int = load_i64(o, 0)
    new_rc: int = rc - 1
    store_i64(o, 0, new_rc)
    if new_rc > 0:
        return

    tag: int = load_i32(o, 8)
    if tag == 2:        # PY_TYPE_INT
        py_dealloc_int(o)
        return
    if tag == 3:        # PY_TYPE_FLOAT
        py_dealloc_float(o)
        return
    if tag == 4:        # PY_TYPE_STR
        py_dealloc_str(o)
        return
    if tag == 5:        # PY_TYPE_LIST
        py_dealloc_list(o)
        return
    if tag == 7:        # PY_TYPE_TUPLE
        py_dealloc_tuple(o)
        return
    if tag == 6:        # PY_TYPE_DICT
        py_dealloc_dict(o)
        return
    if tag == 8:        # PY_TYPE_SET
        py_dealloc_set(o)
        return
    if tag == 10:       # PY_TYPE_CLASS
        py_class_dealloc(o)
        return
    if tag == 11:       # PY_TYPE_INSTANCE
        py_instance_dealloc(o)
        return
    if tag == 12:       # PY_TYPE_EXC
        py_dealloc_exc(o)
        return
    if tag >= 100:      # PY_TYPE_USER
        py_instance_dealloc(o)
        return
    py_dealloc_generic(o)
