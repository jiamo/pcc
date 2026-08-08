"""Raw incremental/concurrent tracing scheduler and allocation pacer."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i32,
    atomic_rmw_i32,
    atomic_store_i32,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    store_i32,
    unsigned_div_i64,
)


__pcc_freestanding__ = True


pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_thread_join = extern("pcc_thread_join", (c_ptr, c_ptr), c_int64)
pcc_platform_monotonic_us = extern("pcc_platform_monotonic_us", (), c_int64)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_gc_maybe_start_cms_worker = extern(
    "pcc_gc_maybe_start_cms_worker", (), c_void
)
pcc_py_gc_minor_graph_lock = extern(
    "pcc_py_gc_minor_graph_lock", (), c_void
)
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)
pcc_gc_begin_mark_cycle = extern("pcc_gc_begin_mark_cycle", (), c_void)
pcc_gc_finish_tracing_cycle = extern(
    "pcc_gc_finish_tracing_cycle", (), c_int64
)
pcc_gc_trace_referents = extern("pcc_gc_trace_referents", (c_ptr,), c_void)
pcc_gc_gray_count_load_acquire = extern(
    "pcc_gc_gray_count_load_acquire", (), c_int64
)
pcc_gc_gray_count_decrement_acq_rel = extern(
    "pcc_gc_gray_count_decrement_acq_rel", (), c_void
)
pcc_gc_gray_count_store_release = extern(
    "pcc_gc_gray_count_store_release", (c_int64,), c_void
)


@c_abi_export("pcc_gc_tracing_debt_threshold")
def pcc_gc_tracing_debt_threshold() -> i64:
    override: i64 = load_i32(global_addr("pcc_gc_debt_threshold_override"), 0)
    if override > 0:
        return override
    threshold: i64 = 65536
    live: i64 = load_i32(global_addr("pcc_gc_live_bytes"), 0)
    pause: i64 = load_i32(global_addr("pcc_gc_pause"), 0)
    if live > 0 and pause > 100:
        live_pause: i64 = unsigned_div_i64(live * (pause - 100), 100)
        if live_pause > threshold:
            threshold = live_pause
    return threshold


@c_abi_export("pcc_gc_tracing_budget_from_debt")
def pcc_gc_tracing_budget_from_debt() -> i64:
    debt: i64 = load_i32(global_addr("pcc_gc_debt_bytes"), 0)
    stepmul: i64 = load_i32(global_addr("pcc_gc_stepmul"), 0)
    budget: i64 = unsigned_div_i64(
        unsigned_div_i64(debt, 64) * stepmul, 100
    )
    if budget < 1:
        budget: i64 = 1
    if budget > 65536:
        budget: i64 = 65536
    return budget


@c_abi_export("pcc_gc_tracing_discharge_debt")
def pcc_gc_tracing_discharge_debt(processed: i64) -> None:
    if processed <= 0:
        return
    debt: i64 = load_i32(global_addr("pcc_gc_debt_bytes"), 0)
    stepmul: i64 = load_i32(global_addr("pcc_gc_stepmul"), 0)
    credit: i64 = unsigned_div_i64(processed * 64 * stepmul, 100)
    if credit < 64:
        credit: i64 = 64
    if credit >= debt:
        store_i32(global_addr("pcc_gc_debt_bytes"), 0, 0)
    else:
        store_i32(global_addr("pcc_gc_debt_bytes"), 0, debt - credit)


@c_abi_export("pcc_gc_tracing_record_pause")
def pcc_gc_tracing_record_pause(start_us: i64, end_us: i64) -> None:
    if start_us <= 0:
        return
    if end_us < start_us:
        return
    pause: i64 = end_us - start_us
    if pause <= 0:
        pause: i64 = 1
    store_i32(
        global_addr("pcc_gc_metric_pause_count"),
        0,
        load_i32(global_addr("pcc_gc_metric_pause_count"), 0) + 1,
    )
    store_i32(
        global_addr("pcc_gc_metric_pause_sum_us"),
        0,
        load_i32(global_addr("pcc_gc_metric_pause_sum_us"), 0) + pause,
    )
    if pause < 100:
        store_i32(
            global_addr("pcc_gc_metric_pause_hist0"),
            0,
            load_i32(global_addr("pcc_gc_metric_pause_hist0"), 0) + 1,
        )
    elif pause < 1000:
        store_i32(
            global_addr("pcc_gc_metric_pause_hist1"),
            0,
            load_i32(global_addr("pcc_gc_metric_pause_hist1"), 0) + 1,
        )
    elif pause < 10000:
        store_i32(
            global_addr("pcc_gc_metric_pause_hist2"),
            0,
            load_i32(global_addr("pcc_gc_metric_pause_hist2"), 0) + 1,
        )
    else:
        store_i32(
            global_addr("pcc_gc_metric_pause_hist3"),
            0,
            load_i32(global_addr("pcc_gc_metric_pause_hist3"), 0) + 1,
        )
    current: i64 = load_i32(global_addr("pcc_gc_metric_max_pause_us"), 0)
    if pause > current:
        store_i32(global_addr("pcc_gc_metric_max_pause_us"), 0, pause)


@c_abi_export("pcc_gc_record_explicit_pause")
def pcc_gc_record_explicit_pause(start_us: i64, end_us: i64) -> None:
    pcc_gc_tracing_record_pause(start_us, end_us)


@c_abi_export("pcc_gc_tracing_step_cycle")
def pcc_gc_tracing_step_cycle(remaining_budget: i64) -> i64:
    if remaining_budget <= 0:
        return 0

    pcc_py_gc_minor_graph_lock()
    local_processed: i64 = 0
    active: i64 = load_i32(global_addr("pcc_gc_mark_active"), 0)
    requested: i64 = load_i32(global_addr("pcc_gc_cycle_requested"), 0)
    if active == 0:
        if requested == 0:
            pcc_py_gc_minor_graph_unlock()
            return local_processed
        if pcc_threads_enabled() != 0:
            if load_i32(global_addr("pcc_gc_in_auto_step"), 0) != 0:
                pcc_py_gc_minor_graph_unlock()
                return local_processed
        pcc_gc_begin_mark_cycle()

    node = global_load_ptr("pcc_gc_trace_cursor")
    if ptr_is_null(node) != 0:
        node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0 and local_processed < remaining_budget:
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
        node = nxt
    global_store_ptr("pcc_gc_trace_cursor", node)

    if ptr_is_null(global_load_ptr("pcc_gc_trace_cursor")) != 0:
        if pcc_gc_gray_count_load_acquire() != 0:
            global_store_ptr("pcc_gc_trace_cursor", global_load_ptr("pcc_gc_object_head"))
        else:
            if pcc_gc_finish_tracing_cycle() != 0:
                global_store_ptr("pcc_gc_trace_cursor", null())
                pcc_gc_gray_count_store_release(0)
                store_i32(global_addr("pcc_gc_mark_active"), 0, 0)
                store_i32(global_addr("pcc_gc_cycle_requested"), 0, 0)

    pcc_py_gc_minor_graph_unlock()
    return local_processed


@c_abi_export("pcc_gc_incremental_concurrent_step")
def pcc_gc_incremental_concurrent_step(budget: i64) -> i64:
    if budget <= 0:
        return 0
    steps: i64 = load_i32(global_addr("pcc_gc_metric_step"), 0)
    store_i32(global_addr("pcc_gc_metric_step"), 0, steps + 1)
    start_us: i64 = pcc_platform_monotonic_us()
    processed: i64 = pcc_gc_tracing_step_cycle(budget)
    pcc_gc_tracing_discharge_debt(processed)
    if load_i32(global_addr("pcc_gc_mark_active"), 0) == 0:
        if load_i32(global_addr("pcc_gc_cycle_requested"), 0) == 0:
            store_i32(global_addr("pcc_gc_debt_bytes"), 0, 0)
    pcc_gc_tracing_record_pause(start_us, pcc_platform_monotonic_us())
    return processed


@c_abi_export("pcc_gc_incremental_maybe_auto_step")
def pcc_gc_incremental_maybe_auto_step() -> None:
    if load_i32(global_addr("pcc_gc_in_auto_step"), 0) != 0:
        return
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 1:
        return
    if pcc_threads_enabled() != 0:
        return
    debt: i64 = load_i32(global_addr("pcc_gc_debt_bytes"), 0)
    if debt < pcc_gc_tracing_debt_threshold():
        return
    store_i32(global_addr("pcc_gc_in_auto_step"), 0, 1)
    pcc_gc_incremental_concurrent_step(pcc_gc_tracing_budget_from_debt())
    store_i32(global_addr("pcc_gc_in_auto_step"), 0, 0)


@c_abi_export("pcc_gc_cms_stop_worker")
def pcc_gc_cms_stop_worker() -> None:
    handle = global_load_ptr("pcc_gc_cms_worker_handle")
    if (
        atomic_load_i32(
            global_addr("pcc_gc_cms_worker_started"), 0, "acquire"
        ) == 0
        or ptr_is_null(handle) != 0
    ):
        return
    atomic_store_i32(
        global_addr("pcc_gc_cms_worker_stop_requested"), 0, 1, "release"
    )
    pcc_thread_join(handle, null())
    global_store_ptr("pcc_gc_cms_worker_handle", null())
    atomic_store_i32(
        global_addr("pcc_gc_cms_worker_started"), 0, 0, "release"
    )
    atomic_store_i32(
        global_addr("pcc_gc_cms_worker_stop_requested"), 0, 0, "release"
    )


@c_abi_export("pcc_gc_cms_note_alloc")
def pcc_gc_cms_note_alloc(bytes: i64) -> None:
    if bytes <= 0:
        bytes: i64 = 1
    atomic_rmw_i32(
        "add", global_addr("pcc_gc_cms_queue_pushes"), 0, 1, "release"
    )
    debt: i64 = load_i32(global_addr("pcc_gc_debt_bytes"), 0) + bytes
    store_i32(global_addr("pcc_gc_debt_bytes"), 0, debt)
    if debt >= pcc_gc_tracing_debt_threshold():
        assists: i64 = load_i32(global_addr("pcc_gc_cms_mutator_assists"), 0)
        store_i32(global_addr("pcc_gc_cms_mutator_assists"), 0, assists + 1)
        store_i32(global_addr("pcc_gc_in_auto_step"), 0, 1)
        pcc_gc_incremental_concurrent_step(pcc_gc_tracing_budget_from_debt())
        store_i32(global_addr("pcc_gc_in_auto_step"), 0, 0)


@c_abi_export("pcc_gc_note_alloc")
def pcc_gc_note_alloc(bytes: i64) -> None:
    backend: i64 = pcc_gc_config_ensure()
    if bytes < 0:
        bytes: i64 = 0
    allocations: i64 = load_i32(global_addr("pcc_gc_metric_alloc"), 0)
    store_i32(global_addr("pcc_gc_metric_alloc"), 0, allocations + 1)
    if backend == 1:
        debt: i64 = load_i32(global_addr("pcc_gc_debt_bytes"), 0) + bytes
        store_i32(global_addr("pcc_gc_debt_bytes"), 0, debt)
        pcc_gc_incremental_maybe_auto_step()
    elif backend == 2:
        pcc_gc_maybe_start_cms_worker()
        pcc_gc_cms_note_alloc(bytes)
