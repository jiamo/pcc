"""pcc-Python port of py_obj_dealloc.c.

Type-specific object deallocators. The refcount dispatch in py_obj.py
still calls these symbols by name; the pcc-Python runtime archive
replaces the C object with this module while preserving the ABI.
"""
from pcc.extern import extern, c_abi_export, c_ptr, c_void
from pcc.unsafe import (
    free,
    global_load_ptr,
    load_i64,
    load_ptr,
    ptr_eq,
    ptr_is_null,
)


py_decref = extern("py_decref", (c_ptr,), c_void)


@c_abi_export("py_dealloc_int")
def py_dealloc_int(o) -> None:
    free(o)


@c_abi_export("py_dealloc_float")
def py_dealloc_float(o) -> None:
    free(o)


@c_abi_export("py_dealloc_str")
def py_dealloc_str(o) -> None:
    free(o)


@c_abi_export("py_dealloc_list")
def py_dealloc_list(o) -> None:
    length: int = load_i64(o, 16)
    items = load_ptr(o, 32)
    i: int = 0
    while i < length:
        py_decref(load_ptr(items, i * 8))
        i = i + 1
    free(items)
    free(o)


@c_abi_export("py_dealloc_tuple")
def py_dealloc_tuple(o) -> None:
    length: int = load_i64(o, 16)
    i: int = 0
    while i < length:
        py_decref(load_ptr(o, 24 + i * 8))
        i = i + 1
    free(o)


@c_abi_export("py_dealloc_dict")
def py_dealloc_dict(o) -> None:
    entries = load_ptr(o, 40)
    if ptr_is_null(entries) == 0:
        entries_used: int = load_i64(o, 48)
        i: int = 0
        while i < entries_used:
            off: int = i * 24
            key = load_ptr(entries, off + 8)
            if ptr_is_null(key) == 0:
                py_decref(key)
                py_decref(load_ptr(entries, off + 16))
            i = i + 1
        free(entries)
    free(load_ptr(o, 32))
    free(o)


@c_abi_export("py_dealloc_set")
def py_dealloc_set(o) -> None:
    entries = load_ptr(o, 40)
    if ptr_is_null(entries) == 0:
        dummy = global_load_ptr("py_set_dummy")
        capacity: int = load_i64(o, 24)
        i: int = 0
        while i < capacity:
            key = load_ptr(entries, i * 16 + 8)
            if ptr_is_null(key) == 0:
                if ptr_eq(key, dummy) == 0:
                    py_decref(key)
            i = i + 1
        free(entries)
    free(o)


@c_abi_export("py_dealloc_generic")
def py_dealloc_generic(o) -> None:
    free(o)
