"""Raw GC configuration and public tracing-collection entrypoints."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import cstr, global_addr, load_i8, load_i32, ptr_is_null, store_i32
from pcc.unsafe import (
    atomic_cas_i32,
    atomic_load_i32,
    atomic_rmw_i32,
    atomic_store_i32,
    function_addr,
    null,
)


__pcc_freestanding__ = True


pcc_platform_getenv = extern("pcc_platform_getenv", (c_ptr,), c_ptr)
pcc_platform_write = extern(
    "pcc_platform_write", (c_int64, c_ptr, c_int64), c_int64
)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_thread_start = extern("pcc_thread_start", (c_ptr, c_ptr, c_ptr), c_int64)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
pcc_platform_sleep_ns = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)
pcc_gc_tracing_budget_from_debt = extern(
    "pcc_gc_tracing_budget_from_debt", (), c_int64
)
pcc_gc_tracing_step_cycle = extern(
    "pcc_gc_tracing_step_cycle", (c_int64,), c_int64
)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_gc_tracing_has_sweep_candidate = extern(
    "pcc_gc_tracing_has_sweep_candidate", (), c_int64
)
pcc_gc_tracing_sweep_unreachable = extern(
    "pcc_gc_tracing_sweep_unreachable", (c_int64,), c_int64
)


@c_abi_export("pcc_gc_cms_worker_main_py")
def pcc_gc_cms_worker_main_py(arg):
    while atomic_load_i32(
        global_addr("pcc_gc_cms_worker_stop_requested"), 0, "acquire"
    ) == 0:
        pushes: i64 = atomic_load_i32(
            global_addr("pcc_gc_cms_queue_pushes"), 0, "acquire"
        )
        drains: i64 = atomic_load_i32(
            global_addr("pcc_gc_cms_worker_drains"), 0, "acquire"
        )
        if drains < pushes:
            worker_stw: i64 = pcc_stop_the_world()
            if worker_stw == 0:
                traced: i64 = 0
                if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 2:
                    traced = pcc_gc_tracing_step_cycle(
                        pcc_gc_tracing_budget_from_debt()
                    )
                pcc_resume_world()
                if traced > 0:
                    atomic_rmw_i32(
                        "add",
                        global_addr("pcc_gc_cms_worker_traces"),
                        0,
                        traced,
                        "relaxed",
                    )
                atomic_rmw_i32(
                    "add",
                    global_addr("pcc_gc_cms_worker_drains"),
                    0,
                    1,
                    "relaxed",
                )
            else:
                pcc_platform_sleep_ns(1000000)
        else:
            pcc_platform_sleep_ns(1000000)
        pcc_thread_safepoint()
    atomic_rmw_i32(
        "add", global_addr("pcc_gc_cms_worker_stops"), 0, 1, "relaxed"
    )
    return null()


@c_abi_export("pcc_gc_config_parse_env_i32")
def pcc_gc_config_parse_env_i32(
    raw, default: i64, min_value: i64, max_value: i64
) -> i64:
    if ptr_is_null(raw) != 0:
        return default
    value: i64 = 0
    i: i64 = 0
    seen: i64 = 0
    neg: i64 = 0
    ch: i64 = load_i8(raw, 0) & 255
    if ch == 45:  # '-'
        neg: i64 = 1
        i: i64 = 1
    while True:
        ch = load_i8(raw, i) & 255
        if ch == 0:
            break
        if ch < 48 or ch > 57:
            return default
        value = value * 10 + (ch - 48)
        seen: i64 = 1
        i = i + 1
    if seen == 0:
        return default
    if neg != 0:
        value = -value
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


@c_abi_export("pcc_gc_config_abort_bad_backend")
def _pcc_gc_config_abort_bad_backend() -> None:
    message = cstr(
        "pcc runtime: invalid PCC_GC_BACKEND; expected one of 0,1,2,3,4\n"
    )
    length: i64 = 0
    while load_i8(message, length) != 0:
        length = length + 1
    pcc_platform_write(2, message, length)
    pcc_platform_abort()


@c_abi_export("pcc_gc_config_parse_backend")
def _pcc_gc_config_parse_backend(raw, default: i64) -> i64:
    if ptr_is_null(raw) != 0:
        return default
    first: i64 = load_i8(raw, 0) & 255
    if first >= 48 and first <= 52 and load_i8(raw, 1) == 0:
        return first - 48
    _pcc_gc_config_abort_bad_backend()
    return default


@c_abi_export("pcc_gc_maybe_start_cms_worker")
def pcc_gc_maybe_start_cms_worker() -> None:
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 2:
        return
    if pcc_threads_enabled() == 0:
        return
    atomic_store_i32(
        global_addr("pcc_gc_cms_worker_stop_requested"), 0, 0, "release"
    )
    if atomic_cas_i32(
        global_addr("pcc_gc_cms_worker_started"),
        0,
        0,
        1,
        "acq_rel",
        "acquire",
    ) != 0:
        return
    if pcc_thread_start(
        global_addr("pcc_gc_cms_worker_handle"),
        function_addr("pcc_gc_cms_worker_main_py"),
        null(),
    ) == 0:
        atomic_rmw_i32(
            "add", global_addr("pcc_gc_cms_worker_starts"), 0, 1, "relaxed"
        )
        return
    atomic_store_i32(
        global_addr("pcc_gc_cms_worker_started"), 0, 0, "release"
    )


@c_abi_export("pcc_gc_config_ensure")
def pcc_gc_config_ensure() -> i64:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0:
        return load_i32(global_addr("pcc_gc_backend_selected"), 0)
    store_i32(global_addr("pcc_gc_config_initialized"), 0, 1)
    backend: i64 = _pcc_gc_config_parse_backend(
        pcc_platform_getenv(cstr("PCC_GC_BACKEND")),
        load_i32(global_addr("pcc_gc_backend_selected"), 0),
    )
    pause: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_PAUSE")), 1000, 50, 1000
    )
    stepmul: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_STEPMUL")), 10000, 1, 10000
    )
    stepmul = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_STEP_MUL")), stepmul, 1, 10000
    )
    threshold: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_DEBT_THRESHOLD")),
        0, 0, 1099511627776,
    )
    minor_heap_size: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_MINOR_HEAP_SIZE")),
        33554432, 256, 1099511627776,
    )
    minor_alloc_max: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_MINOR_ALLOC_MAX")),
        16, 16, 1073741824,
    )
    store_i32(global_addr("pcc_gc_backend_selected"), 0, backend)
    if backend == 3 or backend == 4:
        store_i32(global_addr("pcc_gc_read_barrier_enabled"), 0, 1)
    else:
        store_i32(global_addr("pcc_gc_read_barrier_enabled"), 0, 0)
    store_i32(global_addr("pcc_gc_pause"), 0, pause)
    store_i32(global_addr("pcc_gc_stepmul"), 0, stepmul)
    store_i32(global_addr("pcc_gc_debt_threshold_override"), 0, threshold)
    store_i32(global_addr("pcc_gc_minor_heap_size"), 0, minor_heap_size)
    store_i32(global_addr("pcc_gc_minor_alloc_max"), 0, minor_alloc_max)
    if backend != 0:
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    pcc_gc_maybe_start_cms_worker()
    return backend


@c_abi_export("pcc_gc_has_tracing_sweep")
def pcc_gc_has_tracing_sweep() -> i64:
    backend: i64 = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_config_ensure()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 1 and backend != 2 and backend != 3 and backend != 4:
        return 0
    if pcc_gc_tracing_has_sweep_candidate() != 0:
        return 1
    return 0


@c_abi_export("pcc_gc_collect_tracing")
def pcc_gc_collect_tracing() -> i64:
    backend: i64 = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_config_ensure()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 1 and backend != 2 and backend != 3 and backend != 4:
        return 0
    if pcc_gc_tracing_has_sweep_candidate() == 0:
        return 0
    stw: i64 = pcc_stop_the_world()
    if stw != 0:
        return 0
    # ``gc.collect()`` is the explicit full-heap boundary, not an incremental
    # scheduler step.  The former 1024-object budget left every candidate
    # after the first batch live until a later call, so a 10k two-node-cycle
    # workload reported/reclaimed only 1024 objects and steadily grew RSS.
    # PASS-0/PASS-1/PASS-2 already walk the candidate graph as one STW
    # transaction; give that transaction the complete signed-i64 budget so
    # finalizers run once and the returned count describes this collection.
    reclaimed: i64 = pcc_gc_tracing_sweep_unreachable(9223372036854775807)
    if stw == 0:
        pcc_resume_world()
    return reclaimed


@c_abi_export("pcc_gc_begin_explicit_tracing_collect")
def pcc_gc_begin_explicit_tracing_collect() -> None:
    backend: i64 = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_config_ensure()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    store_i32(global_addr("pcc_gc_explicit_collect_active"), 0, 1)
    if backend != 0:
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)


@c_abi_export("pcc_gc_end_explicit_tracing_collect")
def pcc_gc_end_explicit_tracing_collect() -> None:
    store_i32(global_addr("pcc_gc_explicit_collect_active"), 0, 0)
