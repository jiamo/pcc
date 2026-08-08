"""Shared write-barrier and five-backend step dispatch policy."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    null,
    ptr_diff,
    ptr_is_null,
    store_i32,
)


__pcc_freestanding__ = True


pcc_platform_monotonic_us = extern(
    "pcc_platform_monotonic_us", (), c_int64
)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_gc_incremental_concurrent_step = extern(
    "pcc_gc_incremental_concurrent_step", (c_int64,), c_int64
)
pcc_gc_generational_step = extern(
    "pcc_gc_generational_step", (c_int64, c_int64), c_int64
)
pcc_gc_backend4_step_remembered_roots = extern(
    "pcc_gc_backend4_step_remembered_roots", (c_int64,), c_int64
)
pcc_gc_backend4_step_generation_aging = extern(
    "pcc_gc_backend4_step_generation_aging", (c_int64,), c_int64
)
pcc_gc_backend4_evacuation_page_drain = extern(
    "pcc_gc_backend4_evacuation_page_drain", (c_int64,), c_int64
)
pcc_gc_backend4_select_relocation_pages = extern(
    "pcc_gc_backend4_select_relocation_pages", (c_int64,), c_int64
)
pcc_gc_tracing_has_sweep_candidate = extern(
    "pcc_gc_tracing_has_sweep_candidate", (), c_int64
)
pcc_gc_tracing_step_cycle = extern(
    "pcc_gc_tracing_step_cycle", (c_int64,), c_int64
)
pcc_gc_tracing_record_pause = extern(
    "pcc_gc_tracing_record_pause", (c_int64, c_int64), c_void
)
pcc_gc_backend4_remap_and_retire_unlocked = extern(
    "pcc_gc_backend4_remap_and_retire_unlocked", (), c_void
)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_py_gc_minor_graph_lock = extern(
    "pcc_py_gc_minor_graph_lock", (), c_void
)
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)
pcc_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock", (c_ptr,), c_int64
)
pcc_gc_pointer_is_managed = extern(
    "pcc_gc_pointer_is_managed", (c_ptr,), c_int64
)
pcc_gc_backend3_remember_owner = extern(
    "pcc_gc_backend3_remember_owner", (c_ptr, c_int64), c_void
)
pcc_gc_backend4_store_buffer_enqueue = extern(
    "pcc_gc_backend4_store_buffer_enqueue", (c_ptr, c_ptr, c_ptr), c_int64
)


@c_abi_export("pcc_gc_dispatch_selected_backend")
def _selected_backend() -> i64:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return pcc_gc_config_ensure()
    return load_i32(global_addr("pcc_gc_backend_selected"), 0)


@c_abi_export("pcc_gc_dispatch_ptr_can_have_header")
def _ptr_can_have_header(obj) -> i64:
    return pcc_gc_pointer_is_managed(obj)


@c_abi_export("pcc_gc_dispatch_tracing_work_pending")
def _tracing_work_pending() -> i64:
    if load_i32(global_addr("pcc_gc_cycle_requested"), 0) != 0:
        return 1
    if load_i32(global_addr("pcc_gc_mark_active"), 0) != 0:
        return 1
    return pcc_gc_tracing_has_sweep_candidate()


@c_abi_export("pcc_gc_step")
def pcc_gc_step(budget: i64) -> i64:
    backend: i64 = _selected_backend()
    if budget <= 0:
        return 0
    if backend == 1 or backend == 2:
        return pcc_gc_incremental_concurrent_step(budget)

    store_i32(
        global_addr("pcc_gc_metric_step"),
        0,
        load_i32(global_addr("pcc_gc_metric_step"), 0) + 1,
    )
    start_us: i64 = pcc_platform_monotonic_us()
    processed: i64 = 0

    if backend == 3:
        processed = processed + pcc_gc_generational_step(budget, 1)
        if (
            processed < budget
            and load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0
            and _tracing_work_pending() != 0
        ):
            processed = processed + pcc_gc_tracing_step_cycle(budget - processed)
    elif backend == 4:
        if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0:
            processed = processed + pcc_gc_tracing_step_cycle(budget - processed)
        else:
            processed = processed + pcc_gc_backend4_step_remembered_roots(
                budget - processed
            )
            if processed < budget:
                processed = processed + pcc_gc_backend4_step_generation_aging(
                    budget - processed
                )
            if processed < budget:
                processed = processed + pcc_gc_backend4_evacuation_page_drain(
                    budget - processed
                )
            if processed < budget:
                selected: i64 = pcc_gc_backend4_select_relocation_pages(
                    budget - processed
                )
                if selected > 0:
                    moved: i64 = pcc_gc_backend4_evacuation_page_drain(
                        budget - processed
                    )
                    if moved > 0:
                        processed = processed + moved
                    else:
                        processed = processed + selected
            if processed < budget and _tracing_work_pending() != 0:
                stw: i64 = pcc_stop_the_world()
                processed = processed + pcc_gc_tracing_step_cycle(
                    budget - processed
                )
                if stw == 0:
                    pcc_resume_world()
            if (
                processed == 0
                and load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0
            ):
                pcc_py_gc_minor_graph_lock()
                before: i64 = load_i32(
                    global_addr("pcc_gc_forwarding_population"), 0
                )
                if ptr_is_null(
                    global_load_ptr("pcc_gc_relocation_set_head")
                ) != 0:
                    pcc_gc_backend4_remap_and_retire_unlocked()
                after: i64 = load_i32(
                    global_addr("pcc_gc_forwarding_population"), 0
                )
                pcc_py_gc_minor_graph_unlock()
                if before > after:
                    processed = processed + (before - after)
                elif before > 0:
                    processed = processed + 1

    pcc_gc_tracing_record_pause(start_us, pcc_platform_monotonic_us())
    return processed


@c_abi_export("pcc_gc_note_slot_write_barrier")
def pcc_gc_note_slot_write_barrier(owner, slot, value) -> None:
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return
    backend: i64 = _selected_backend()

    if ptr_is_null(owner) != 0:
        if backend != 1 and backend != 2 and backend != 4:
            return
        pcc_py_gc_minor_graph_lock()
        if (
            pcc_gc_object_is_known_no_lock(value) != 0
            and load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
        ):
            value_flags: i64 = load_i32(value, 12)
            should_gray: i64 = 0
            if (value_flags & 8) != 0:
                should_gray: i64 = 1
            if backend == 2 and (value_flags & 16) == 0:
                should_gray: i64 = 1
            if should_gray != 0:
                store_i32(value, 12, (value_flags & ~56) | 16)
                store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
                if backend == 2:
                    store_i32(
                        global_addr("pcc_gc_cms_wb_flushes"),
                        0,
                        load_i32(global_addr("pcc_gc_cms_wb_flushes"), 0) + 1,
                    )
        pcc_py_gc_minor_graph_unlock()
        return

    if is_tagged_int(owner) != 0:
        return
    if backend == 1 or backend == 2:
        pcc_py_gc_minor_graph_lock()
        if (
            pcc_gc_object_is_known_no_lock(owner) != 0
            and pcc_gc_object_is_known_no_lock(value) != 0
        ):
            owner_flags: i64 = load_i32(owner, 12)
            value_flags = load_i32(value, 12)
            should_shade: i64 = 0
            if backend == 1 and (owner_flags & 32) != 0:
                should_shade: i64 = 1
            if (
                backend == 2
                and load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
            ):
                should_shade: i64 = 1
            should_gray_value: i64 = 0
            if (value_flags & 8) != 0:
                should_gray_value: i64 = 1
            if backend == 2 and (value_flags & 16) == 0:
                should_gray_value: i64 = 1
            if should_shade != 0 and should_gray_value != 0:
                store_i32(value, 12, (value_flags & ~56) | 16)
                store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
                if backend == 2:
                    store_i32(
                        global_addr("pcc_gc_cms_wb_flushes"),
                        0,
                        load_i32(global_addr("pcc_gc_cms_wb_flushes"), 0) + 1,
                    )
        pcc_py_gc_minor_graph_unlock()
        return

    if backend != 3 and backend != 4:
        return
    if _ptr_can_have_header(owner) == 0 or _ptr_can_have_header(value) == 0:
        return
    owner_flags = load_i32(owner, 12)
    value_flags = load_i32(value, 12)
    if (owner_flags & 256) == 0 or (value_flags & 128) == 0:
        return
    pcc_py_gc_minor_graph_lock()
    if (
        pcc_gc_object_is_known_no_lock(owner) != 0
        and pcc_gc_object_is_known_no_lock(value) != 0
    ):
        owner_flags = load_i32(owner, 12)
        value_flags = load_i32(value, 12)
        if (owner_flags & 256) != 0 and (value_flags & 128) != 0:
            if backend == 4:
                if pcc_gc_backend4_store_buffer_enqueue(owner, slot, value) != 0:
                    store_i32(
                        global_addr("pcc_gc_backend4_genzgc_store_barriers"),
                        0,
                        load_i32(
                            global_addr("pcc_gc_backend4_genzgc_store_barriers"),
                            0,
                        )
                        + 1,
                    )
            else:
                pcc_gc_backend3_remember_owner(owner, owner_flags)
    pcc_py_gc_minor_graph_unlock()


@c_abi_export("pcc_gc_note_write_barrier")
def pcc_gc_note_write_barrier(owner, value) -> None:
    pcc_gc_note_slot_write_barrier(owner, null(), value)
