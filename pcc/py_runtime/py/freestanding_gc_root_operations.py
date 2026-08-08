"""Known-object, root gray, root resolve, and gray-counter primitives."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_cas_i32,
    atomic_load_i32,
    atomic_rmw_i32,
    atomic_store_i32,
    global_addr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_forwarding_index_find = extern("pcc_gc_forwarding_index_find", (c_ptr,), c_ptr)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)


@c_abi_export("pcc_gc_gray_count_load_acquire")
def pcc_gc_gray_count_load_acquire() -> i64:
    return atomic_load_i32(global_addr("pcc_gc_gray_count"), 0, "acquire")


@c_abi_export("pcc_gc_gray_count_store_release")
def pcc_gc_gray_count_store_release(value: i64) -> None:
    atomic_store_i32(global_addr("pcc_gc_gray_count"), 0, value, "release")


@c_abi_export("pcc_gc_gray_count_increment_acq_rel")
def pcc_gc_gray_count_increment_acq_rel() -> None:
    atomic_rmw_i32("add", global_addr("pcc_gc_gray_count"), 0, 1, "acq_rel")


@c_abi_export("pcc_gc_gray_count_decrement_acq_rel")
def pcc_gc_gray_count_decrement_acq_rel() -> None:
    slot = global_addr("pcc_gc_gray_count")
    old: i64 = atomic_load_i32(slot, 0, "acquire")
    while old > 0:
        desired: i64 = old - 1
        observed: i64 = atomic_cas_i32(
            slot,
            0,
            old,
            desired,
            "acq_rel",
            "acquire",
        )
        if observed == old:
            return
        old = observed


@c_abi_export("pcc_gc_object_is_known_no_lock")
def pcc_gc_object_is_known_no_lock(obj) -> i64:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    node = pcc_gc_object_index_find(obj)
    if ptr_is_null(node) != 0:
        return 0
    return 1 if load_i64(node, 32) == 0 else 0


@c_abi_export("pcc_gc_mark_root_gray_if_known")
def pcc_gc_mark_root_gray_if_known(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    forwarding = pcc_gc_forwarding_index_find(obj)
    if ptr_is_null(forwarding) == 0:
        resolved = load_ptr(forwarding, 8)
        if ptr_is_null(resolved) == 0 and ptr_eq(resolved, obj) == 0:
            obj = resolved
    if pcc_gc_object_is_known_no_lock(obj) == 0:
        return
    flags: i64 = load_i32(obj, 12)
    if (flags & 16) == 0:
        pcc_gc_gray_count_increment_acq_rel()
    store_i32(obj, 12, (flags & ~56) | 16)


@c_abi_export("pcc_gc_resolve_root_slot_unlocked")
def pcc_gc_resolve_root_slot_unlocked(slot_base, slot_offset: i64):
    value = load_ptr(slot_base, slot_offset)
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return value
    if pcc_gc_object_is_known_no_lock(value) == 0:
        forwarding_unknown = pcc_gc_forwarding_index_find(value)
        if ptr_is_null(forwarding_unknown) == 0:
            resolved_unknown = load_ptr(forwarding_unknown, 8)
            if (
                ptr_is_null(resolved_unknown) == 0
                and ptr_eq(resolved_unknown, value) == 0
            ):
                if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4:
                    store_ptr(slot_base, slot_offset, resolved_unknown)
                    return resolved_unknown
                py_incref(resolved_unknown)
                store_ptr(slot_base, slot_offset, resolved_unknown)
                py_decref(value)
                return resolved_unknown
        return value
    flags: i64 = load_i32(value, 12)
    if (flags & 2048) == 0:
        return value
    forwarding = pcc_gc_forwarding_index_find(value)
    if ptr_is_null(forwarding) != 0:
        store_i32(value, 12, flags & ~2048)
        return value
    resolved = load_ptr(forwarding, 8)
    if ptr_is_null(resolved) != 0 or ptr_eq(resolved, value) != 0:
        store_i32(value, 12, flags & ~2048)
        return value
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4:
        store_ptr(slot_base, slot_offset, resolved)
        return resolved
    py_incref(resolved)
    store_ptr(slot_base, slot_offset, resolved)
    py_decref(value)
    return resolved
