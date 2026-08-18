"""Scheduler and suspended-continuation root registry ownership.

This strict module owns registry mutation and root-map metadata decoding.  The
mapped-root trace/rewrite walker remains in ``py_gc_backend.py`` until its
collector resolver can move as one slot-contract slice.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_store_i32,
    free,
    gc_backend_current,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)


@c_abi_export("pcc_gc_cycle_requested_store_release")
def pcc_gc_cycle_requested_store_release(value: i64) -> None:
    slot = global_addr("pcc_gc_cycle_requested")
    atomic_store_i32(slot, 0, value, "release")


@c_abi_export("pcc_gc_root_registry_note_mutation_locked")
def pcc_gc_root_registry_note_mutation_locked() -> None:
    revision: i64 = load_i64(
        global_addr("pcc_gc_root_registry_revision"), 0
    )
    if revision == 9223372036854775807:
        revision = 1
    else:
        revision = revision + 1
    store_i64(global_addr("pcc_gc_root_registry_revision"), 0, revision)


@c_abi_export("pcc_gc_root_slot_count_from_map")
def pcc_gc_root_slot_count_from_map(frame_map) -> i64:
    if ptr_is_null(frame_map) != 0:
        return 0
    root_count: i64 = load_i32(frame_map, 0)
    if root_count == -2147483648:
        return 0
    if root_count < 0:
        root_count = 0 - root_count
    if root_count <= 0 or root_count > 100000:
        return 0
    return root_count


@c_abi_export("pcc_gc_root_map_is_borrowed")
def pcc_gc_root_map_is_borrowed(frame_map) -> i64:
    if ptr_is_null(frame_map) != 0:
        return 0
    if load_i32(frame_map, 0) < 0:
        return 1
    return 0


@c_abi_export("pcc_gc_scheduler_root_link_locked")
def pcc_gc_scheduler_root_link_locked(node) -> None:
    if ptr_is_null(node) != 0:
        return
    head = global_load_ptr("pcc_gc_scheduler_root_head")
    store_ptr(node, 8, head)
    store_ptr(node, 16, null())
    if ptr_is_null(head) == 0:
        store_ptr(head, 16, node)
    global_store_ptr("pcc_gc_scheduler_root_head", node)
    pcc_gc_root_registry_note_mutation_locked()


@c_abi_export("pcc_gc_scheduler_root_unlink_locked")
def pcc_gc_scheduler_root_unlink_locked(node) -> i64:
    if ptr_is_null(node) != 0:
        return 0
    previous = load_ptr(node, 16)
    next_node = load_ptr(node, 8)
    if ptr_eq(
        global_load_ptr("pcc_gc_backend3_scheduler_root_scan_cursor"),
        node,
    ) != 0:
        global_store_ptr(
            "pcc_gc_backend3_scheduler_root_scan_cursor", next_node
        )
        store_i64(
            global_addr("pcc_gc_backend3_scheduler_root_scan_slot"), 0, 0
        )
    if ptr_is_null(previous) == 0:
        store_ptr(previous, 8, next_node)
    elif ptr_eq(global_load_ptr("pcc_gc_scheduler_root_head"), node) != 0:
        global_store_ptr("pcc_gc_scheduler_root_head", next_node)
    else:
        cursor_previous = null()
        cursor = global_load_ptr("pcc_gc_scheduler_root_head")
        while ptr_is_null(cursor) == 0 and ptr_eq(cursor, node) == 0:
            cursor_previous = cursor
            cursor = load_ptr(cursor, 8)
        if ptr_is_null(cursor) != 0:
            return 0
        if ptr_is_null(cursor_previous) != 0:
            global_store_ptr("pcc_gc_scheduler_root_head", next_node)
        else:
            store_ptr(cursor_previous, 8, next_node)
    if ptr_is_null(next_node) == 0:
        store_ptr(next_node, 16, previous)
    store_ptr(node, 8, null())
    store_ptr(node, 16, null())
    pcc_gc_root_registry_note_mutation_locked()
    return 1


@c_abi_export("pcc_gc_scheduler_root_register_handle")
def pcc_gc_scheduler_root_register_handle(slot) -> c_ptr:
    gc_backend_current()
    if ptr_is_null(slot) != 0:
        return null()
    node = malloc(24)
    if ptr_is_null(node) != 0:
        return null()
    memset(node, 0, 24)
    store_ptr(node, 0, slot)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_scheduler_root_link_locked(node)
    pcc_py_gc_minor_graph_unlock()
    pcc_gc_cycle_requested_store_release(1)
    return node


@c_abi_export("pcc_gc_scheduler_root_register")
def pcc_gc_scheduler_root_register(slot) -> None:
    pcc_gc_scheduler_root_register_handle(slot)


@c_abi_export("pcc_gc_scheduler_root_unregister_handle")
def pcc_gc_scheduler_root_unregister_handle(handle) -> None:
    gc_backend_current()
    if ptr_is_null(handle) != 0:
        return
    pcc_py_gc_minor_graph_lock()
    pcc_gc_scheduler_root_unlink_locked(handle)
    pcc_py_gc_minor_graph_unlock()
    free(handle)
    pcc_gc_cycle_requested_store_release(1)


@c_abi_export("pcc_gc_scheduler_root_unregister")
def pcc_gc_scheduler_root_unregister(slot) -> None:
    gc_backend_current()
    if ptr_is_null(slot) != 0:
        return
    dead = null()
    pcc_py_gc_minor_graph_lock()
    node = global_load_ptr("pcc_gc_scheduler_root_head")
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), slot) != 0:
            dead = node
            pcc_gc_scheduler_root_unlink_locked(node)
            break
        node = load_ptr(node, 8)
    pcc_py_gc_minor_graph_unlock()
    if ptr_is_null(dead) == 0:
        free(dead)
        pcc_gc_cycle_requested_store_release(1)


@c_abi_export("pcc_gc_register_continuation_root")
def pcc_gc_register_continuation_root(frame_map, slots) -> None:
    gc_backend_current()
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return
    root_count: i64 = pcc_gc_root_slot_count_from_map(frame_map)
    if root_count <= 0:
        return
    node_size: i64 = 48 + root_count * 8
    node = malloc(node_size)
    if ptr_is_null(node) != 0:
        return
    memset(node, 0, node_size)
    store_ptr(node, 0, frame_map)
    store_ptr(node, 8, slots)
    store_i64(node, 24, root_count)
    store_i32(node, 32, pcc_gc_root_map_is_borrowed(frame_map))
    store_ptr(node, 40, ptr_add(node, 48))
    pcc_py_gc_minor_graph_lock()
    store_ptr(node, 16, global_load_ptr("pcc_gc_continuation_root_head"))
    global_store_ptr("pcc_gc_continuation_root_head", node)
    pcc_gc_root_registry_note_mutation_locked()
    pcc_gc_cycle_requested_store_release(1)
    pcc_py_gc_minor_graph_unlock()


@c_abi_export("pcc_gc_unregister_continuation_root")
def pcc_gc_unregister_continuation_root(slots) -> None:
    gc_backend_current()
    if ptr_is_null(slots) != 0:
        return
    dead = null()
    pcc_py_gc_minor_graph_lock()
    previous = null()
    node = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(node) == 0:
        next_node = load_ptr(node, 16)
        if ptr_eq(load_ptr(node, 8), slots) != 0:
            if ptr_eq(
                global_load_ptr(
                    "pcc_gc_backend3_continuation_root_scan_cursor"
                ),
                node,
            ) != 0:
                global_store_ptr(
                    "pcc_gc_backend3_continuation_root_scan_cursor",
                    next_node,
                )
                store_i64(
                    global_addr("pcc_gc_backend3_frame_root_scan_slot"),
                    0,
                    0,
                )
            if ptr_is_null(previous) != 0:
                global_store_ptr("pcc_gc_continuation_root_head", next_node)
            else:
                store_ptr(previous, 16, next_node)
            dead = node
            pcc_gc_root_registry_note_mutation_locked()
            pcc_gc_cycle_requested_store_release(1)
            break
        previous = node
        node = next_node
    pcc_py_gc_minor_graph_unlock()
    if ptr_is_null(dead) == 0:
        free(dead)
