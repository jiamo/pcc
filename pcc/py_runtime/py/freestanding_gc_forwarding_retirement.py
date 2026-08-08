"""Backend 4 one-epoch forwarding and source-page retirement."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    free,
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
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


py_decref = extern("py_decref", (c_ptr,), c_void)
pcc_dealloc_cascade_active = extern(
    "pcc_dealloc_cascade_active", (), c_int64
)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_backend4_remap_referents = extern(
    "pcc_gc_backend4_remap_referents", (c_ptr,), c_void
)
pcc_gc_backend4_zpage_clear_active_page = extern(
    "pcc_gc_backend4_zpage_clear_active_page", (c_ptr,), c_void
)
pcc_gc_backend4_zpage_destroy = extern(
    "pcc_gc_backend4_zpage_destroy", (c_ptr,), c_void
)
pcc_gc_backend4_zpage_find_page_for_addr = extern(
    "pcc_gc_backend4_zpage_find_page_for_addr", (c_ptr, c_int64), c_ptr
)
pcc_gc_backend4_zpage_unlink_page = extern(
    "pcc_gc_backend4_zpage_unlink_page", (c_ptr,), c_void
)
pcc_gc_forwarding_index_remove = extern(
    "pcc_gc_forwarding_index_remove", (c_ptr,), c_ptr
)
pcc_gc_forwarding_list_head = extern(
    "pcc_gc_forwarding_list_head", (), c_ptr
)
pcc_gc_forwarding_target_index_remove = extern(
    "pcc_gc_forwarding_target_index_remove", (c_ptr,), c_ptr
)
pcc_gc_forwarding_target_unlink = extern(
    "pcc_gc_forwarding_target_unlink", (c_ptr,), c_void
)
pcc_gc_forwarding_unlink_main = extern(
    "pcc_gc_forwarding_unlink_main", (c_ptr,), c_void
)
pcc_gc_identity_remove = extern("pcc_gc_identity_remove", (c_ptr,), c_void)
pcc_gc_managed_pointer_index_remove = extern(
    "pcc_gc_managed_pointer_index_remove", (c_ptr,), c_int64
)
pcc_gc_live_bytes_subtract = extern(
    "pcc_gc_live_bytes_subtract", (c_int64,), c_void
)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_object_index_remove = extern(
    "pcc_gc_object_index_remove", (c_ptr,), c_ptr
)
pcc_gc_object_list_head = extern("pcc_gc_object_list_head", (), c_ptr)
pcc_gc_object_node_freeing = extern(
    "pcc_gc_object_node_freeing", (c_ptr,), c_int64
)
pcc_gc_object_node_release = extern(
    "pcc_gc_object_node_release", (c_ptr,), c_void
)
pcc_gc_object_node_size = extern(
    "pcc_gc_object_node_size", (c_ptr,), c_int64
)
pcc_gc_object_node_unlink = extern(
    "pcc_gc_object_node_unlink", (c_ptr,), c_void
)
pcc_gc_visit_registered_root_slots = extern(
    "pcc_gc_visit_registered_root_slots", (c_int64, c_int64), c_int64
)


@c_abi_export("pcc_gc_backend4_park_page")
def pcc_gc_backend4_park_page(page) -> None:
    if ptr_is_null(page) != 0:
        return
    pcc_gc_backend4_zpage_clear_active_page(page)
    store_ptr(page, 112, null())
    store_ptr(page, 56, global_load_ptr("pcc_gc_backend4_parked_head"))
    global_store_ptr("pcc_gc_backend4_parked_head", page)


@c_abi_export("pcc_gc_backend4_drain_parked_pages")
def pcc_gc_backend4_drain_parked_pages() -> None:
    page = global_load_ptr("pcc_gc_backend4_parked_head")
    global_store_ptr("pcc_gc_backend4_parked_head", null())
    while ptr_is_null(page) == 0:
        nxt = load_ptr(page, 56)
        store_ptr(page, 56, null())
        pcc_gc_backend4_zpage_destroy(page)
        page = nxt


@c_abi_export("pcc_gc_backend4_release_retained_pages_unlocked")
def _release_retained_pages() -> None:
    # A source page is first parked when its final forwarding entry retires,
    # then moved to the retained list by the next remap.  Only the following
    # remap may release its physical span: by then two complete root/referent
    # rewrite epochs have passed, so a legitimate value surviving either
    # safepoint must have been reloaded from an updateable slot.  Keep an
    # invariant-violating page quarantined instead of risking a use-after-free.
    page = global_load_ptr("pcc_gc_backend4_retained_page_head")
    global_store_ptr("pcc_gc_backend4_retained_page_head", null())
    while ptr_is_null(page) == 0:
        nxt = load_ptr(page, 56)
        store_ptr(page, 56, null())
        if (
            load_i64(page, 32) > 0
            or load_i64(page, 88) > 0
            or load_i64(page, 96) > 0
        ):
            store_ptr(
                page,
                56,
                global_load_ptr("pcc_gc_backend4_retained_page_head"),
            )
            global_store_ptr("pcc_gc_backend4_retained_page_head", page)
        else:
            span = load_ptr(page, 72)
            if ptr_is_null(span) == 0:
                store_ptr(page, 72, null())
                free(span)
            free(page)
        page = nxt


@c_abi_export("pcc_gc_backend4_note_forwarding_removed_on_page")
def pcc_gc_backend4_note_forwarding_removed_on_page(page) -> None:
    if ptr_is_null(page) != 0:
        return
    fwd: i64 = load_i64(page, 96)
    if fwd > 0:
        fwd = fwd - 1
        store_i64(page, 96, fwd)
    if (
        load_i32(page, 104) != 0
        and fwd <= 0
        and load_i64(page, 32) <= 0
        and load_i64(page, 88) <= 0
        and pcc_dealloc_cascade_active() == 0
    ):
        # While a trash cascade is active the page may still own queued
        # objects; leave the deferred flag set — the post-drain sweep
        # (pcc_gc_backend4_sweep_deferred_recycles) completes it. Keep the
        # deferred-page counter in step so the sweep's O(1) no-op check
        # stays accurate.
        store_i32(page, 104, 0)
        counter = global_addr("pcc_gc_backend4_deferred_recycle_pages")
        remaining: i64 = load_i64(counter, 0)
        if remaining > 0:
            store_i64(counter, 0, remaining - 1)
        pcc_gc_backend4_zpage_unlink_page(page)
        pcc_gc_backend4_park_page(page)


@c_abi_export("pcc_gc_backend4_zpage_note_forwarding_removed")
def pcc_gc_backend4_zpage_note_forwarding_removed(from_obj) -> None:
    if pcc_gc_backend() != 4:
        return
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return
    if (load_i32(from_obj, 12) & 65536) == 0:
        return
    pcc_gc_backend4_note_forwarding_removed_on_page(
        pcc_gc_backend4_zpage_find_page_for_addr(from_obj, 16)
    )


@c_abi_export("pcc_gc_forwarding_remove")
def pcc_gc_forwarding_remove(from_obj) -> None:
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return
    node = pcc_gc_forwarding_index_remove(from_obj)
    if ptr_is_null(node) != 0:
        return
    pcc_gc_forwarding_target_unlink(node)
    pcc_gc_forwarding_unlink_main(node)
    target = load_ptr(node, 8)
    py_decref(target)
    from_page = load_ptr(node, 48)
    free(node)
    population: i64 = load_i32(global_addr("pcc_gc_forwarding_population"), 0)
    if population > 0:
        store_i32(
            global_addr("pcc_gc_forwarding_population"), 0, population - 1
        )
    if ptr_is_null(from_page) == 0:
        pcc_gc_backend4_note_forwarding_removed_on_page(from_page)
    else:
        pcc_gc_backend4_zpage_note_forwarding_removed(from_obj)


@c_abi_export("pcc_gc_retire_forwarded_source_unlocked")
def _retire_forwarded_source(from_obj) -> None:
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return
    pcc_gc_identity_remove(from_obj)
    pcc_gc_managed_pointer_index_remove(from_obj)
    dead = pcc_gc_object_index_find(from_obj)
    if ptr_is_null(dead) != 0:
        scan = pcc_gc_object_list_head()
        while ptr_is_null(scan) == 0:
            if ptr_eq(load_ptr(scan, 0), from_obj) != 0:
                dead = scan
                break
            scan = load_ptr(scan, 16)
    if ptr_is_null(dead) != 0:
        return
    size: i64 = pcc_gc_object_node_size(dead)
    if pcc_gc_object_node_freeing(dead) == 0 and size > 0:
        pcc_gc_live_bytes_subtract(size)
    pcc_gc_object_index_remove(from_obj)
    pcc_gc_object_node_unlink(dead)
    pcc_gc_object_node_release(dead)


@c_abi_export("pcc_gc_forwarding_remove_target")
def pcc_gc_forwarding_remove_target(target) -> None:
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0:
        return
    node = pcc_gc_forwarding_target_index_remove(target)
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 32)
        from_obj = load_ptr(node, 0)
        pcc_gc_forwarding_index_remove(from_obj)
        pcc_gc_forwarding_unlink_main(node)
        # A target can die before the usual two-epoch remap retirement.  Its
        # old shell must leave the object index immediately or stale pointers
        # remain classified as live managed objects.
        _retire_forwarded_source(from_obj)
        store_ptr(node, 32, null())
        store_ptr(node, 40, null())
        from_page = load_ptr(node, 48)
        free(node)
        population: i64 = load_i32(
            global_addr("pcc_gc_forwarding_population"), 0
        )
        if population > 0:
            store_i32(
                global_addr("pcc_gc_forwarding_population"),
                0,
                population - 1,
            )
        if ptr_is_null(from_page) == 0:
            pcc_gc_backend4_note_forwarding_removed_on_page(from_page)
        else:
            pcc_gc_backend4_zpage_note_forwarding_removed(from_obj)
        node = nxt


@c_abi_export("pcc_gc_backend4_remap_and_retire_unlocked")
def pcc_gc_backend4_remap_and_retire_unlocked() -> None:
    if pcc_gc_backend() != 4:
        return
    # Two-epoch quarantine: release the prior retained generation before
    # parked pages from the immediately preceding remap enter that generation.
    _release_retained_pages()
    pcc_gc_backend4_drain_parked_pages()
    if ptr_is_null(pcc_gc_forwarding_list_head()) != 0:
        return
    node = pcc_gc_object_list_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if load_i64(node, 32) == 0:
            pcc_gc_backend4_remap_referents(load_ptr(node, 0))
        node = nxt
    pcc_gc_visit_registered_root_slots(3, 0)

    fwd = pcc_gc_forwarding_list_head()
    while ptr_is_null(fwd) == 0:
        nxt = load_ptr(fwd, 16)
        old = load_ptr(fwd, 0)
        if ptr_is_null(old) != 0 or is_tagged_int(old) != 0:
            pcc_gc_forwarding_target_unlink(fwd)
            pcc_gc_forwarding_unlink_main(fwd)
            py_decref(load_ptr(fwd, 8))
            free(fwd)
            population: i64 = load_i32(
                global_addr("pcc_gc_forwarding_population"), 0
            )
            if population > 0:
                store_i32(
                    global_addr("pcc_gc_forwarding_population"),
                    0,
                    population - 1,
                )
            fwd = nxt
            continue
        old_flags: i64 = load_i32(old, 12)
        if (old_flags & 131072) == 0:
            store_i32(old, 12, old_flags | 131072)
            fwd = nxt
            continue
        store_i32(old, 12, old_flags & ~(2048 | 131072))
        _retire_forwarded_source(old)
        pcc_gc_forwarding_remove(old)
        fwd = nxt
