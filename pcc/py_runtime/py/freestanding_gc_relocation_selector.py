"""Backend 4 relocation candidate scoring and page-grouped selection."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    abi_constant,
    global_addr,
    global_load_ptr,
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


pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_backend4_evacuation_page_add = extern(
    "pcc_gc_backend4_evacuation_page_add", (c_ptr,), c_int64
)
pcc_gc_backend4_evacuation_page_find = extern(
    "pcc_gc_backend4_evacuation_page_find", (c_ptr,), c_ptr
)
pcc_gc_backend4_owner_remembered_slots = extern(
    "pcc_gc_backend4_owner_remembered_slots", (c_ptr,), c_int64
)
pcc_gc_backend4_relocate_copy_supported_tag = extern(
    "pcc_gc_backend4_relocate_copy_supported_tag", (c_int64,), c_int64
)
pcc_gc_backend4_relocation_set_add = extern(
    "pcc_gc_backend4_relocation_set_add", (c_ptr,), c_int64
)
pcc_gc_backend4_relocation_set_find = extern(
    "pcc_gc_backend4_relocation_set_find", (c_ptr,), c_ptr
)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_gc_object_known_size = extern(
    "pcc_gc_object_known_size", (c_ptr,), c_int64
)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)


@c_abi_export("pcc_gc_relocation_selector_zpage_head")
def _zpage_head():
    return global_load_ptr("pcc_gc_backend4_zpage_head")


@c_abi_export("pcc_gc_relocation_selector_evacuation_policy_accept")
def _backend4_evacuation_policy_accept(size: i64) -> i64:
    if size <= 0:
        return 0
    if size <= 4096:
        return 1
    if size <= 65536:
        return 1
    return 0


@c_abi_export("pcc_gc_relocation_selector_evacuation_policy_defer_large")
def _backend4_evacuation_policy_defer_large(size: i64) -> i64:
    if size > 65536:
        return 1
    return 0


@c_abi_export("pcc_gc_relocation_selector_large_page_policy_accept")
def _backend4_large_page_evacuation_policy_accept(page, size: i64) -> i64:
    if ptr_is_null(page) != 0:
        return 0
    if size <= 65536:
        return 0
    if load_i32(page, 24) != 2:
        return 0
    if load_i64(page, 16) > load_i64(page, 8):
        return 1
    return 0


@c_abi_export("pcc_gc_relocation_selector_note_page_candidate")
def _backend4_note_page_candidate(size: i64, page) -> None:
    if size <= 0:
        return
    total_bytes: i64 = load_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_bytes_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_bytes_count"),
        0,
        total_bytes + size,
    )
    if size <= 4096:
        small: i64 = load_i32(
            global_addr("pcc_gc_backend4_small_page_candidates"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_small_page_candidates"), 0, small + 1
        )
        small_bytes: i64 = load_i32(
            global_addr("pcc_gc_backend4_small_page_candidate_bytes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_small_page_candidate_bytes_count"),
            0,
            small_bytes + size,
        )
    elif size <= 65536:
        medium: i64 = load_i32(
            global_addr("pcc_gc_backend4_medium_page_candidates"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_medium_page_candidates"), 0, medium + 1
        )
        medium_bytes: i64 = load_i32(
            global_addr("pcc_gc_backend4_medium_page_candidate_bytes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_medium_page_candidate_bytes_count"),
            0,
            medium_bytes + size,
        )
    if ptr_is_null(page) != 0:
        return
    page_bytes: i64 = load_i64(page, 8)
    if page_bytes <= 0:
        return
    zpage_total: i64 = load_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count"),
        0,
        zpage_total + page_bytes,
    )
    page_class: i64 = load_i32(page, 24)
    if page_class == 0:
        small_zpage: i64 = load_i32(
            global_addr("pcc_gc_backend4_small_page_candidate_zpage_bytes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_small_page_candidate_zpage_bytes_count"),
            0,
            small_zpage + page_bytes,
        )
    elif page_class == 1:
        medium_zpage: i64 = load_i32(
            global_addr("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count"),
            0,
            medium_zpage + page_bytes,
        )


@c_abi_export("pcc_gc_relocation_selector_candidate_score")
def _backend4_zpage_candidate_score(node, allow_large_pages: i64) -> i64:
    if ptr_is_null(node) != 0:
        return -1
    obj = load_ptr(node, 0)
    page = load_ptr(node, 8)
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0 or ptr_is_null(page) != 0:
        return -1
    if ptr_is_null(pcc_gc_backend4_relocation_set_find(obj)) == 0:
        return -1
    flags: i64 = load_i32(obj, 12)
    if (flags & (64 | 8192)) != 0:
        return -1
    tag: i64 = load_i32(obj, 8)
    if pcc_gc_backend4_relocate_copy_supported_tag(tag) == 0:
        return -1
    if (
        tag == abi_constant("object.type.thread")
        and ptr_is_null(load_ptr(obj, 16)) == 0
    ):
        return -1
    if tag == abi_constant("object.type.virtual_thread"):
        if load_i64(
            obj, abi_constant("object.virtual_thread.queued_offset")
        ) != 0:
            return -1
        if ptr_is_null(load_ptr(
            obj, abi_constant("object.virtual_thread.timer_entry_offset")
        )) == 0:
            return -1
        if ptr_is_null(load_ptr(
            obj, abi_constant("object.virtual_thread.io_entry_offset")
        )) == 0:
            return -1
        if ptr_is_null(load_ptr(
            obj, abi_constant("object.virtual_thread.join_waiters_offset")
        )) == 0:
            return -1
        if ptr_is_null(load_ptr(
            obj, abi_constant("object.virtual_thread.join_wait_tail_offset")
        )) == 0:
            return -1
        if ptr_is_null(load_ptr(
            obj, abi_constant("object.virtual_thread.join_entry_offset")
        )) == 0:
            return -1
        if ptr_is_null(load_ptr(
            obj, abi_constant("object.virtual_thread.channel_arm_a_offset")
        )) == 0:
            return -1
        if ptr_is_null(load_ptr(
            obj, abi_constant("object.virtual_thread.channel_arm_b_offset")
        )) == 0:
            return -1
        if load_i64(
            obj, abi_constant("object.virtual_thread.wait_kind_offset")
        ) != 0:
            return -1
    if tag == abi_constant("object.type.vthread_channel"):
        kind: i64 = load_i64(
            obj, abi_constant("object.vthread_channel.core.kind_offset")
        )
        if kind == 0:
            if ptr_is_null(load_ptr(
                obj,
                abi_constant("object.vthread_channel.core.send_head_offset"),
            )) == 0:
                return -1
            if ptr_is_null(load_ptr(
                obj,
                abi_constant("object.vthread_channel.core.send_tail_offset"),
            )) == 0:
                return -1
            if ptr_is_null(load_ptr(
                obj,
                abi_constant("object.vthread_channel.core.recv_head_offset"),
            )) == 0:
                return -1
            if ptr_is_null(load_ptr(
                obj,
                abi_constant("object.vthread_channel.core.recv_tail_offset"),
            )) == 0:
                return -1
            if load_i64(
                obj, abi_constant("object.vthread_channel.core.flags_offset")
            ) != 0:
                return -1
        elif kind != 1 and kind != 2:
            return -1
    size: i64 = pcc_gc_object_known_size(obj)
    large_page_accepted: i64 = 0
    if _backend4_evacuation_policy_accept(size) == 0 and allow_large_pages != 0:
        large_page_accepted = _backend4_large_page_evacuation_policy_accept(
            page, size
        )
    if _backend4_evacuation_policy_accept(size) == 0 and large_page_accepted == 0:
        if _backend4_evacuation_policy_defer_large(size) != 0:
            if (flags & 32768) == 0:
                store_i32(obj, 12, flags | 32768)
                deferred: i64 = load_i32(
                    global_addr("pcc_gc_backend4_large_object_defers"), 0
                )
                store_i32(
                    global_addr("pcc_gc_backend4_large_object_defers"),
                    0,
                    deferred + 1,
                )
                deferred_bytes: i64 = load_i32(
                    global_addr(
                        "pcc_gc_backend4_large_object_deferred_bytes_count"
                    ),
                    0,
                )
                store_i32(
                    global_addr(
                        "pcc_gc_backend4_large_object_deferred_bytes_count"
                    ),
                    0,
                    deferred_bytes + size,
                )
        return -1
    capacity: i64 = load_i64(page, 16)
    page_used: i64 = load_i64(page, 8)
    score: i64 = capacity - page_used
    if score < 0:
        score: i64 = 0
    score = score + load_i64(page, 40)
    score = score + load_i64(page, 48)
    score = score + pcc_gc_backend4_owner_remembered_slots(obj)
    if (flags & 256) != 0:
        score = score + 1
    if score <= 0:
        return -1
    return score


@c_abi_export("pcc_gc_relocation_selector_add_candidate_node")
def _backend4_add_candidate_node(node, allow_large_pages: i64) -> i64:
    if ptr_is_null(node) != 0:
        return 0
    obj = load_ptr(node, 0)
    page = load_ptr(node, 8)
    size: i64 = pcc_gc_object_known_size(obj)
    count_page: i64 = 0
    if ptr_is_null(pcc_gc_backend4_evacuation_page_find(page)) != 0:
        count_page: i64 = 1
    if pcc_gc_backend4_relocation_set_add(obj) == 0:
        return 0
    candidates: i64 = load_i32(
        global_addr("pcc_gc_backend4_evacuation_candidates"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidates"), 0, candidates + 1
    )
    if count_page != 0:
        if pcc_gc_backend4_evacuation_page_add(page) != 0:
            _backend4_note_page_candidate(size, page)
        else:
            _backend4_note_page_candidate(size, null())
    else:
        _backend4_note_page_candidate(size, null())
    return 1


@c_abi_export("pcc_gc_relocation_selector_select_page_objects")
def _backend4_select_page_objects(
    seed_node,
    budget: i64,
    allow_large_pages: i64,
) -> i64:
    if ptr_is_null(seed_node) != 0 or budget <= 0:
        return 0
    seed_page = load_ptr(seed_node, 8)
    seed_obj = load_ptr(seed_node, 0)
    if ptr_is_null(seed_page) != 0:
        return 0
    selected: i64 = 0
    pass_no: i64 = 0
    while pass_no < 2 and selected < budget:
        node = _zpage_head()
        while ptr_is_null(node) == 0 and selected < budget:
            obj = load_ptr(node, 0)
            same_seed: i64 = ptr_eq(obj, seed_obj)
            if ptr_eq(load_ptr(node, 8), seed_page) != 0:
                if (pass_no == 0 and same_seed != 0) or (
                    pass_no != 0 and same_seed == 0
                ):
                    if _backend4_zpage_candidate_score(node, allow_large_pages) > 0:
                        if _backend4_add_candidate_node(node, allow_large_pages) != 0:
                            selected = selected + 1
                            if (selected & 15) == 0:
                                pcc_thread_safepoint()
            node = load_ptr(node, 16)
        pass_no = pass_no + 1
    return selected


@c_abi_export("pcc_gc_select_relocation_set")
def pcc_gc_select_relocation_set(budget: i64) -> i64:
    backend: i64 = pcc_gc_config_ensure()
    if backend != 4 or budget <= 0:
        return 0
    pcc_py_gc_minor_graph_lock()
    selected: i64 = 0
    while selected < budget:
        best_node = null()
        best_score: i64 = -1
        node = _zpage_head()
        while ptr_is_null(node) == 0:
            score: i64 = _backend4_zpage_candidate_score(node, 0)
            if score > best_score:
                best_node = node
                best_score = score
            node = load_ptr(node, 16)
        if ptr_is_null(best_node) != 0:
            break
        added: i64 = _backend4_select_page_objects(
            best_node, budget - selected, 0
        )
        if added <= 0:
            break
        selected = selected + added
    pcc_py_gc_minor_graph_unlock()
    return selected


@c_abi_export("pcc_gc_backend4_select_relocation_pages")
def pcc_gc_backend4_select_relocation_pages(page_budget: i64) -> i64:
    if pcc_gc_backend() != 4 or page_budget <= 0:
        return 0
    pcc_py_gc_minor_graph_lock()
    selected: i64 = 0
    pages: i64 = 0
    while pages < page_budget:
        best_node = null()
        best_score: i64 = -1
        node = _zpage_head()
        while ptr_is_null(node) == 0:
            page = load_ptr(node, 8)
            score: i64 = _backend4_zpage_candidate_score(node, 1)
            if (
                score > best_score
                and ptr_is_null(pcc_gc_backend4_evacuation_page_find(page)) != 0
            ):
                best_node = node
                best_score = score
            node = load_ptr(node, 16)
        if ptr_is_null(best_node) != 0:
            break
        best_page = load_ptr(best_node, 8)
        object_budget: i64 = load_i64(best_page, 32)
        if object_budget < 1:
            object_budget: i64 = 1
        before_selected: i64 = selected
        added: i64 = _backend4_select_page_objects(best_node, object_budget, 1)
        if added <= 0:
            break
        selected = selected + added
        if selected > before_selected:
            pages = pages + 1
    pcc_py_gc_minor_graph_unlock()
    return selected
