"""Raw tracing mark-cycle shared by GC backends 1 through 4."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    store_i32,
)


__pcc_freestanding__ = True


pcc_gc_gray_count_decrement_acq_rel = extern(
    "pcc_gc_gray_count_decrement_acq_rel", (), c_void
)
pcc_gc_gray_count_increment_acq_rel = extern(
    "pcc_gc_gray_count_increment_acq_rel", (), c_void
)
pcc_gc_gray_count_load_acquire = extern(
    "pcc_gc_gray_count_load_acquire", (), c_int64
)
pcc_gc_gray_current_roots = extern("pcc_gc_gray_current_roots", (), c_void)
pcc_gc_gray_refcount_external_roots = extern(
    "pcc_gc_gray_refcount_external_roots", (), c_void
)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_forwarding_index_find = extern(
    "pcc_gc_forwarding_index_find", (c_ptr,), c_ptr
)
pcc_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock", (c_ptr,), c_int64
)
pcc_gc_prepare_object_list_mark = extern(
    "pcc_gc_prepare_object_list_mark", (c_int64,), c_void
)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)


@c_abi_export("pcc_gc_trace_mark_gray_if_known")
def pcc_gc_trace_mark_gray_if_known(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    forwarding = pcc_gc_forwarding_index_find(obj)
    if ptr_is_null(forwarding) == 0:
        resolved = load_ptr(forwarding, 8)
        if ptr_is_null(resolved) == 0:
            if ptr_eq(resolved, obj) == 0:
                obj = resolved
    if pcc_gc_object_is_known_no_lock(obj) == 0:
        return
    flags: i64 = load_i32(obj, 12)
    if (flags & 32) == 0:
        if (flags & 16) == 0:
            pcc_gc_gray_count_increment_acq_rel()
        store_i32(obj, 12, (flags & ~56) | 16)


@c_abi_export("pcc_gc_trace_slot")
def pcc_gc_trace_slot(slot, role: i64, context) -> None:
    if role == 3:  # borrowed update-only metadata is not a graph edge
        return
    child = pcc_gc_load_ptr(null(), slot)
    pcc_gc_trace_mark_gray_if_known(child)


@c_abi_export("pcc_gc_trace_referents")
def pcc_gc_trace_referents(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    pcc_gc_visit_object_slots(obj, pcc_gc_trace_slot, null())


@c_abi_export("pcc_gc_seed_roots")
def pcc_gc_seed_roots() -> None:
    explicit_collect: i64 = load_i32(
        global_addr("pcc_gc_explicit_collect_active"),
        0,
    )
    pcc_gc_prepare_object_list_mark(explicit_collect)
    pcc_gc_gray_refcount_external_roots()
    pcc_gc_gray_current_roots()


@c_abi_export("pcc_gc_drain_all_gray_unlocked")
def pcc_gc_drain_all_gray_unlocked() -> i64:
    processed: i64 = 0
    while True:
        local_processed: i64 = 0
        node = global_load_ptr("pcc_gc_object_head")
        while ptr_is_null(node) == 0:
            nxt = load_ptr(node, 16)
            if load_i64(node, 32) != 0:
                node = nxt
                continue
            obj = load_ptr(node, 0)
            if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
                node = nxt
                continue
            flags: i64 = load_i32(obj, 12)
            if (flags & 16) != 0:
                pcc_gc_trace_referents(obj)
                pcc_gc_gray_count_decrement_acq_rel()
                store_i32(obj, 12, (flags & ~56) | 32)
                local_processed = local_processed + 1
                processed = processed + 1
            node = nxt
        if local_processed == 0:
            break
    return processed


@c_abi_export("pcc_gc_begin_mark_cycle")
def pcc_gc_begin_mark_cycle() -> None:
    pcc_gc_seed_roots()
    store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 0)
    global_store_ptr(
        "pcc_gc_trace_cursor",
        global_load_ptr("pcc_gc_object_head"),
    )
    if pcc_gc_gray_count_load_acquire() == 0:
        global_store_ptr("pcc_gc_trace_cursor", null())
        store_i32(global_addr("pcc_gc_mark_active"), 0, 0)


@c_abi_export("pcc_gc_finish_tracing_cycle")
def pcc_gc_finish_tracing_cycle() -> i64:
    stw: i64 = pcc_stop_the_world()
    if stw != 0:
        return 0
    pcc_gc_gray_current_roots()
    pcc_gc_drain_all_gray_unlocked()
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if load_i64(node, 32) != 0:
            pcc_thread_safepoint()
            node = nxt
            continue
        obj = load_ptr(node, 0)
        if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
            pcc_thread_safepoint()
            node = nxt
            continue
        flags: i64 = load_i32(obj, 12)
        if (flags & 8) != 0:
            store_i32(obj, 12, flags | 1024)
        else:
            store_i32(obj, 12, flags & ~1024)
        pcc_thread_safepoint()
        node = nxt
    pcc_resume_world()
    return 1
