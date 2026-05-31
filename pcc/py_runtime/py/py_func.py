"""pcc-Python port of py_func.c.

Function object layout:
    offset  0   PyObjectHeader
    offset 16   PyNativeFuncEntry
    offset 24   captures tuple
    offset 32   borrowed const char* name, nullable
    offset 40   bound self object, nullable
    total size: 48 bytes
"""

from pcc.extern import extern, c_abi_export, c_int32, c_int64, c_ptr, c_void
from pcc.unsafe import (
    call_ptr2,
    ptr_add,
    is_tagged_int,
    load_i32,
    load_ptr,
    null,
    ptr_is_null,
    store_ptr,
)


py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_gc_track = extern("py_gc_track", (c_ptr,), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)


def _checked_func(obj):
    if ptr_is_null(obj):
        return null()
    if is_tagged_int(obj):
        return null()
    if load_i32(obj, 8) != 9:
        return null()
    return obj


@c_abi_export("py_func_new_bound")
def py_func_new_bound(entry, captures_tuple, name, self_obj):
    if ptr_is_null(entry):
        return null()
    fn = pcc_gc_alloc(48, 9, 0)
    if ptr_is_null(fn):
        return null()
    store_ptr(fn, 16, entry)
    store_ptr(fn, 32, name)
    store_ptr(fn, 40, null())
    captures = captures_tuple
    made_captures: int = 0
    if ptr_is_null(captures):
        captures = py_tuple_new(0)
        made_captures = 1
    store_ptr(fn, 24, null())
    pcc_gc_store_ptr(fn, ptr_add(fn, 24), captures)
    if ptr_is_null(self_obj) == 0:
        pcc_gc_store_ptr(fn, ptr_add(fn, 40), self_obj)
    if made_captures != 0:
        py_decref(captures)
    py_gc_track(fn)
    return fn


@c_abi_export("py_func_new_named")
def py_func_new_named(entry, captures_tuple, name):
    return py_func_new_bound(entry, captures_tuple, name, null())


@c_abi_export("py_func_new")
def py_func_new(entry, captures_tuple):
    return py_func_new_named(entry, captures_tuple, null())


@c_abi_export("py_func_call")
def py_func_call(callable_obj, args_tuple):
    fn = _checked_func(callable_obj)
    if ptr_is_null(fn):
        return null()
    entry = load_ptr(fn, 16)
    if ptr_is_null(entry):
        return null()
    args = args_tuple
    made_args = 0
    if ptr_is_null(args):
        args = py_tuple_new(0)
        made_args = 1
    captures = pcc_gc_load_ptr(fn, ptr_add(fn, 24))
    result = call_ptr2(entry, captures, args)
    if made_args != 0:
        py_decref(args)
    return result


@c_abi_export("py_dealloc_func")
def py_dealloc_func(o) -> None:
    captures = pcc_gc_load_ptr(o, ptr_add(o, 24))
    self_obj = pcc_gc_load_ptr(o, ptr_add(o, 40))
    py_decref(captures)
    if ptr_is_null(self_obj) == 0:
        py_decref(self_obj)
    pcc_gc_free_object_memory(o)
