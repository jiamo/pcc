"""Raw backend-0 cycle-collector actions over the shared slot contract."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
)


__pcc_freestanding__ = True


pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
py_gc_index_find = extern("py_gc_index_find", (c_ptr,), c_ptr)


@c_abi_export("pcc_gc_backend0_is_unreachable")
def pcc_gc_backend0_is_unreachable(o) -> i64:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    node = py_gc_index_find(o)
    if ptr_is_null(node) != 0:
        return 0
    if load_i32(node, 16) == 0:
        return 1
    return 0


@c_abi_export("pcc_gc_backend0_subtract_slot")
def pcc_gc_backend0_subtract_slot(slot, role: i64, context) -> None:
    if role == 3:  # borrowed update-only metadata is not a graph edge
        return
    child = pcc_gc_load_ptr(null(), slot)
    if ptr_is_null(child) != 0 or is_tagged_int(child) != 0:
        return
    node = py_gc_index_find(child)
    if ptr_is_null(node) == 0:
        store_i64(node, 8, load_i64(node, 8) - 1)


@c_abi_export("pcc_gc_backend0_visit_subtract")
def pcc_gc_backend0_visit_subtract(o) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    pcc_gc_visit_object_slots(o, pcc_gc_backend0_subtract_slot, null())


@c_abi_export("pcc_gc_backend0_mark_reachable")
def pcc_gc_backend0_mark_reachable(o) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    node = py_gc_index_find(o)
    if ptr_is_null(node) != 0 or load_i32(node, 16) != 0:
        return
    store_i32(node, 16, 1)
    pcc_gc_visit_object_slots(o, pcc_gc_backend0_mark_slot, null())


@c_abi_export("pcc_gc_backend0_mark_slot")
def pcc_gc_backend0_mark_slot(slot, role: i64, context) -> None:
    if role == 3:
        return
    pcc_gc_backend0_mark_reachable(pcc_gc_load_ptr(null(), slot))
