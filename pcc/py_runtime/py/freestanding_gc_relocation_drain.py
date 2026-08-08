"""Backend 4 bounded object and page evacuation drains."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    global_addr,
    global_load_ptr,
    load_i32,
    load_ptr,
    ptr_eq,
    ptr_is_null,
    store_i32,
)


__pcc_freestanding__ = True


pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_backend4_relocate_copy_unlocked = extern(
    "pcc_gc_backend4_relocate_copy_unlocked", (c_ptr, c_int64), c_ptr
)
pcc_gc_backend4_remap_and_retire_unlocked = extern(
    "pcc_gc_backend4_remap_and_retire_unlocked", (), c_void
)
pcc_gc_backend4_zpage_page_for_owner = extern(
    "pcc_gc_backend4_zpage_page_for_owner", (c_ptr,), c_ptr
)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_gc_object_known_size = extern(
    "pcc_gc_object_known_size", (c_ptr,), c_int64
)
pcc_gc_relocate_copy = extern(
    "pcc_gc_relocate_copy", (c_ptr, c_int64), c_ptr
)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)


@c_abi_export("pcc_gc_relocation_drain_relocation_set_head")
def _relocation_set_head():
    return global_load_ptr("pcc_gc_relocation_set_head")


@c_abi_export("pcc_gc_relocation_drain_evacuation_page_head")
def _evacuation_page_head():
    return global_load_ptr("pcc_gc_backend4_evacuation_page_head")


@c_abi_export("pcc_gc_relocation_drain_note_incomplete_batch")
def _note_incomplete_batch(moved: i64) -> None:
    if moved <= 0 or ptr_is_null(_relocation_set_head()) != 0:
        return
    incomplete: i64 = load_i32(
        global_addr("pcc_gc_backend4_evacuation_incomplete_batches_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_incomplete_batches_count"),
        0,
        incomplete + 1,
    )


@c_abi_export("pcc_gc_relocation_drain_remap_if_drained_unlocked")
def _remap_if_drained_unlocked() -> None:
    if ptr_is_null(_relocation_set_head()) == 0:
        return
    if load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0:
        pcc_gc_backend4_remap_and_retire_unlocked()


@c_abi_export("pcc_gc_relocation_drain_selected")
def _relocate_selected(budget: i64) -> i64:
    if pcc_gc_backend() != 4 or budget <= 0:
        return 0
    moved: i64 = 0
    node = _relocation_set_head()
    while ptr_is_null(node) == 0 and moved < budget:
        nxt = load_ptr(node, 8)
        obj = load_ptr(node, 0)
        to_obj = pcc_gc_relocate_copy(obj, pcc_gc_object_known_size(obj))
        if ptr_is_null(to_obj) == 0:
            py_decref(to_obj)
            moved = moved + 1
            if (moved & 15) == 0:
                pcc_thread_safepoint()
        node = nxt
    _note_incomplete_batch(moved)
    if ptr_is_null(_relocation_set_head()) != 0:
        pcc_py_gc_minor_graph_lock()
        _remap_if_drained_unlocked()
        pcc_py_gc_minor_graph_unlock()
    return moved


@c_abi_export("pcc_gc_backend4_evacuation_drain")
def pcc_gc_backend4_evacuation_drain(budget: i64) -> i64:
    pcc_gc_config_ensure()
    return _relocate_selected(budget)


@c_abi_export("pcc_gc_relocation_drain_selected_page")
def _relocate_selected_page(page) -> i64:
    if ptr_is_null(page) != 0:
        return 0
    moved: i64 = 0
    node = _relocation_set_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 8)
        obj = load_ptr(node, 0)
        owner_page = pcc_gc_backend4_zpage_page_for_owner(obj)
        if ptr_eq(owner_page, page) != 0:
            to_obj = pcc_gc_backend4_relocate_copy_unlocked(
                obj, pcc_gc_object_known_size(obj)
            )
            if ptr_is_null(to_obj) == 0:
                py_decref(to_obj)
                moved = moved + 1
                if (moved & 15) == 0:
                    pcc_thread_safepoint()
        node = nxt
    return moved


@c_abi_export("pcc_gc_backend4_evacuation_page_drain")
def pcc_gc_backend4_evacuation_page_drain(page_budget: i64) -> i64:
    backend: i64 = pcc_gc_config_ensure()
    if backend != 4 or page_budget <= 0:
        return 0
    moved: i64 = 0
    pages: i64 = 0
    pcc_py_gc_minor_graph_lock()
    while pages < page_budget:
        head = _evacuation_page_head()
        if ptr_is_null(head) != 0:
            break
        page = load_ptr(head, 0)
        page_moved: i64 = _relocate_selected_page(page)
        if page_moved <= 0:
            break
        moved = moved + page_moved
        pages = pages + 1
    _note_incomplete_batch(moved)
    _remap_if_drained_unlocked()
    pcc_py_gc_minor_graph_unlock()
    return moved
