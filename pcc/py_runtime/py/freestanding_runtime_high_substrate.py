"""Freestanding TLS and graph-lock substrate for the pcc-Python runtime."""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import (
    atomic_cas_i32,
    atomic_store_i32,
    define_global_i32,
    define_thread_local_i32,
    define_thread_local_ptr_null,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    load_i32,
    store_i32,
    thread_safepoint,
)


__pcc_freestanding__ = True


define_thread_local_ptr_null("g_tls_pcc_py_gc_minor_current")
define_thread_local_ptr_null("g_tls_pcc_py_gc_pending_minor_block")
define_thread_local_i32("g_tls_pcc_py_gc_minor_graph_lock_depth", 0)
define_global_i32("g_pcc_py_gc_minor_graph_lock", 0)


@c_abi_export("pcc_py_gc_minor_current_get")
def pcc_py_gc_minor_current_get() -> c_ptr:
    return global_load_ptr("g_tls_pcc_py_gc_minor_current")


@c_abi_export("pcc_py_gc_minor_current_set")
def pcc_py_gc_minor_current_set(block: c_ptr) -> None:
    global_store_ptr("g_tls_pcc_py_gc_minor_current", block)


@c_abi_export("pcc_py_gc_pending_minor_block_get")
def pcc_py_gc_pending_minor_block_get() -> c_ptr:
    return global_load_ptr("g_tls_pcc_py_gc_pending_minor_block")


@c_abi_export("pcc_py_gc_pending_minor_block_set")
def pcc_py_gc_pending_minor_block_set(block: c_ptr) -> None:
    global_store_ptr("g_tls_pcc_py_gc_pending_minor_block", block)


@c_abi_export("pcc_py_gc_minor_graph_lock")
def pcc_py_gc_minor_graph_lock() -> None:
    # Take the thread-local's address once.  Every `global_addr` on a
    # thread-local is a `_tlv_get_addr` call on Darwin, and this pair was the
    # single hottest leaf in a `pcc1 -> pcc2` frontend worker (~261 of ~403
    # tlv samples).  The re-entrant fast path below is the common case and
    # used to pay for two lookups where one does.
    depth_slot = global_addr("g_tls_pcc_py_gc_minor_graph_lock_depth")
    depth: i64 = load_i32(depth_slot, 0)
    if depth > 0:
        store_i32(depth_slot, 0, depth + 1)
        return

    acquired: i64 = 0
    while acquired == 0:
        old: i64 = atomic_cas_i32(
            global_addr("g_pcc_py_gc_minor_graph_lock"),
            0,
            0,
            1,
            "acq_rel",
            "acquire",
        )
        if old == 0:
            acquired: i64 = 1
        else:
            thread_safepoint()
    store_i32(depth_slot, 0, 1)


@c_abi_export("pcc_py_gc_minor_graph_unlock")
def pcc_py_gc_minor_graph_unlock() -> None:
    depth_slot = global_addr("g_tls_pcc_py_gc_minor_graph_lock_depth")
    depth: i64 = load_i32(depth_slot, 0)
    if depth <= 0:
        return
    depth = depth - 1
    store_i32(depth_slot, 0, depth)
    if depth == 0:
        atomic_store_i32(
            global_addr("g_pcc_py_gc_minor_graph_lock"),
            0,
            0,
            "release",
        )
