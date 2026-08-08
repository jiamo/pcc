"""Raw object-node pool, object list, and Backend 3 young worklist."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, extern
from pcc.unsafe import (
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_object_node_is_active = extern(
    "pcc_gc_object_node_is_active", (c_ptr,), c_int64
)


@c_abi_export("pcc_gc_object_list_head")
def pcc_gc_object_list_head() -> c_ptr:
    return global_load_ptr("pcc_gc_object_head")


@c_abi_export("pcc_gc_object_set_list_head")
def pcc_gc_object_set_list_head(head: c_ptr) -> None:
    global_store_ptr("pcc_gc_object_head", head)


@c_abi_export("pcc_gc_trace_cursor_load")
def pcc_gc_trace_cursor_load() -> c_ptr:
    return global_load_ptr("pcc_gc_trace_cursor")


@c_abi_export("pcc_gc_trace_cursor_store")
def pcc_gc_trace_cursor_store(node: c_ptr) -> None:
    global_store_ptr("pcc_gc_trace_cursor", node)


@c_abi_export("pcc_gc_backend3_young_list_head")
def pcc_gc_backend3_young_list_head() -> c_ptr:
    return global_load_ptr("pcc_gc_backend3_young_head")


@c_abi_export("pcc_gc_backend3_young_set_head")
def pcc_gc_backend3_young_set_head(node: c_ptr) -> None:
    global_store_ptr("pcc_gc_backend3_young_head", node)


@c_abi_export("pcc_gc_object_node_size")
def pcc_gc_object_node_size(node: c_ptr) -> i64:
    return load_i64(node, 8)


@c_abi_export("pcc_gc_object_node_next")
def pcc_gc_object_node_next(node: c_ptr) -> c_ptr:
    return load_ptr(node, 16)


@c_abi_export("pcc_gc_object_node_set_next")
def pcc_gc_object_node_set_next(node: c_ptr, nxt: c_ptr) -> None:
    store_ptr(node, 16, nxt)


@c_abi_export("pcc_gc_object_node_minor_block")
def pcc_gc_object_node_minor_block(node: c_ptr) -> c_ptr:
    return load_ptr(node, 24)


@c_abi_export("pcc_gc_object_node_freeing")
def pcc_gc_object_node_freeing(node: c_ptr) -> i64:
    return load_i64(node, 32)


@c_abi_export("pcc_gc_object_node_set_freeing")
def pcc_gc_object_node_set_freeing(node: c_ptr, freeing: i64) -> None:
    store_i64(node, 32, freeing)


@c_abi_export("pcc_gc_object_node_prev")
def pcc_gc_object_node_prev(node: c_ptr) -> c_ptr:
    return load_ptr(node, 40)


@c_abi_export("pcc_gc_object_node_set_prev")
def pcc_gc_object_node_set_prev(node: c_ptr, prev: c_ptr) -> None:
    store_ptr(node, 40, prev)


@c_abi_export("pcc_gc_object_node_zpage")
def pcc_gc_object_node_zpage(node: c_ptr) -> c_ptr:
    return load_ptr(node, 48)


@c_abi_export("pcc_gc_object_node_set_zpage")
def pcc_gc_object_node_set_zpage(node: c_ptr, zpage_node: c_ptr) -> None:
    store_ptr(node, 48, zpage_node)


@c_abi_export("pcc_gc_object_node_gc_refs")
def pcc_gc_object_node_gc_refs(node: c_ptr) -> i64:
    return load_i64(node, 56)


@c_abi_export("pcc_gc_object_node_set_gc_refs")
def pcc_gc_object_node_set_gc_refs(node: c_ptr, value: i64) -> None:
    store_i64(node, 56, value)


@c_abi_export("pcc_gc_object_node_young_next")
def pcc_gc_object_node_young_next(node: c_ptr) -> c_ptr:
    return load_ptr(node, 64)


@c_abi_export("pcc_gc_object_node_set_young_next")
def pcc_gc_object_node_set_young_next(node: c_ptr, nxt: c_ptr) -> None:
    store_ptr(node, 64, nxt)


@c_abi_export("pcc_gc_object_node_young_prev")
def pcc_gc_object_node_young_prev(node: c_ptr) -> c_ptr:
    return load_ptr(node, 72)


@c_abi_export("pcc_gc_object_node_set_young_prev")
def pcc_gc_object_node_set_young_prev(node: c_ptr, prev: c_ptr) -> None:
    store_ptr(node, 72, prev)


@c_abi_export("pcc_gc_object_node_alloc")
def pcc_gc_object_node_alloc() -> c_ptr:
    head = global_load_ptr("pcc_gc_object_node_free_head")
    if ptr_is_null(head) == 0:
        global_store_ptr("pcc_gc_object_node_free_head", load_ptr(head, 16))
        count: i64 = load_i32(global_addr("pcc_gc_object_node_free_count"), 0)
        if count > 0:
            store_i32(global_addr("pcc_gc_object_node_free_count"), 0, count - 1)
        return head
    return malloc(80)


@c_abi_export("pcc_gc_object_node_release")
def pcc_gc_object_node_release(node: c_ptr) -> None:
    if ptr_is_null(node) != 0:
        return
    count: i64 = load_i32(global_addr("pcc_gc_object_node_free_count"), 0)
    if count >= 8192:
        free(node)
        return
    store_ptr(node, 16, global_load_ptr("pcc_gc_object_node_free_head"))
    global_store_ptr("pcc_gc_object_node_free_head", node)
    store_i32(global_addr("pcc_gc_object_node_free_count"), 0, count + 1)


@c_abi_export("pcc_gc_backend3_young_link_head")
def pcc_gc_backend3_young_link_head(node: c_ptr) -> None:
    if ptr_is_null(node) != 0:
        return
    head = pcc_gc_backend3_young_list_head()
    pcc_gc_object_node_set_young_prev(node, null())
    pcc_gc_object_node_set_young_next(node, head)
    if ptr_is_null(head) == 0:
        pcc_gc_object_node_set_young_prev(head, node)
    pcc_gc_backend3_young_set_head(node)


@c_abi_export("pcc_gc_backend3_young_unlink")
def pcc_gc_backend3_young_unlink(node: c_ptr) -> None:
    if ptr_is_null(node) != 0:
        return
    prev = pcc_gc_object_node_young_prev(node)
    nxt = pcc_gc_object_node_young_next(node)
    if ptr_is_null(prev) == 0:
        pcc_gc_object_node_set_young_next(prev, nxt)
    elif ptr_eq(pcc_gc_backend3_young_list_head(), node) != 0:
        pcc_gc_backend3_young_set_head(nxt)
    else:
        pcc_gc_object_node_set_young_next(node, null())
        pcc_gc_object_node_set_young_prev(node, null())
        return
    if ptr_is_null(nxt) == 0:
        pcc_gc_object_node_set_young_prev(nxt, prev)
    pcc_gc_object_node_set_young_next(node, null())
    pcc_gc_object_node_set_young_prev(node, null())


@c_abi_export("pcc_gc_object_node_unlink")
def pcc_gc_object_node_unlink(node: c_ptr) -> None:
    prev = pcc_gc_object_node_prev(node)
    nxt = pcc_gc_object_node_next(node)
    if ptr_eq(pcc_gc_trace_cursor_load(), node) != 0:
        pcc_gc_trace_cursor_store(nxt)
    pcc_gc_backend3_young_unlink(node)
    if ptr_is_null(prev) != 0:
        pcc_gc_object_set_list_head(nxt)
    else:
        pcc_gc_object_node_set_next(prev, nxt)
    if ptr_is_null(nxt) == 0:
        pcc_gc_object_node_set_prev(nxt, prev)


@c_abi_export("pcc_gc_backend3_young_rebuild")
def pcc_gc_backend3_young_rebuild() -> None:
    pcc_gc_backend3_young_set_head(null())
    node = pcc_gc_object_list_head()
    while ptr_is_null(node) == 0:
        pcc_gc_object_node_set_young_next(node, null())
        pcc_gc_object_node_set_young_prev(node, null())
        if pcc_gc_object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            if (load_i32(obj, 12) & 128) != 0:
                pcc_gc_backend3_young_link_head(node)
        node = pcc_gc_object_node_next(node)


@c_abi_export("pcc_gc_object_known_size")
def pcc_gc_object_known_size(obj: c_ptr) -> i64:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    node = pcc_gc_object_index_find(obj)
    if ptr_is_null(node) == 0 and pcc_gc_object_node_freeing(node) == 0:
        return pcc_gc_object_node_size(node)
    return 0


@c_abi_export("pcc_gc_live_bytes_subtract")
def pcc_gc_live_bytes_subtract(size: i64) -> None:
    if size <= 0:
        return
    live: i64 = load_i32(global_addr("pcc_gc_live_bytes"), 0)
    if size >= live:
        store_i32(global_addr("pcc_gc_live_bytes"), 0, 0)
    else:
        store_i32(global_addr("pcc_gc_live_bytes"), 0, live - size)
