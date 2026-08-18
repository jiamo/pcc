"""Backend 4 relocation candidate scoring and page-grouped selection."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    abi_constant,
    atomic_rmw_i32,
    define_global_i64,
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
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


# GC-P1-BACKEND4-FRESH-ALLOC-FILTER-DISAGREEMENT diagnosability counters.
# Mirrors py_gc_backend.c (which uses atomics); the selector runs under the
# graph lock on this arm, so plain load/store increments are ordered.
define_global_i64("pcc_gc_backend4_candidate_fresh_skips_g", 0)
define_global_i64("pcc_gc_backend4_relocation_add_refusals_g", 0)


@c_abi_export("pcc_gc_backend4_candidate_fresh_skips_count")
def _candidate_fresh_skips_count() -> i64:
    return load_i64(
        global_addr("pcc_gc_backend4_candidate_fresh_skips_g"), 0
    )


@c_abi_export("pcc_gc_backend4_relocation_add_refusals_count")
def _relocation_add_refusals_count() -> i64:
    return load_i64(
        global_addr("pcc_gc_backend4_relocation_add_refusals_g"), 0
    )


pcc_gc_backend4_relocate_copy_supported_tag = extern(
    "pcc_gc_backend4_relocate_copy_supported_tag", (c_int64,), c_int64
)
pcc_gc_backend4_zpage_clear_active_page = extern(
    "pcc_gc_backend4_zpage_clear_active_page", (c_ptr,), c_void
)
pcc_gc_forwarding_find = extern("pcc_gc_forwarding_find", (c_ptr,), c_ptr)
pcc_gc_forwarding_target_exists = extern(
    "pcc_gc_forwarding_target_exists", (c_ptr,), c_int64
)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)


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
    if load_i64(page, 88) > 0:
        return -1
    flags: i64 = load_i32(obj, 12)
    if (flags & (64 | 2048 | 8192 | 524288)) != 0:
        return -1
    if (flags & 16384) != 0:
        # FRESH_ALLOC: the add refuses half-initialized objects
        # unconditionally, so a fresh owner must be skipped here, counted,
        # instead of costing a full page walk that adds nothing
        # (GC-P1-BACKEND4-FRESH-ALLOC-FILTER-DISAGREEMENT).
        skips: i64 = load_i64(
            global_addr("pcc_gc_backend4_candidate_fresh_skips_g"), 0
        )
        store_i64(
            global_addr("pcc_gc_backend4_candidate_fresh_skips_g"),
            0,
            skips + 1,
        )
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
    size: i64 = load_i64(node, 32)
    large_page_accepted: i64 = 0
    if _backend4_evacuation_policy_accept(size) == 0 and allow_large_pages != 0:
        large_page_accepted = _backend4_large_page_evacuation_policy_accept(
            page, size
        )
    if _backend4_evacuation_policy_accept(size) == 0 and large_page_accepted == 0:
        if _backend4_evacuation_policy_defer_large(size) != 0:
            if (flags & 32768) == 0:
                atomic_rmw_i32("or", obj, 12, 32768, "acq_rel")
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
    score = score + load_i64(node, 72)
    if (flags & 256) != 0:
        score = score + 1
    if score <= 0:
        return -1
    return score


@c_abi_export("pcc_gc_relocation_selector_add_candidate_node")
def _backend4_add_candidate_node(node, allow_large_pages: i64, plan) -> i64:
    if ptr_is_null(node) != 0:
        return 0
    obj = load_ptr(node, 0)
    page = load_ptr(node, 8)
    size: i64 = load_i64(node, 32)
    if ptr_is_null(plan) != 0:
        return 0
    if load_i64(
        global_addr("pcc_gc_backend4_relocation_reset_owner"), 0
    ) != 0:
        return 0
    if load_i64(
        global_addr("pcc_gc_backend4_reseed_commit_owner"), 0
    ) != 0:
        return 0

    relocation_node = load_ptr(plan, 0)
    if ptr_is_null(relocation_node) != 0:
        return 0
    flags: i64 = load_i32(obj, 12)
    if (flags & (64 | 2048 | 8192 | 16384 | 524288)) != 0:
        # Mirror the ported pcc_gc_backend4_relocation_set_add exactly:
        # this inline add previously omitted PINNED (64) and FRESH_ALLOC
        # (16384), so the selector path could admit a pinned or
        # half-initialized object that the exported add refuses.  Counted:
        # a snapshot-approved candidate refused here is the diagnosable
        # form of the silent select()==0.
        refusals: i64 = load_i64(
            global_addr("pcc_gc_backend4_relocation_add_refusals_g"), 0
        )
        store_i64(
            global_addr("pcc_gc_backend4_relocation_add_refusals_g"),
            0,
            refusals + 1,
        )
        return 0
    if ptr_is_null(pcc_gc_forwarding_find(obj)) == 0:
        return 0
    if pcc_gc_forwarding_target_exists(obj) != 0:
        return 0
    count_page: i64 = 0
    if load_i32(page, 108) == 0:
        count_page = 1
        if ptr_is_null(load_ptr(plan, 8)) != 0:
            return 0

    store_ptr(plan, 0, load_ptr(relocation_node, 8))
    store_ptr(relocation_node, 0, obj)
    store_ptr(
        relocation_node,
        8,
        global_load_ptr("pcc_gc_relocation_set_head"),
    )
    global_store_ptr("pcc_gc_relocation_set_head", relocation_node)
    relocation_revision: i64 = load_i64(
        global_addr("pcc_gc_backend4_reseed_relocation_revision"), 0
    )
    store_i64(
        global_addr("pcc_gc_backend4_reseed_relocation_revision"),
        0,
        relocation_revision + 1,
    )
    atomic_rmw_i32("or", obj, 12, 2048, "acq_rel")

    candidates: i64 = load_i32(
        global_addr("pcc_gc_backend4_evacuation_candidates"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidates"), 0, candidates + 1
    )
    if count_page != 0:
        page_node = load_ptr(plan, 8)
        store_ptr(plan, 8, load_ptr(page_node, 8))
        pcc_gc_backend4_zpage_clear_active_page(page)
        store_ptr(page_node, 0, page)
        store_ptr(
            page_node,
            8,
            global_load_ptr("pcc_gc_backend4_evacuation_page_head"),
        )
        global_store_ptr("pcc_gc_backend4_evacuation_page_head", page_node)
        store_i32(page, 108, 1)
        revision: i64 = load_i64(
            global_addr("pcc_gc_backend4_reseed_page_revision"), 0
        )
        store_i64(
            global_addr("pcc_gc_backend4_reseed_page_revision"),
            0,
            revision + 1,
        )
        _backend4_note_page_candidate(size, page)
    else:
        _backend4_note_page_candidate(size, null())
    return 1


@c_abi_export("pcc_gc_relocation_selector_select_page_objects")
def _backend4_select_page_objects(
    seed_node,
    budget: i64,
    allow_large_pages: i64,
    plan,
) -> i64:
    if ptr_is_null(seed_node) != 0 or budget <= 0:
        return 0
    seed_page = load_ptr(seed_node, 8)
    seed_obj = load_ptr(seed_node, 0)
    if ptr_is_null(seed_page) != 0:
        return 0
    selected: i64 = 0
    examined: i64 = 1
    if _backend4_zpage_candidate_score(seed_node, allow_large_pages) > 0:
        if _backend4_add_candidate_node(
            seed_node, allow_large_pages, plan
        ) != 0:
            selected = selected + 1
    node = load_ptr(seed_page, 112)
    while (
        ptr_is_null(node) == 0
        and selected < budget
        and examined < 16
    ):
        current = node
        node = load_ptr(node, 48)
        examined = examined + 1
        if ptr_eq(load_ptr(current, 0), seed_obj) != 0:
            continue
        if _backend4_zpage_candidate_score(current, allow_large_pages) > 0:
            if _backend4_add_candidate_node(
                current, allow_large_pages, plan
            ) != 0:
                selected = selected + 1
    return selected


@c_abi_export("pcc_gc_relocation_selector_scan_reset")
def _selector_scan_reset() -> None:
    global_store_ptr("pcc_gc_backend4_selector_scan_cursor", null())
    global_store_ptr("pcc_gc_backend4_selector_scan_best", null())
    global_store_ptr("pcc_gc_backend4_selector_scan_page", null())
    store_i64(global_addr("pcc_gc_backend4_selector_scan_owner"), 0, 0)
    store_i64(
        global_addr("pcc_gc_backend4_selector_scan_best_score"), 0, -1
    )
    store_i32(
        global_addr("pcc_gc_backend4_selector_scan_allow_large"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_selector_scan_require_unselected"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_selector_scan_restart"), 0, 0
    )


@c_abi_export("pcc_gc_relocation_selector_best_page_batch")
def _best_relocation_page_batch(
    owner_thread_id: i64,
    page_token,
    require_unselected_page: i64,
    allow_large_pages: i64,
    result,
) -> i64:
    # result: best node @0, examined @8, complete @16.
    store_ptr(result, 0, null())
    store_i64(result, 8, 0)
    store_i64(result, 16, 0)
    if owner_thread_id <= 0:
        store_i64(result, 16, 1)
        return -1
    scan_owner: i64 = load_i64(
        global_addr("pcc_gc_backend4_selector_scan_owner"), 0
    )
    if scan_owner == 0:
        store_i64(
            global_addr("pcc_gc_backend4_selector_scan_owner"),
            0,
            owner_thread_id,
        )
        global_store_ptr("pcc_gc_backend4_selector_scan_page", page_token)
        store_i32(
            global_addr("pcc_gc_backend4_selector_scan_allow_large"),
            0,
            allow_large_pages,
        )
        store_i32(
            global_addr(
                "pcc_gc_backend4_selector_scan_require_unselected"
            ),
            0,
            require_unselected_page,
        )
        global_store_ptr("pcc_gc_backend4_selector_scan_cursor", _zpage_head())
        global_store_ptr("pcc_gc_backend4_selector_scan_best", null())
        store_i64(
            global_addr("pcc_gc_backend4_selector_scan_best_score"), 0, -1
        )
        store_i32(
            global_addr("pcc_gc_backend4_selector_scan_restart"), 0, 0
        )
    elif (
        scan_owner != owner_thread_id
        or ptr_eq(
            global_load_ptr("pcc_gc_backend4_selector_scan_page"),
            page_token,
        ) == 0
        or load_i32(
            global_addr("pcc_gc_backend4_selector_scan_allow_large"), 0
        ) != allow_large_pages
        or load_i32(
            global_addr(
                "pcc_gc_backend4_selector_scan_require_unselected"
            ),
            0,
        ) != require_unselected_page
    ):
        store_i64(result, 16, 1)
        return -1
    if load_i32(
        global_addr("pcc_gc_backend4_selector_scan_restart"), 0
    ) != 0:
        global_store_ptr("pcc_gc_backend4_selector_scan_cursor", _zpage_head())
        global_store_ptr("pcc_gc_backend4_selector_scan_best", null())
        store_i64(
            global_addr("pcc_gc_backend4_selector_scan_best_score"), 0, -1
        )
        store_i32(
            global_addr("pcc_gc_backend4_selector_scan_restart"), 0, 0
        )

    examined: i64 = 0
    cursor = global_load_ptr("pcc_gc_backend4_selector_scan_cursor")
    while ptr_is_null(cursor) == 0 and examined < 16:
        node = cursor
        cursor = load_ptr(node, 16)
        global_store_ptr("pcc_gc_backend4_selector_scan_cursor", cursor)
        examined = examined + 1
        if (
            ptr_is_null(page_token) == 0
            and ptr_eq(load_ptr(node, 8), page_token) == 0
        ):
            continue
        score: i64 = _backend4_zpage_candidate_score(
            node, allow_large_pages
        )
        page = load_ptr(node, 8)
        if (
            score <= 0
            or (
                require_unselected_page != 0
                and load_i32(page, 108) != 0
            )
        ):
            continue
        best_score: i64 = load_i64(
            global_addr("pcc_gc_backend4_selector_scan_best_score"), 0
        )
        if score > best_score:
            global_store_ptr("pcc_gc_backend4_selector_scan_best", node)
            store_i64(
                global_addr("pcc_gc_backend4_selector_scan_best_score"),
                0,
                score,
            )
    store_i64(result, 8, examined)
    if ptr_is_null(cursor) == 0:
        return 0

    has_best: i64 = 0
    best_node = global_load_ptr("pcc_gc_backend4_selector_scan_best")
    if ptr_is_null(best_node) == 0:
        best_page = load_ptr(best_node, 8)
        score = _backend4_zpage_candidate_score(
            best_node, allow_large_pages
        )
        if (
            score > 0
            and (
                ptr_is_null(page_token) != 0
                or ptr_eq(best_page, page_token) != 0
            )
            and (
                require_unselected_page == 0
                or load_i32(best_page, 108) == 0
            )
        ):
            store_ptr(result, 0, best_node)
            has_best = 1
    _selector_scan_reset()
    store_i64(result, 16, 1)
    return has_best


@c_abi_export("pcc_gc_relocation_selector_page_scan_reset")
def _selector_page_scan_reset() -> None:
    global_store_ptr("pcc_gc_backend4_selector_page_cursor", null())
    global_store_ptr("pcc_gc_backend4_selector_page_seed", null())
    global_store_ptr("pcc_gc_backend4_selector_page", null())
    store_i64(global_addr("pcc_gc_backend4_selector_page_owner"), 0, 0)
    store_i32(
        global_addr("pcc_gc_backend4_selector_page_allow_large"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_selector_page_seed_pending"), 0, 0
    )


@c_abi_export("pcc_gc_relocation_selector_page_scan_begin")
def _selector_page_scan_begin(
    owner_thread_id: i64, seed_node, allow_large_pages: i64
) -> i64:
    if (
        owner_thread_id <= 0
        or ptr_is_null(seed_node) != 0
        or load_i64(
            global_addr("pcc_gc_backend4_selector_page_owner"), 0
        ) != 0
    ):
        return 0
    page = load_ptr(seed_node, 8)
    if ptr_is_null(page) != 0:
        return 0
    store_i64(
        global_addr("pcc_gc_backend4_selector_page_owner"),
        0,
        owner_thread_id,
    )
    global_store_ptr("pcc_gc_backend4_selector_page", page)
    global_store_ptr("pcc_gc_backend4_selector_page_seed", seed_node)
    global_store_ptr(
        "pcc_gc_backend4_selector_page_cursor", load_ptr(page, 112)
    )
    store_i32(
        global_addr("pcc_gc_backend4_selector_page_allow_large"),
        0,
        allow_large_pages,
    )
    store_i32(
        global_addr("pcc_gc_backend4_selector_page_seed_pending"), 0, 1
    )
    return 1


@c_abi_export("pcc_gc_relocation_selector_select_page_objects_batch")
def _select_page_objects_batch(
    owner_thread_id: i64,
    seed_node,
    budget: i64,
    allow_large_pages: i64,
    plan,
    result,
) -> i64:
    # result: examined @0, complete @8.
    store_i64(result, 0, 0)
    store_i64(result, 8, 0)
    if budget <= 0:
        store_i64(result, 8, 1)
        return -1
    page_owner: i64 = load_i64(
        global_addr("pcc_gc_backend4_selector_page_owner"), 0
    )
    if page_owner == 0:
        if _selector_page_scan_begin(
            owner_thread_id, seed_node, allow_large_pages
        ) == 0:
            store_i64(result, 8, 1)
            return -1
    elif (
        page_owner != owner_thread_id
        or load_i32(
            global_addr("pcc_gc_backend4_selector_page_allow_large"), 0
        ) != allow_large_pages
        or (
            ptr_is_null(seed_node) == 0
            and ptr_eq(
                load_ptr(seed_node, 8),
                global_load_ptr("pcc_gc_backend4_selector_page"),
            ) == 0
        )
    ):
        store_i64(result, 8, 1)
        return -1

    examined: i64 = 0
    selected: i64 = 0
    seed_pending: i64 = load_i32(
        global_addr("pcc_gc_backend4_selector_page_seed_pending"), 0
    )
    if seed_pending != 0 and examined < 16 and selected < budget:
        seed = global_load_ptr("pcc_gc_backend4_selector_page_seed")
        store_i32(
            global_addr("pcc_gc_backend4_selector_page_seed_pending"), 0, 0
        )
        examined = examined + 1
        if (
            ptr_is_null(seed) == 0
            and _backend4_zpage_candidate_score(
                seed, allow_large_pages
            ) > 0
            and _backend4_add_candidate_node(
                seed, allow_large_pages, plan
            ) != 0
        ):
            selected = selected + 1
    cursor = global_load_ptr("pcc_gc_backend4_selector_page_cursor")
    while (
        ptr_is_null(cursor) == 0
        and examined < 16
        and selected < budget
    ):
        node = cursor
        cursor = load_ptr(node, 48)
        global_store_ptr("pcc_gc_backend4_selector_page_cursor", cursor)
        examined = examined + 1
        if ptr_eq(
            node, global_load_ptr("pcc_gc_backend4_selector_page_seed")
        ) != 0:
            continue
        if (
            _backend4_zpage_candidate_score(node, allow_large_pages) > 0
            and _backend4_add_candidate_node(
                node, allow_large_pages, plan
            ) != 0
        ):
            selected = selected + 1
    store_i64(result, 0, examined)
    if (
        selected >= budget
        or (
            load_i32(
                global_addr("pcc_gc_backend4_selector_page_seed_pending"), 0
            ) == 0
            and ptr_is_null(cursor) != 0
        )
    ):
        _selector_page_scan_reset()
        store_i64(result, 8, 1)
    return selected


@c_abi_export("pcc_gc_select_relocation_set")
def pcc_gc_select_relocation_set(budget: i64) -> i64:
    backend: i64 = pcc_gc_config_ensure()
    if backend != 4 or budget <= 0:
        return 0
    owner_thread_id: i64 = pcc_current_thread_id()
    if owner_thread_id <= 0:
        return 0
    selected: i64 = 0
    while selected < budget:
        batch_budget: i64 = budget - selected
        if batch_budget > 16:
            batch_budget: i64 = 16
        plan = stack_alloc(16)
        store_ptr(plan, 0, null())
        store_ptr(plan, 8, null())
        allocated: i64 = 0
        while allocated < batch_budget:
            relocation_node = malloc(16)
            if ptr_is_null(relocation_node) != 0:
                break
            page_node = malloc(16)
            if ptr_is_null(page_node) != 0:
                free(relocation_node)
                break
            store_ptr(relocation_node, 8, load_ptr(plan, 0))
            store_ptr(plan, 0, relocation_node)
            store_ptr(page_node, 8, load_ptr(plan, 8))
            store_ptr(plan, 8, page_node)
            allocated = allocated + 1
        batch_budget = allocated
        if batch_budget <= 0:
            break
        added: i64 = 0
        scan_complete: i64 = 0
        page_commit_complete: i64 = 1
        scan_result = stack_alloc(24)
        while scan_complete == 0:
            pcc_py_gc_minor_graph_lock()
            has_best: i64 = _best_relocation_page_batch(
                owner_thread_id, null(), 0, 0, scan_result
            )
            scan_complete = load_i64(scan_result, 16)
            if scan_complete != 0 and has_best > 0:
                page_commit_complete = 0
                if _selector_page_scan_begin(
                    owner_thread_id, load_ptr(scan_result, 0), 0
                ) == 0:
                    page_commit_complete = 1
            pcc_py_gc_minor_graph_unlock()
            if (
                (scan_complete == 0 or page_commit_complete == 0)
                and load_i64(scan_result, 8) > 0
            ):
                pcc_thread_safepoint()
        page_result = stack_alloc(16)
        while page_commit_complete == 0:
            pcc_py_gc_minor_graph_lock()
            page_added: i64 = _select_page_objects_batch(
                owner_thread_id,
                null(),
                batch_budget - added,
                0,
                plan,
                page_result,
            )
            pcc_py_gc_minor_graph_unlock()
            if page_added < 0:
                added = page_added
                break
            added = added + page_added
            page_commit_complete = load_i64(page_result, 8)
            if (
                page_commit_complete == 0
                and load_i64(page_result, 0) > 0
            ):
                pcc_thread_safepoint()
        unused = load_ptr(plan, 0)
        while ptr_is_null(unused) == 0:
            nxt = load_ptr(unused, 8)
            free(unused)
            unused = nxt
        unused = load_ptr(plan, 8)
        while ptr_is_null(unused) == 0:
            nxt = load_ptr(unused, 8)
            free(unused)
            unused = nxt
        if added <= 0:
            break
        selected = selected + added
        pcc_thread_safepoint()
    return selected


@c_abi_export("pcc_gc_backend4_select_relocation_pages")
def pcc_gc_backend4_select_relocation_pages(page_budget: i64) -> i64:
    if pcc_gc_config_ensure() != 4 or page_budget <= 0:
        return 0
    owner_thread_id: i64 = pcc_current_thread_id()
    if owner_thread_id <= 0:
        return 0
    selected: i64 = 0
    pages: i64 = 0
    while pages < page_budget:
        page_token = null()
        object_budget: i64 = 0
        preflight_result = stack_alloc(24)
        preflight_complete: i64 = 0
        while preflight_complete == 0:
            pcc_py_gc_minor_graph_lock()
            has_preflight: i64 = _best_relocation_page_batch(
                owner_thread_id, null(), 1, 1, preflight_result
            )
            preflight_complete = load_i64(preflight_result, 16)
            if preflight_complete != 0 and has_preflight > 0:
                page_token = load_ptr(load_ptr(preflight_result, 0), 8)
                object_budget = load_i64(page_token, 32)
                if object_budget < 1:
                    object_budget = 1
            pcc_py_gc_minor_graph_unlock()
            if (
                preflight_complete == 0
                and load_i64(preflight_result, 8) > 0
            ):
                pcc_thread_safepoint()
        if ptr_is_null(page_token) != 0 or object_budget <= 0:
            break

        plan = stack_alloc(16)
        store_ptr(plan, 0, null())
        store_ptr(plan, 8, null())
        page_node = malloc(16)
        if ptr_is_null(page_node) != 0:
            break
        store_ptr(page_node, 8, null())
        store_ptr(plan, 8, page_node)
        allocated: i64 = 0
        while allocated < object_budget:
            relocation_node = malloc(16)
            if ptr_is_null(relocation_node) != 0:
                break
            store_ptr(relocation_node, 8, load_ptr(plan, 0))
            store_ptr(plan, 0, relocation_node)
            allocated = allocated + 1
        if allocated != object_budget:
            unused = load_ptr(plan, 0)
            while ptr_is_null(unused) == 0:
                nxt = load_ptr(unused, 8)
                free(unused)
                unused = nxt
            free(load_ptr(plan, 8))
            break

        page_selected: i64 = 0
        retry_preflight: i64 = 0
        while page_selected < object_budget:
            batch_budget: i64 = object_budget - page_selected
            if batch_budget > 16:
                batch_budget = 16
            added: i64 = 0
            current_complete: i64 = 0
            page_commit_complete: i64 = 1
            current_result = stack_alloc(24)
            while current_complete == 0:
                pcc_py_gc_minor_graph_lock()
                has_current: i64 = _best_relocation_page_batch(
                    owner_thread_id,
                    page_token,
                    0,
                    1,
                    current_result,
                )
                current_complete = load_i64(current_result, 16)
                if current_complete != 0 and has_current > 0:
                    if (
                        page_selected == 0
                        and load_i64(page_token, 32) > object_budget
                    ):
                        retry_preflight = 1
                    else:
                        page_commit_complete = 0
                        if _selector_page_scan_begin(
                            owner_thread_id,
                            load_ptr(current_result, 0),
                            1,
                        ) == 0:
                            page_commit_complete = 1
                pcc_py_gc_minor_graph_unlock()
                if (
                    (
                        current_complete == 0
                        or page_commit_complete == 0
                    )
                    and load_i64(current_result, 8) > 0
                ):
                    pcc_thread_safepoint()
            page_result = stack_alloc(16)
            while retry_preflight == 0 and page_commit_complete == 0:
                pcc_py_gc_minor_graph_lock()
                page_added: i64 = _select_page_objects_batch(
                    owner_thread_id,
                    null(),
                    batch_budget - added,
                    1,
                    plan,
                    page_result,
                )
                pcc_py_gc_minor_graph_unlock()
                if page_added < 0:
                    added = page_added
                    break
                added = added + page_added
                page_commit_complete = load_i64(page_result, 8)
                if (
                    page_commit_complete == 0
                    and load_i64(page_result, 0) > 0
                ):
                    pcc_thread_safepoint()
            if retry_preflight != 0 or added <= 0:
                break
            page_selected = page_selected + added
            selected = selected + added
            pcc_thread_safepoint()

        unused = load_ptr(plan, 0)
        while ptr_is_null(unused) == 0:
            nxt = load_ptr(unused, 8)
            free(unused)
            unused = nxt
        unused = load_ptr(plan, 8)
        while ptr_is_null(unused) == 0:
            nxt = load_ptr(unused, 8)
            free(unused)
            unused = nxt
        if retry_preflight != 0:
            continue
        if page_selected <= 0:
            break
        pages = pages + 1
    return selected
