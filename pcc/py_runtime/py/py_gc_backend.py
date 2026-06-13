"""GC backend selector and telemetry for pcc-Python runtime archives.

Names are algorithmic, not project-branded:

0. refcount-cycle
1. incremental-tricolor
2. concurrent-mark-sweep
3. generational-minor-major
4. colored-relocating
"""

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr,
    getenv,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    memset,
    memmove,
    free,
    malloc,
    null,
    ptr_add,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    store_ptr,
    store_i64,
    store_i32,
)

# Slot-role / visit-mode values, inlined as literals at every use site
# (module-level int constants get zeroed in stripped library .o builds):
#   _PY_OBJ_SLOT_OWNED = 1
#   _PY_OBJ_SLOT_BORROWED_TRACED = 2
#   _PY_OBJ_SLOT_BORROWED_UPDATE_ONLY = 3
#   _PY_OBJ_VISIT_TRACE = 1
#   _PY_OBJ_VISIT_PROMOTE = 2
#   _PY_OBJ_VISIT_UPDATE = 3
#   _PY_OBJ_VISIT_SUBTRACT = 4
#   _PY_OBJ_VISIT_CLEAR = 5
#   _PY_OBJ_VISIT_RELOCATE_COUNT = 6
#   _PY_OBJ_VISIT_RELOCATE_FROM = 7
#   _PY_OBJ_VISIT_RELOCATE_TO = 8

# Shared ABI entry points used by backend-level finalization.
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_object_index_insert = extern(
    "pcc_gc_object_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_object_index_remove = extern("pcc_gc_object_index_remove", (c_ptr,), c_ptr)
pcc_gc_object_index_clear = extern("pcc_gc_object_index_clear", (), c_void)
pcc_gc_forwarding_index_find = extern("pcc_gc_forwarding_index_find", (c_ptr,), c_ptr)
pcc_gc_forwarding_index_insert = extern(
    "pcc_gc_forwarding_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_forwarding_index_remove = extern(
    "pcc_gc_forwarding_index_remove",
    (c_ptr,),
    c_ptr,
)
pcc_gc_forwarding_index_clear = extern("pcc_gc_forwarding_index_clear", (), c_void)
pcc_gc_forwarding_target_index_find = extern(
    "pcc_gc_forwarding_target_index_find", (c_ptr,), c_ptr
)
pcc_gc_forwarding_target_index_insert = extern(
    "pcc_gc_forwarding_target_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_forwarding_target_index_upsert = extern(
    "pcc_gc_forwarding_target_index_upsert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_forwarding_target_index_remove = extern(
    "pcc_gc_forwarding_target_index_remove",
    (c_ptr,),
    c_ptr,
)
pcc_gc_forwarding_target_index_clear = extern(
    "pcc_gc_forwarding_target_index_clear", (), c_void
)
pcc_gc_identity_index_find = extern("pcc_gc_identity_index_find", (c_ptr,), c_ptr)
pcc_gc_identity_index_insert = extern(
    "pcc_gc_identity_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_identity_index_remove = extern("pcc_gc_identity_index_remove", (c_ptr,), c_ptr)
pcc_gc_identity_index_clear = extern("pcc_gc_identity_index_clear", (), c_void)
pcc_gc_frame_index_insert = extern(
    "pcc_gc_frame_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_frame_index_find = extern("pcc_gc_frame_index_find", (c_ptr,), c_ptr)
pcc_gc_frame_index_replace = extern(
    "pcc_gc_frame_index_replace",
    (c_ptr, c_ptr),
    c_ptr,
)
pcc_gc_frame_index_remove = extern("pcc_gc_frame_index_remove", (c_ptr,), c_ptr)
pcc_gc_zpage_owner_index_find = extern(
    "pcc_gc_zpage_owner_index_find",
    (c_ptr,),
    c_ptr,
)
pcc_gc_zpage_owner_index_insert = extern(
    "pcc_gc_zpage_owner_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_zpage_owner_index_upsert = extern(
    "pcc_gc_zpage_owner_index_upsert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_zpage_owner_index_remove = extern(
    "pcc_gc_zpage_owner_index_remove",
    (c_ptr,),
    c_ptr,
)
pcc_gc_zpage_page_index_find = extern(
    "pcc_gc_zpage_page_index_find",
    (c_ptr,),
    c_ptr,
)
pcc_gc_zpage_page_index_insert = extern(
    "pcc_gc_zpage_page_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_zpage_page_index_upsert = extern(
    "pcc_gc_zpage_page_index_upsert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_zpage_page_index_remove = extern(
    "pcc_gc_zpage_page_index_remove",
    (c_ptr,),
    c_ptr,
)
py_weakref_invalidate = extern("py_weakref_invalidate", (c_ptr,), c_void)
py_user_del_dispatch = extern("py_user_del_dispatch", (c_ptr,), c_void)
py_gc_untrack = extern("py_gc_untrack", (c_ptr,), c_void)
pcc_gc_note_object_freeing = extern("pcc_gc_note_object_freeing", (c_ptr,), c_void)
pcc_gc_load_ptr_extern = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_store_root_extern = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)
py_tls_exc_get = extern("py_tls_exc_get", (), c_ptr)
py_tls_exc_set = extern("py_tls_exc_set", (c_ptr,), c_void)
pcc_mutex_new = extern("pcc_mutex_new", (), c_ptr)
pcc_mutex_free = extern("pcc_mutex_free", (c_ptr,), c_void)
pcc_mutex_lock = extern("pcc_mutex_lock", (c_ptr,), c_int64)
pcc_mutex_unlock = extern("pcc_mutex_unlock", (c_ptr,), c_int64)
py_dealloc_int = extern("py_dealloc_int", (c_ptr,), c_void)
py_dealloc_float = extern("py_dealloc_float", (c_ptr,), c_void)
py_dealloc_str = extern("py_dealloc_str", (c_ptr,), c_void)
py_dealloc_list = extern("py_dealloc_list", (c_ptr,), c_void)
py_dealloc_tuple = extern("py_dealloc_tuple", (c_ptr,), c_void)
py_dealloc_dict = extern("py_dealloc_dict", (c_ptr,), c_void)
py_dealloc_set = extern("py_dealloc_set", (c_ptr,), c_void)
py_dealloc_func = extern("py_dealloc_func", (c_ptr,), c_void)
py_dealloc_file = extern("py_dealloc_file", (c_ptr,), c_void)
py_dealloc_iter = extern("py_dealloc_iter", (c_ptr,), c_void)
py_dealloc_gen = extern("py_dealloc_gen", (c_ptr,), c_void)
py_dealloc_coroutine = extern("py_dealloc_coroutine", (c_ptr,), c_void)
py_dealloc_continuation = extern("py_dealloc_continuation", (c_ptr,), c_void)
py_dealloc_task = extern("py_dealloc_task", (c_ptr,), c_void)
py_dealloc_memoryview = extern("py_dealloc_memoryview", (c_ptr,), c_void)
py_dealloc_weakref = extern("py_dealloc_weakref", (c_ptr,), c_void)
py_dealloc_thread_lock = extern("py_dealloc_thread_lock", (c_ptr,), c_void)
py_dealloc_thread_rlock = extern("py_dealloc_thread_rlock", (c_ptr,), c_void)
py_dealloc_thread_event = extern("py_dealloc_thread_event", (c_ptr,), c_void)
py_dealloc_thread_condition = extern("py_dealloc_thread_condition", (c_ptr,), c_void)
py_dealloc_thread_semaphore = extern("py_dealloc_thread_semaphore", (c_ptr,), c_void)
py_dealloc_thread_thread = extern("py_dealloc_thread_thread", (c_ptr,), c_void)
py_dealloc_virtual_thread = extern("py_dealloc_virtual_thread", (c_ptr,), c_void)
py_dealloc_generic = extern("py_dealloc_generic", (c_ptr,), c_void)
py_class_dealloc = extern("py_class_dealloc", (c_ptr,), c_void)
py_instance_dealloc = extern("py_instance_dealloc", (c_ptr,), c_void)
py_dealloc_exc = extern("py_dealloc_exc", (c_ptr,), c_void)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,), c_int64)
pcc_capi_dealloc_cext_object = extern(
    "pcc_capi_dealloc_cext_object",
    (c_ptr, c_int64),
    c_int64,
)
pcc_capi_visit_cext_object_slots_i64 = extern(
    "pcc_capi_visit_cext_object_slots_i64",
    (c_ptr, c_ptr, c_ptr),
    c_int32,
)
abort_extern = extern("abort", (), c_void)
pcc_refcount_forget = extern("pcc_refcount_forget", (c_ptr,), c_void)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_runtime_now_us = extern("pcc_runtime_now_us", (), c_int64)
pcc_py_gc_minor_current_get = extern("pcc_py_gc_minor_current_get", (), c_ptr)
pcc_py_gc_minor_current_set = extern("pcc_py_gc_minor_current_set", (c_ptr,), c_void)
pcc_py_gc_pending_minor_block_get = extern(
    "pcc_py_gc_pending_minor_block_get", (), c_ptr
)
pcc_py_gc_pending_minor_block_set = extern(
    "pcc_py_gc_pending_minor_block_set", (c_ptr,), c_void
)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_py_atomic_i32_load = extern("pcc_py_atomic_i32_load", (c_ptr,), c_int32)
pcc_py_atomic_i32_store = extern("pcc_py_atomic_i32_store", (c_ptr, c_int32), c_void)
pcc_py_atomic_i32_add_fetch = extern(
    "pcc_py_atomic_i32_add_fetch", (c_ptr, c_int32), c_int32
)
pcc_py_atomic_i64_load = extern("pcc_py_atomic_i64_load", (c_ptr,), c_int64)
pcc_py_atomic_i64_store = extern("pcc_py_atomic_i64_store", (c_ptr, c_int64), c_void)
pcc_py_atomic_i64_add_fetch = extern(
    "pcc_py_atomic_i64_add_fetch", (c_ptr, c_int64), c_int64
)
pcc_py_atomic_i64_dec_if_positive = extern(
    "pcc_py_atomic_i64_dec_if_positive", (c_ptr,), c_int64
)


def _gc_ptr_can_have_header(o) -> bool:
    if ptr_is_null(o) != 0:
        return False
    if is_tagged_int(o) != 0:
        return False
    bits: int = ptr_diff(o, null())
    if bits < 4096:
        return False
    if (bits & 7) != 0:
        return False
    if bits >= 281474976710656:
        return False
    return True


def _parse_env_i32(raw, default: int, min_value: int, max_value: int) -> int:
    if ptr_is_null(raw) != 0:
        return default
    value: int = 0
    i: int = 0
    seen: int = 0
    neg: int = 0
    ch: int = load_i8(raw, 0) & 255
    if ch == 45:  # '-'
        neg = 1
        i = 1
    while True:
        ch = load_i8(raw, i) & 255
        if ch == 0:
            break
        if ch < 48 or ch > 57:
            return default
        value = value * 10 + (ch - 48)
        seen = 1
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


def _init_config() -> int:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0:
        return load_i32(global_addr("pcc_gc_backend_selected"), 0)
    store_i32(global_addr("pcc_gc_config_initialized"), 0, 1)
    backend: int = _parse_env_i32(
        getenv(cstr("PCC_GC_BACKEND")),
        load_i32(global_addr("pcc_gc_backend_selected"), 0),
        0,
        4,
    )
    pause: int = _parse_env_i32(getenv(cstr("PCC_GC_PAUSE")), 1000, 50, 1000)
    stepmul: int = _parse_env_i32(getenv(cstr("PCC_GC_STEPMUL")), 10000, 1, 10000)
    stepmul = _parse_env_i32(getenv(cstr("PCC_GC_STEP_MUL")), stepmul, 1, 10000)
    threshold: int = _parse_env_i32(
        getenv(cstr("PCC_GC_DEBT_THRESHOLD")), 0, 0, 1073741824
    )
    minor_heap_size: int = _parse_env_i32(
        getenv(cstr("PCC_GC_MINOR_HEAP_SIZE")), 33554432, 256, 1073741824
    )
    minor_alloc_max: int = _parse_env_i32(
        getenv(cstr("PCC_GC_MINOR_ALLOC_MAX")), 16, 16, 1073741824
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
    _maybe_start_cms_worker()
    return backend


def _debt_threshold() -> int:
    override: int = load_i32(global_addr("pcc_gc_debt_threshold_override"), 0)
    if override > 0:
        return override
    threshold: int = 65536
    live: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
    pause: int = load_i32(global_addr("pcc_gc_pause"), 0)
    if live > 0 and pause > 100:
        live_pause: int = (live * (pause - 100)) // 100
        if live_pause > threshold:
            threshold = live_pause
    return threshold


def _budget_from_debt() -> int:
    debt: int = load_i32(global_addr("pcc_gc_debt_bytes"), 0)
    stepmul: int = load_i32(global_addr("pcc_gc_stepmul"), 0)
    budget: int = ((debt // 64) * stepmul) // 100
    if budget < 1:
        budget = 1
    if budget > 65536:
        budget = 65536
    return budget


def _discharge_debt(processed: int) -> None:
    if processed <= 0:
        return
    debt: int = load_i32(global_addr("pcc_gc_debt_bytes"), 0)
    stepmul: int = load_i32(global_addr("pcc_gc_stepmul"), 0)
    credit: int = (processed * 64 * stepmul) // 100
    if credit < 64:
        credit = 64
    if credit >= debt:
        store_i32(global_addr("pcc_gc_debt_bytes"), 0, 0)
    else:
        store_i32(global_addr("pcc_gc_debt_bytes"), 0, debt - credit)


def _record_pause(start_us: int, end_us: int) -> None:
    if start_us <= 0:
        return
    if end_us < start_us:
        return
    pause: int = end_us - start_us
    if pause <= 0:
        pause = 1
    # G-P3-LONGRUN mirror of the C tier: count + sum + 4-bucket
    # histogram (i32 globals — sum_us can saturate after ~35min of
    # accumulated pauses; recorded limit, C tier is i64).
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
    current: int = load_i32(global_addr("pcc_gc_metric_max_pause_us"), 0)
    if pause > current:
        store_i32(global_addr("pcc_gc_metric_max_pause_us"), 0, pause)


@c_abi_export("pcc_gc_record_explicit_pause")
def pcc_gc_record_explicit_pause(start_us: int, end_us: int) -> None:
    _record_pause(start_us, end_us)


def _maybe_auto_step() -> None:
    if load_i32(global_addr("pcc_gc_in_auto_step"), 0) != 0:
        return
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 1:
        return
    if pcc_threads_enabled() != 0:
        return
    debt: int = load_i32(global_addr("pcc_gc_debt_bytes"), 0)
    if debt < _debt_threshold():
        return
    store_i32(global_addr("pcc_gc_in_auto_step"), 0, 1)
    pcc_gc_step(_budget_from_debt())
    store_i32(global_addr("pcc_gc_in_auto_step"), 0, 0)


def _maybe_start_cms_worker() -> None:
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 2:
        return
    if pcc_threads_enabled() == 0:
        return
    if load_i32(global_addr("pcc_gc_cms_worker_started"), 0) != 0:
        return
    store_i32(global_addr("pcc_gc_cms_worker_started"), 0, 1)
    starts: int = load_i32(global_addr("pcc_gc_cms_worker_starts"), 0)
    store_i32(global_addr("pcc_gc_cms_worker_starts"), 0, starts + 1)


def _stop_cms_worker() -> None:
    if load_i32(global_addr("pcc_gc_cms_worker_started"), 0) == 0:
        return
    store_i32(global_addr("pcc_gc_cms_worker_started"), 0, 0)
    stops: int = load_i32(global_addr("pcc_gc_cms_worker_stops"), 0)
    store_i32(global_addr("pcc_gc_cms_worker_stops"), 0, stops + 1)


def _note_cms_alloc(bytes: int) -> None:
    if bytes <= 0:
        bytes = 1
    pushes: int = load_i32(global_addr("pcc_gc_cms_queue_pushes"), 0)
    store_i32(global_addr("pcc_gc_cms_queue_pushes"), 0, pushes + 1)
    debt: int = load_i32(global_addr("pcc_gc_debt_bytes"), 0) + bytes
    store_i32(global_addr("pcc_gc_debt_bytes"), 0, debt)
    if debt >= _debt_threshold():
        assists: int = load_i32(global_addr("pcc_gc_cms_mutator_assists"), 0)
        store_i32(global_addr("pcc_gc_cms_mutator_assists"), 0, assists + 1)
        store_i32(global_addr("pcc_gc_in_auto_step"), 0, 1)
        pcc_gc_step(_budget_from_debt())
        store_i32(global_addr("pcc_gc_in_auto_step"), 0, 0)


def _minor_collect_reset() -> None:
    pcc_py_atomic_i32_add_fetch(global_addr("pcc_gc_minor_collections"), 1)
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 3:
        _step_generational_promotion(1024, 0)
    pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_bytes"), 0)


def _note_minor_alloc(bytes: int) -> None:
    max_size: int = load_i32(global_addr("pcc_gc_minor_alloc_max"), 0)
    if bytes <= 0 or bytes > max_size:
        return
    pcc_py_atomic_i32_add_fetch(global_addr("pcc_gc_minor_allocations"), 1)
    pcc_py_atomic_i32_add_fetch(global_addr("pcc_gc_minor_bytes"), bytes)


def _align16(bytes: int) -> int:
    if bytes <= 0:
        return 0
    return (bytes + 15) & ~15


def _minor_blocks_head():
    return global_load_ptr("pcc_gc_minor_blocks")


def _set_minor_blocks_head(head) -> None:
    global_store_ptr("pcc_gc_minor_blocks", head)


def _minor_current():
    return pcc_py_gc_minor_current_get()


def _set_minor_current(block) -> None:
    pcc_py_gc_minor_current_set(block)


def _pending_minor_block():
    return pcc_py_gc_pending_minor_block_get()


def _set_pending_minor_block(block) -> None:
    pcc_py_gc_pending_minor_block_set(block)


def _minor_block_base(block):
    return load_ptr(block, 0)


def _minor_block_used(block) -> int:
    return load_i64(block, 8)


def _set_minor_block_used(block, used: int) -> None:
    store_i64(block, 8, used)


def _minor_block_size(block) -> int:
    return load_i64(block, 16)


def _minor_block_next(block):
    return load_ptr(block, 24)


def _set_minor_block_next(block, nxt) -> None:
    store_ptr(block, 24, nxt)


def _minor_block_live(block) -> int:
    return pcc_py_atomic_i64_load(ptr_add(block, 32))


def _set_minor_block_live(block, live: int) -> None:
    pcc_py_atomic_i64_store(ptr_add(block, 32), live)


def _minor_block_owner(block) -> int:
    return load_i64(block, 40)


def _minor_new_block(min_bytes: int):
    block_bytes: int = load_i32(global_addr("pcc_gc_minor_heap_size"), 0)
    if block_bytes < min_bytes:
        block_bytes = min_bytes
    block_bytes = _align16(block_bytes)
    if block_bytes <= 0:
        return null()

    block = malloc(48)
    if ptr_is_null(block) != 0:
        return null()
    base = malloc(block_bytes)
    if ptr_is_null(base) != 0:
        free(block)
        return null()
    memset(base, 0, block_bytes)
    store_ptr(block, 0, base)
    store_i64(block, 8, 0)
    store_i64(block, 16, block_bytes)
    store_i64(block, 32, 0)
    store_i64(block, 40, pcc_current_thread_id())
    pcc_py_gc_minor_graph_lock()
    store_ptr(block, 24, _minor_blocks_head())
    _set_minor_blocks_head(block)
    pcc_py_gc_minor_graph_unlock()
    _set_minor_current(block)
    pcc_py_atomic_i32_add_fetch(global_addr("pcc_gc_minor_arena_refills"), 1)
    return block


def _minor_release_block(block) -> None:
    if ptr_is_null(block) != 0:
        return
    live: int = pcc_py_atomic_i64_dec_if_positive(ptr_add(block, 32))
    if live != 0:
        return
    # Span-retain empty minor blocks so stale SSA/root pointers stay
    # recognizable by the free-path span fallback.
    if _minor_block_owner(block) == pcc_current_thread_id():
        pcc_py_atomic_i64_store(ptr_add(block, 8), 0)
        _set_minor_current(block)
        pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_bytes"), 0)
    return


def _minor_block_containing(ptr):
    if ptr_is_null(ptr) != 0:
        return null()
    node = _minor_blocks_head()
    while ptr_is_null(node) == 0:
        base = _minor_block_base(node)
        delta: int = ptr_diff(ptr, base)
        if delta >= 0 and delta < _minor_block_size(node):
            return node
        node = _minor_block_next(node)
    return null()


def _minor_find_reusable_block(min_bytes: int):
    owner: int = pcc_current_thread_id()
    pcc_py_gc_minor_graph_lock()
    node = _minor_blocks_head()
    while ptr_is_null(node) == 0:
        if (
            _minor_block_owner(node) == owner
            and _minor_block_live(node) == 0
            and _minor_block_size(node) >= min_bytes
        ):
            _set_minor_block_used(node, 0)
            pcc_py_gc_minor_graph_unlock()
            _set_minor_current(node)
            pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_bytes"), 0)
            return node
        node = _minor_block_next(node)
    pcc_py_gc_minor_graph_unlock()
    return null()


@c_abi_export("pcc_gc_try_minor_alloc")
def pcc_gc_try_minor_alloc(bytes: int):
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    _set_pending_minor_block(null())
    if backend != 3:
        return null()

    aligned: int = _align16(bytes)
    max_size: int = load_i32(global_addr("pcc_gc_minor_alloc_max"), 0)
    if aligned <= 0 or aligned > max_size:
        pcc_py_atomic_i32_add_fetch(global_addr("pcc_gc_minor_arena_fallbacks"), 1)
        return null()

    block = _minor_current()
    if ptr_is_null(block) == 0:
        used: int = _minor_block_used(block)
        total: int = _minor_block_size(block)
        if total - used < aligned:
            if (
                _minor_block_owner(block) == pcc_current_thread_id()
                and _minor_block_live(block) == 0
                and total >= aligned
            ):
                _set_minor_block_used(block, 0)
                pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_bytes"), 0)
            else:
                _minor_collect_reset()
                block = _minor_find_reusable_block(aligned)
    if ptr_is_null(block) != 0:
        block = _minor_find_reusable_block(aligned)
    if ptr_is_null(block) != 0:
        block = _minor_new_block(aligned)
        if ptr_is_null(block) != 0:
            pcc_py_atomic_i32_add_fetch(global_addr("pcc_gc_minor_arena_fallbacks"), 1)
            return null()

    used2: int = _minor_block_used(block)
    mem = ptr_add(_minor_block_base(block), used2)
    _set_minor_block_used(block, used2 + aligned)
    pcc_py_atomic_i64_add_fetch(ptr_add(block, 32), 1)
    _set_pending_minor_block(block)
    pcc_py_atomic_i32_add_fetch(global_addr("pcc_gc_minor_arena_bumps"), 1)
    _note_minor_alloc(aligned)
    memset(mem, 0, bytes)
    return mem


def _counter_global(metric: int):
    if metric == 0:
        return global_addr("pcc_gc_metric_alloc")
    if metric == 1:
        return global_addr("pcc_gc_metric_store")
    if metric == 2:
        return global_addr("pcc_gc_metric_load")
    if metric == 3:
        return global_addr("pcc_gc_metric_safepoint")
    if metric == 4:
        return global_addr("pcc_gc_metric_pin")
    if metric == 5:
        return global_addr("pcc_gc_metric_step")
    return global_addr("pcc_gc_metric_step")


def _counter_inc(metric: int, delta: int) -> None:
    slot = _counter_global(metric)
    v: int = load_i32(slot, 0)
    store_i32(slot, 0, v + delta)


def _object_head():
    return global_load_ptr("pcc_gc_object_head")


def _set_object_head(head) -> None:
    global_store_ptr("pcc_gc_object_head", head)


def _trace_cursor():
    return global_load_ptr("pcc_gc_trace_cursor")


def _set_trace_cursor(node) -> None:
    global_store_ptr("pcc_gc_trace_cursor", node)


def _gray_count() -> int:
    return load_i32(global_addr("pcc_gc_gray_count"), 0)


def _set_gray_count(value: int) -> None:
    store_i32(global_addr("pcc_gc_gray_count"), 0, value)


def _inc_gray_count() -> None:
    _set_gray_count(_gray_count() + 1)


def _dec_gray_count() -> None:
    count: int = _gray_count()
    if count > 0:
        _set_gray_count(count - 1)


def _object_graph_lock() -> None:
    pcc_py_gc_minor_graph_lock()


def _object_graph_unlock() -> None:
    pcc_py_gc_minor_graph_unlock()


def _object_node_size(node) -> int:
    return load_i64(node, 8)


def _object_node_next(node):
    return load_ptr(node, 16)


def _set_object_node_next(node, nxt) -> None:
    store_ptr(node, 16, nxt)


def _object_node_minor_block(node):
    return load_ptr(node, 24)


def _object_node_freeing(node) -> int:
    return load_i64(node, 32)


def _set_object_node_freeing(node, freeing: int) -> None:
    store_i64(node, 32, freeing)


def _object_node_is_active(node) -> int:
    if ptr_is_null(node) != 0:
        return 0
    if _object_node_freeing(node) != 0:
        return 0
    obj = load_ptr(node, 0)
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    # Backend 4 keeps a zpage node indexed until type-specific deallocation has
    # consumed its fields.  Bit 524288 marks only that delayed-deallocation
    # window; refcount zero alone also describes valid forwarding shells.
    if (load_i32(obj, 12) & 524288) != 0:
        return 0
    return 1


def _object_node_prev(node):
    return load_ptr(node, 40)


def _set_object_node_prev(node, prev) -> None:
    store_ptr(node, 40, prev)


def _object_node_zpage(node):
    return load_ptr(node, 48)


def _set_object_node_zpage(node, zpage_node) -> None:
    store_ptr(node, 48, zpage_node)


def _object_node_gc_refs(node) -> int:
    return load_i64(node, 56)


def _set_object_node_gc_refs(node, value: int) -> None:
    store_i64(node, 56, value)


def _object_node_alloc():
    head = global_load_ptr("pcc_gc_object_node_free_head")
    if ptr_is_null(head) == 0:
        nxt = load_ptr(head, 16)
        global_store_ptr("pcc_gc_object_node_free_head", nxt)
        count: int = load_i32(global_addr("pcc_gc_object_node_free_count"), 0)
        if count > 0:
            store_i32(global_addr("pcc_gc_object_node_free_count"), 0, count - 1)
        return head
    node = malloc(64)
    return node


def _object_node_release(node) -> None:
    if ptr_is_null(node) != 0:
        return
    count: int = load_i32(global_addr("pcc_gc_object_node_free_count"), 0)
    if count >= 8192:
        free(node)
        return
    store_ptr(node, 16, global_load_ptr("pcc_gc_object_node_free_head"))
    global_store_ptr("pcc_gc_object_node_free_head", node)
    store_i32(global_addr("pcc_gc_object_node_free_count"), 0, count + 1)


def _unlink_object_node(node) -> None:
    prev = _object_node_prev(node)
    nxt = _object_node_next(node)
    if ptr_eq(_trace_cursor(), node) != 0:
        _set_trace_cursor(nxt)
    if ptr_is_null(prev) != 0:
        _set_object_head(nxt)
    else:
        _set_object_node_next(prev, nxt)
    if ptr_is_null(nxt) == 0:
        _set_object_node_prev(nxt, prev)


def _object_known_size(obj) -> int:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    node = pcc_gc_object_index_find(obj)
    if ptr_is_null(node) == 0 and _object_node_freeing(node) == 0:
        return _object_node_size(node)
    return 0


def _live_bytes_subtract(size: int) -> None:
    if size <= 0:
        return
    live: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
    if size >= live:
        store_i32(global_addr("pcc_gc_live_bytes"), 0, 0)
    else:
        store_i32(global_addr("pcc_gc_live_bytes"), 0, live - size)


def _gc_tracks_objects() -> int:
    return pcc_gc_backend() != 0


def _backend3_graph_leaf_tag(tag: int) -> int:
    if tag == 0:
        return 1
    if tag == 1:
        return 1
    if tag == 2:
        return 1
    if tag == 3:
        return 1
    if tag == 4:
        return 1
    if tag == 16:
        return 1
    if tag == 17:
        return 1
    if tag == 18:
        return 1
    if tag == 32:
        return 1
    return 0


def _should_track_frame_roots() -> int:
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0:
        return 1
    return load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0)


def _frame_roots_disabled_fast() -> int:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return 0
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0:
        return 0
    if load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0:
        return 0
    return 1


def _clear_object_list() -> None:
    _object_graph_lock()
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = _object_node_next(node)
        free(node)
        node = nxt
    _set_object_head(null())
    _set_trace_cursor(null())
    _set_gray_count(0)
    pcc_gc_object_index_clear()
    _object_graph_unlock()
    global_store_ptr("pcc_gc_last_alloc", null())


def _forwarding_head():
    return global_load_ptr("pcc_gc_forwarding_head")


def _set_forwarding_head(head) -> None:
    global_store_ptr("pcc_gc_forwarding_head", head)


def _forwarding_find(from_obj):
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return null()
    return pcc_gc_forwarding_index_find(from_obj)


def _forwarding_target_find(target):
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0:
        return null()
    return pcc_gc_forwarding_target_index_find(target)


def _forwarding_target_exists(target) -> int:
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0:
        return 0
    if ptr_is_null(_forwarding_target_find(target)) == 0:
        return 1
    return 0


def _forwarding_target_prepare(target, node):
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0 or ptr_is_null(node) != 0:
        return null()
    head = _forwarding_target_find(target)
    rc: int = 0
    if ptr_is_null(head) != 0:
        rc = pcc_gc_forwarding_target_index_insert(target, node)
    else:
        rc = pcc_gc_forwarding_target_index_upsert(target, node)
    if rc < 0:
        return null()
    if ptr_is_null(head) != 0:
        return node
    return head


def _forwarding_target_attach_prepared(node, prepared_head) -> None:
    if ptr_is_null(node) != 0:
        return
    old_head = prepared_head
    if ptr_eq(prepared_head, node) != 0:
        old_head = null()
    store_ptr(node, 32, old_head)
    store_ptr(node, 40, null())
    if ptr_is_null(old_head) == 0:
        store_ptr(old_head, 40, node)


def _forwarding_target_unlink(node) -> None:
    if ptr_is_null(node) != 0:
        return
    target = load_ptr(node, 8)
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0:
        return
    prev = load_ptr(node, 40)
    nxt = load_ptr(node, 32)
    if ptr_is_null(prev) == 0:
        store_ptr(prev, 32, nxt)
    elif ptr_is_null(nxt) == 0:
        pcc_gc_forwarding_target_index_upsert(target, nxt)
    else:
        pcc_gc_forwarding_target_index_remove(target)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 40, prev)
    store_ptr(node, 32, null())
    store_ptr(node, 40, null())


def _forwarding_unlink_main(node) -> None:
    if ptr_is_null(node) != 0:
        return
    prev = load_ptr(node, 24)
    nxt = load_ptr(node, 16)
    if ptr_is_null(prev) != 0:
        _set_forwarding_head(nxt)
    else:
        store_ptr(prev, 16, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 24, prev)
    store_ptr(node, 16, null())
    store_ptr(node, 24, null())


def _forwarding_remove(from_obj) -> None:
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return
    node = pcc_gc_forwarding_index_remove(from_obj)
    if ptr_is_null(node) != 0:
        return
    _forwarding_target_unlink(node)
    _forwarding_unlink_main(node)
    target = load_ptr(node, 8)
    py_decref(target)
    fpage = load_ptr(node, 48)
    free(node)
    pop1: int = load_i32(global_addr("pcc_gc_forwarding_population"), 0)
    if pop1 > 0:
        store_i32(global_addr("pcc_gc_forwarding_population"), 0, pop1 - 1)
    if ptr_is_null(fpage) == 0:
        _backend4_note_forwarding_removed_on_page(fpage)
    else:
        _backend4_zpage_note_forwarding_removed(from_obj)


def _forwarding_remove_target(target) -> None:
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0:
        return
    node = pcc_gc_forwarding_target_index_remove(target)
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 32)
        from_obj = load_ptr(node, 0)
        pcc_gc_forwarding_index_remove(from_obj)
        _forwarding_unlink_main(node)
        store_ptr(node, 32, null())
        store_ptr(node, 40, null())
        fpage2 = load_ptr(node, 48)
        free(node)
        pop2: int = load_i32(global_addr("pcc_gc_forwarding_population"), 0)
        if pop2 > 0:
            store_i32(global_addr("pcc_gc_forwarding_population"), 0, pop2 - 1)
        if ptr_is_null(fpage2) == 0:
            _backend4_note_forwarding_removed_on_page(fpage2)
        else:
            _backend4_zpage_note_forwarding_removed(from_obj)
        node = nxt


def _forwarding_clear_all() -> None:
    node = _forwarding_head()
    _set_forwarding_head(null())
    pcc_gc_forwarding_index_clear()
    pcc_gc_forwarding_target_index_clear()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        target = load_ptr(node, 8)
        py_decref(target)
        free(node)
        node = nxt
    store_i32(global_addr("pcc_gc_forwarding_population"), 0, 0)


def _identity_head():
    return global_load_ptr("pcc_gc_identity_head")


def _set_identity_head(head) -> None:
    global_store_ptr("pcc_gc_identity_head", head)


def _identity_find(obj):
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return null()
    return pcc_gc_identity_index_find(obj)


def _identity_ensure(obj):
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return null()
    node = _identity_find(obj)
    if ptr_is_null(node) == 0:
        return node
    node = malloc(32)
    if ptr_is_null(node) != 0:
        return null()
    stable_id: int = load_i32(global_addr("pcc_gc_next_object_id"), 0)
    if stable_id <= 0:
        stable_id = 1
    store_i32(global_addr("pcc_gc_next_object_id"), 0, stable_id + 1)
    store_ptr(node, 0, obj)
    store_i64(node, 8, stable_id)
    store_ptr(node, 16, _identity_head())
    store_ptr(node, 24, null())
    old_head = _identity_head()
    if ptr_is_null(old_head) == 0:
        store_ptr(old_head, 24, node)
    _set_identity_head(node)
    if pcc_gc_identity_index_insert(obj, node) < 0:
        _set_identity_head(load_ptr(node, 16))
        nxt = load_ptr(node, 16)
        if ptr_is_null(nxt) == 0:
            store_ptr(nxt, 24, null())
        free(node)
        return null()
    return node


def _identity_assign(obj, stable_id: int) -> int:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    if stable_id <= 0:
        return 0
    node = _identity_find(obj)
    if ptr_is_null(node) == 0:
        store_i64(node, 8, stable_id)
        return 1
    node = malloc(32)
    if ptr_is_null(node) != 0:
        return 0
    store_ptr(node, 0, obj)
    store_i64(node, 8, stable_id)
    store_ptr(node, 16, _identity_head())
    store_ptr(node, 24, null())
    old_head = _identity_head()
    if ptr_is_null(old_head) == 0:
        store_ptr(old_head, 24, node)
    _set_identity_head(node)
    if pcc_gc_identity_index_insert(obj, node) < 0:
        _set_identity_head(load_ptr(node, 16))
        nxt = load_ptr(node, 16)
        if ptr_is_null(nxt) == 0:
            store_ptr(nxt, 24, null())
        free(node)
        return 0
    return 1


def _identity_remove(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    node = pcc_gc_identity_index_remove(obj)
    if ptr_is_null(node) != 0:
        return
    prev = load_ptr(node, 24)
    nxt = load_ptr(node, 16)
    if ptr_is_null(prev) != 0:
        _set_identity_head(nxt)
    else:
        store_ptr(prev, 16, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 24, prev)
    free(node)


def _identity_clear_all() -> None:
    node = _identity_head()
    _set_identity_head(null())
    pcc_gc_identity_index_clear()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        free(node)
        node = nxt


def _relocation_set_head():
    return global_load_ptr("pcc_gc_relocation_set_head")


def _set_relocation_set_head(head) -> None:
    global_store_ptr("pcc_gc_relocation_set_head", head)


def _store_buffer_head():
    return global_load_ptr("pcc_gc_backend4_store_buffer_head")


def _set_store_buffer_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_store_buffer_head", head)


def _store_buffer_medium_head():
    return global_load_ptr("pcc_gc_backend4_store_buffer_medium_head")


def _set_store_buffer_medium_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_store_buffer_medium_head", head)


def _zpage_head():
    return global_load_ptr("pcc_gc_backend4_zpage_head")


def _set_zpage_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_zpage_head", head)


def _zpage_payload_span_head():
    return global_load_ptr("pcc_gc_backend4_zpage_payload_span_head")


def _set_zpage_payload_span_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_zpage_payload_span_head", head)


def _zpage_page_head():
    return global_load_ptr("pcc_gc_backend4_page_head")


def _set_zpage_page_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_page_head", head)


def _zpage_free_page_head():
    return global_load_ptr("pcc_gc_backend4_free_page_head")


def _set_zpage_free_page_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_free_page_head", head)


def _zpage_retained_page_head():
    return global_load_ptr("pcc_gc_backend4_retained_page_head")


def _set_zpage_retained_page_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_retained_page_head", head)


def _evacuation_page_head():
    return global_load_ptr("pcc_gc_backend4_evacuation_page_head")


def _set_evacuation_page_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_evacuation_page_head", head)


def _relocation_set_find(obj):
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return null()
    node = _relocation_set_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), obj) != 0:
            return node
        node = load_ptr(node, 8)
    return null()


def _relocation_set_add(obj) -> int:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    flags: int = load_i32(obj, 12)
    if (flags & 8192) != 0:
        return 0
    if ptr_is_null(_forwarding_find(obj)) == 0:
        return 0
    if _forwarding_target_exists(obj) != 0:
        return 0
    if ptr_is_null(_relocation_set_find(obj)) == 0:
        return 0
    node = malloc(16)
    if ptr_is_null(node) != 0:
        return 0
    store_ptr(node, 0, obj)
    store_ptr(node, 8, _relocation_set_head())
    _set_relocation_set_head(node)
    store_i32(obj, 12, flags | 2048)
    return 1


def _relocation_set_remove(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    prev = null()
    node = _relocation_set_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 8)
        if ptr_eq(load_ptr(node, 0), obj) != 0:
            if ptr_is_null(prev) != 0:
                _set_relocation_set_head(nxt)
            else:
                store_ptr(prev, 8, nxt)
            if ptr_is_null(_forwarding_find(obj)) != 0:
                flags: int = load_i32(obj, 12)
                store_i32(obj, 12, flags & ~2048)
            free(node)
            return
        prev = node
        node = nxt


def _backend4_store_buffer_dec() -> None:
    pending: int = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_entries_count"), 0
    )
    if pending > 0:
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_entries_count"),
            0,
            pending - 1,
        )


def _backend4_store_buffer_contains(owner, slot, value) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return 0
    node = _store_buffer_medium_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            if ptr_eq(load_ptr(node, 8), slot) != 0:
                if ptr_eq(load_ptr(node, 16), value) != 0:
                    return 1
        node = load_ptr(node, 24)
    node = _store_buffer_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            if ptr_eq(load_ptr(node, 8), slot) != 0:
                if ptr_eq(load_ptr(node, 16), value) != 0:
                    return 1
        node = load_ptr(node, 24)
    return 0


def _backend4_store_buffer_medium_capacity() -> int:
    return 32


def _backend4_store_buffer_medium_count() -> int:
    return load_i32(global_addr("pcc_gc_backend4_store_buffer_medium_count"), 0)


def _backend4_store_buffer_medium_set_count(count: int) -> None:
    store_i32(global_addr("pcc_gc_backend4_store_buffer_medium_count"), 0, count)


def _backend4_store_buffer_append_global_owned(owner, slot, value) -> None:
    node = malloc(32)
    if ptr_is_null(node) != 0:
        _backend4_store_buffer_dec()
        py_decref(value)
        return
    store_ptr(node, 0, owner)
    store_ptr(node, 8, slot)
    store_ptr(node, 16, value)
    store_ptr(node, 24, _store_buffer_head())
    _set_store_buffer_head(node)


def _backend4_store_buffer_flush_medium_locked() -> None:
    count: int = _backend4_store_buffer_medium_count()
    if count <= 0:
        return
    node = _store_buffer_medium_head()
    _set_store_buffer_medium_head(null())
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 24)
        _backend4_store_buffer_append_global_owned(
            load_ptr(node, 0),
            load_ptr(node, 8),
            load_ptr(node, 16),
        )
        free(node)
        node = nxt
    _backend4_store_buffer_medium_set_count(0)
    flushes: int = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_flushes_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_flushes_count"), 0, flushes + 1
    )
    flushed: int = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_flushed_entries_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_flushed_entries_count"),
        0,
        flushed + count,
    )
    if count >= _backend4_store_buffer_medium_capacity():
        full: int = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_medium_full_flushes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_medium_full_flushes_count"),
            0,
            full + 1,
        )


def _backend4_store_buffer_enqueue(owner, slot, value) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return 0
    if _backend4_store_buffer_contains(owner, slot, value) != 0:
        skips: int = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_duplicate_skips_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_duplicate_skips_count"),
            0,
            skips + 1,
        )
        return 0
    flags: int = load_i32(owner, 12)
    if (
        _backend4_store_buffer_medium_count()
        >= _backend4_store_buffer_medium_capacity()
    ):
        _backend4_store_buffer_flush_medium_locked()
    if (
        _backend4_store_buffer_medium_count()
        >= _backend4_store_buffer_medium_capacity()
    ):
        return 0
    node = malloc(32)
    if ptr_is_null(node) != 0:
        return 0
    py_incref(value)
    store_ptr(node, 0, owner)
    store_ptr(node, 8, slot)
    store_ptr(node, 16, value)
    store_ptr(node, 24, _store_buffer_medium_head())
    _set_store_buffer_medium_head(node)
    _backend4_store_buffer_medium_set_count(_backend4_store_buffer_medium_count() + 1)
    _backend4_remembered_set_add(owner, slot)
    store_i32(owner, 12, flags | 512)
    pending: int = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_entries_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_entries_count"),
        0,
        pending + 1,
    )
    high_water: int = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_high_water_count"), 0
    )
    if pending + 1 > high_water:
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_high_water_count"),
            0,
            pending + 1,
        )
    owner_fanout: int = _backend4_store_buffer_owner_fanout(owner)
    owner_high_water: int = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_owner_fanout_high_water_count"), 0
    )
    if owner_fanout > owner_high_water:
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_owner_fanout_high_water_count"),
            0,
            owner_fanout,
        )
    owner_count: int = _backend4_store_buffer_owner_count()
    owner_count_high_water: int = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_owner_count_high_water_count"), 0
    )
    if owner_count > owner_count_high_water:
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_owner_count_high_water_count"),
            0,
            owner_count,
        )
    return 1


def _backend4_store_buffer_remove(owner) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    prev = null()
    node = _store_buffer_medium_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 24)
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            if ptr_is_null(prev) != 0:
                _set_store_buffer_medium_head(nxt)
            else:
                store_ptr(prev, 24, nxt)
            _backend4_store_buffer_dec()
            count: int = _backend4_store_buffer_medium_count()
            if count > 0:
                _backend4_store_buffer_medium_set_count(count - 1)
            py_decref(load_ptr(node, 16))
            free(node)
            node = nxt
            continue
        prev = node
        node = nxt
    prev = null()
    node = _store_buffer_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 24)
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            if ptr_is_null(prev) != 0:
                _set_store_buffer_head(nxt)
            else:
                store_ptr(prev, 24, nxt)
            _backend4_store_buffer_dec()
            py_decref(load_ptr(node, 16))
            free(node)
            node = nxt
            continue
        prev = node
        node = nxt


def _backend4_store_buffer_owner_pending(owner) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    node = _store_buffer_medium_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            return 1
        node = load_ptr(node, 24)
    node = _store_buffer_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            return 1
        node = load_ptr(node, 24)
    return 0


def _backend4_store_buffer_owner_fanout(owner) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    node = _store_buffer_medium_head()
    count: int = 0
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            count = count + 1
        node = load_ptr(node, 24)
    node = _store_buffer_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            count = count + 1
        node = load_ptr(node, 24)
    return count


def _backend4_store_buffer_owner_count() -> int:
    head = _store_buffer_medium_head()
    node = head
    count: int = 0
    while ptr_is_null(node) == 0:
        owner = load_ptr(node, 0)
        prev = head
        seen: int = 0
        while ptr_is_null(prev) == 0:
            if ptr_eq(prev, node) != 0:
                break
            if ptr_eq(load_ptr(prev, 0), owner) != 0:
                seen = 1
                break
            prev = load_ptr(prev, 24)
        if seen == 0:
            count = count + 1
        node = load_ptr(node, 24)
    global_head = _store_buffer_head()
    node = global_head
    while ptr_is_null(node) == 0:
        owner = load_ptr(node, 0)
        seen = 0
        prev = head
        while ptr_is_null(prev) == 0:
            if ptr_eq(load_ptr(prev, 0), owner) != 0:
                seen = 1
                break
            prev = load_ptr(prev, 24)
        prev = global_head
        while ptr_is_null(prev) == 0:
            if ptr_eq(prev, node) != 0:
                break
            if ptr_eq(load_ptr(prev, 0), owner) != 0:
                seen = 1
                break
            prev = load_ptr(prev, 24)
        if seen == 0:
            count = count + 1
        node = load_ptr(node, 24)
    return count


def _backend4_store_buffer_entry_count() -> int:
    node = _store_buffer_medium_head()
    count: int = 0
    while ptr_is_null(node) == 0:
        count = count + 1
        node = load_ptr(node, 24)
    node = _store_buffer_head()
    while ptr_is_null(node) == 0:
        count = count + 1
        node = load_ptr(node, 24)
    return count


def _backend4_store_buffer_max_owner_fanout() -> int:
    node = _store_buffer_medium_head()
    max_fanout: int = 0
    while ptr_is_null(node) == 0:
        fanout: int = _backend4_store_buffer_owner_fanout(load_ptr(node, 0))
        if fanout > max_fanout:
            max_fanout = fanout
        node = load_ptr(node, 24)
    node = _store_buffer_head()
    while ptr_is_null(node) == 0:
        fanout: int = _backend4_store_buffer_owner_fanout(load_ptr(node, 0))
        if fanout > max_fanout:
            max_fanout = fanout
        node = load_ptr(node, 24)
    return max_fanout


def _backend4_reset_store_buffer_epoch_state() -> None:
    _object_graph_lock()
    entries: int = _backend4_store_buffer_entry_count()
    owner_fanout: int = _backend4_store_buffer_max_owner_fanout()
    owner_count: int = _backend4_store_buffer_owner_count()
    store_i32(global_addr("pcc_gc_backend4_store_buffer_entries_count"), 0, entries)
    store_i32(global_addr("pcc_gc_backend4_store_buffer_high_water_count"), 0, entries)
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_owner_fanout_high_water_count"),
        0,
        owner_fanout,
    )
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_owner_count_high_water_count"),
        0,
        owner_count,
    )
    _object_graph_unlock()


def _backend4_store_buffer_clear() -> None:
    node = _store_buffer_medium_head()
    _set_store_buffer_medium_head(null())
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 24)
        py_decref(load_ptr(node, 16))
        free(node)
        node = nxt
    _backend4_store_buffer_medium_set_count(0)
    node = _store_buffer_head()
    _set_store_buffer_head(null())
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 24)
        owner = load_ptr(node, 0)
        if _is_known_object(owner) != 0:
            flags: int = load_i32(owner, 12)
            store_i32(owner, 12, flags & ~512)
        py_decref(load_ptr(node, 16))
        free(node)
        node = nxt
    store_i32(global_addr("pcc_gc_backend4_store_buffer_entries_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_store_buffer_high_water_count"), 0, 0)
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_owner_fanout_high_water_count"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_owner_count_high_water_count"), 0, 0
    )
    _backend4_remembered_set_clear()


def _backend4_store_buffer_batch_capacity() -> int:
    return 8


def _backend4_store_buffer_note_max_batch(batch_size: int) -> None:
    max_batch: int = load_i32(
        global_addr("pcc_gc_backend4_store_buffer_max_batch_size_count"), 0
    )
    if batch_size > max_batch:
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_max_batch_size_count"),
            0,
            batch_size,
        )


def _remembered_set_head():
    return global_load_ptr("pcc_gc_backend4_remembered_slots_head")


def _set_remembered_set_head(head) -> None:
    global_store_ptr("pcc_gc_backend4_remembered_slots_head", head)


def _backend4_remembered_set_contains(owner, slot) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    if ptr_is_null(slot) != 0:
        return 0
    node = _remembered_set_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            if ptr_eq(load_ptr(node, 8), slot) != 0:
                return 1
        node = load_ptr(node, 16)
    return 0


def _backend4_owner_remembered_slots(owner) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    node = _remembered_set_head()
    total: int = 0
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            total = total + 1
        node = load_ptr(node, 16)
    return total


def _backend4_zpage_note_remembered_slot(owner, delta: int) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    if delta == 0:
        return
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return
    page = load_ptr(node, 8)
    if ptr_is_null(page) != 0:
        return
    current: int = load_i64(page, 40)
    current = current + delta
    if current < 0:
        current = 0
    store_i64(page, 40, current)


def _backend4_zpage_note_remembered_card(owner, delta: int) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    if delta == 0:
        return
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return
    page = load_ptr(node, 8)
    if ptr_is_null(page) != 0:
        return
    current: int = load_i64(page, 48)
    current = current + delta
    if current < 0:
        current = 0
    store_i64(page, 48, current)


def _backend4_remembered_set_add(owner, slot) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return 0
    if ptr_is_null(slot) != 0:
        return 0
    if _backend4_remembered_set_contains(owner, slot) != 0:
        skips: int = load_i32(
            global_addr("pcc_gc_backend4_remembered_set_duplicate_skips_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_remembered_set_duplicate_skips_count"),
            0,
            skips + 1,
        )
        return 0
    node = malloc(24)
    if ptr_is_null(node) != 0:
        return 0
    store_ptr(node, 0, owner)
    store_ptr(node, 8, slot)
    store_ptr(node, 16, _remembered_set_head())
    _set_remembered_set_head(node)
    entries: int = load_i32(
        global_addr("pcc_gc_backend4_remembered_set_entries_count"), 0
    )
    entries = entries + 1
    store_i32(
        global_addr("pcc_gc_backend4_remembered_set_entries_count"),
        0,
        entries,
    )
    high_water: int = load_i32(
        global_addr("pcc_gc_backend4_remembered_set_high_water_count"), 0
    )
    if entries > high_water:
        store_i32(
            global_addr("pcc_gc_backend4_remembered_set_high_water_count"),
            0,
            entries,
        )
    _backend4_zpage_note_remembered_slot(owner, 1)
    _backend4_zpage_note_remembered_card(owner, 1)
    return 1


def _backend4_remembered_set_remove(owner) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    prev = null()
    node = _remembered_set_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            _backend4_zpage_note_remembered_slot(load_ptr(node, 0), -1)
            _backend4_zpage_note_remembered_card(load_ptr(node, 0), -1)
            if ptr_is_null(prev) != 0:
                _set_remembered_set_head(nxt)
            else:
                store_ptr(prev, 16, nxt)
            entries: int = load_i32(
                global_addr("pcc_gc_backend4_remembered_set_entries_count"), 0
            )
            if entries > 0:
                store_i32(
                    global_addr("pcc_gc_backend4_remembered_set_entries_count"),
                    0,
                    entries - 1,
                )
            free(node)
            node = nxt
            continue
        prev = node
        node = nxt


def _backend4_remembered_set_remove_slot(slot) -> int:
    if ptr_is_null(slot) != 0:
        return 0
    prev = null()
    node = _remembered_set_head()
    removed: int = 0
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if ptr_eq(load_ptr(node, 8), slot) != 0:
            _backend4_zpage_note_remembered_slot(load_ptr(node, 0), -1)
            _backend4_zpage_note_remembered_card(load_ptr(node, 0), -1)
            if ptr_is_null(prev) != 0:
                _set_remembered_set_head(nxt)
            else:
                store_ptr(prev, 16, nxt)
            entries: int = load_i32(
                global_addr("pcc_gc_backend4_remembered_set_entries_count"), 0
            )
            if entries > 0:
                store_i32(
                    global_addr("pcc_gc_backend4_remembered_set_entries_count"),
                    0,
                    entries - 1,
                )
            free(node)
            removed = 1
            node = nxt
            continue
        prev = node
        node = nxt
    return removed


def _backend4_remembered_set_retarget_slot(
    from_owner, to_owner, from_slot, to_slot
) -> None:
    if ptr_is_null(from_owner) != 0 or ptr_is_null(to_owner) != 0:
        return
    if is_tagged_int(from_owner) != 0 or is_tagged_int(to_owner) != 0:
        return
    if ptr_is_null(from_slot) != 0 or ptr_is_null(to_slot) != 0:
        return
    if ptr_eq(from_owner, to_owner) != 0 and ptr_eq(from_slot, to_slot) != 0:
        return
    node = _remembered_set_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), from_owner) != 0:
            if ptr_eq(load_ptr(node, 8), from_slot) != 0:
                _backend4_zpage_note_remembered_slot(load_ptr(node, 0), -1)
                _backend4_zpage_note_remembered_card(load_ptr(node, 0), -1)
                store_ptr(node, 0, to_owner)
                store_ptr(node, 8, to_slot)
                _backend4_zpage_note_remembered_slot(to_owner, 1)
                _backend4_zpage_note_remembered_card(to_owner, 1)
        node = load_ptr(node, 16)


def _backend4_remembered_set_retarget_inline_slot(
    from_owner, to_owner, offset: int
) -> None:
    _backend4_remembered_set_retarget_slot(
        from_owner,
        to_owner,
        ptr_add(from_owner, offset),
        ptr_add(to_owner, offset),
    )


def _retarget_continuation_root_slots(from_slots, from_map, to_slots, to_map) -> None:
    if ptr_is_null(from_slots) != 0 or ptr_is_null(to_slots) != 0:
        return
    if ptr_is_null(to_map) != 0:
        return
    node = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 8), from_slots) != 0:
            if ptr_is_null(from_map) != 0 or ptr_eq(load_ptr(node, 0), from_map) != 0:
                store_ptr(node, 0, to_map)
                store_ptr(node, 8, to_slots)
        node = load_ptr(node, 16)


def _backend4_remembered_set_entry_count() -> int:
    node = _remembered_set_head()
    count: int = 0
    while ptr_is_null(node) == 0:
        count = count + 1
        node = load_ptr(node, 16)
    return count


def _backend4_reset_remembered_set_epoch_state() -> None:
    _object_graph_lock()
    entries: int = _backend4_remembered_set_entry_count()
    store_i32(global_addr("pcc_gc_backend4_remembered_set_entries_count"), 0, entries)
    store_i32(
        global_addr("pcc_gc_backend4_remembered_set_high_water_count"), 0, entries
    )
    _object_graph_unlock()


def _backend4_remembered_set_clear() -> None:
    node = _remembered_set_head()
    _set_remembered_set_head(null())
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        _backend4_zpage_note_remembered_slot(load_ptr(node, 0), -1)
        _backend4_zpage_note_remembered_card(load_ptr(node, 0), -1)
        free(node)
        node = nxt
    store_i32(global_addr("pcc_gc_backend4_remembered_set_entries_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_remembered_set_high_water_count"), 0, 0)


def _is_known_object(o) -> int:
    if ptr_is_null(o):
        return 0
    if is_tagged_int(o):
        return 0
    node = pcc_gc_object_index_find(o)
    if ptr_is_null(node) != 0:
        return 0
    return 1 if _object_node_freeing(node) == 0 else 0


@c_abi_export("pcc_gc_object_is_known_no_lock")
def pcc_gc_object_is_known_no_lock(o) -> int:
    return _is_known_object(o)


# Backend-4 read-barrier safe candidate decision (G-P0-LONGRUN exit UAF).
# Mirrors pcc_gc_backend4_slot_needs_resolve (py_gc_backend.c). Decides
# "does this slot value need relocation resolution?" WITHOUT dereferencing a
# possibly-stale/unmapped slot value's header. Under backend #4 churn a slot
# can hold a freed malloc'd child or an old copy the index/forwarding table
# never mapped; the address heuristic cannot tell that apart from a live
# object, so the raw header load (2048 candidate flag at offset 12) faulted at
# exit-time list dealloc. The object index and forwarding table are
# pointer-VALUE hash lookups (no deref): consult them first and only read the
# header of a proven-mapped (known-live) object.
#   forwarded stale ref -> resolve (no header deref)
#   known-live object   -> safe to read the 2048 candidate flag
#   unknown+unforwarded -> dead pointer leaked into the slot: do NOT deref
@c_abi_export("pcc_gc_backend4_slot_needs_resolve")
def pcc_gc_backend4_slot_needs_resolve(value) -> int:
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return 0
    if ptr_is_null(_forwarding_find(value)) == 0:
        return 1
    if _is_known_object(value) != 0:
        if (load_i32(value, 12) & 2048) != 0:
            return 1
        return 0
    return 0


@c_abi_export("pcc_gc_object_is_known")
def pcc_gc_object_is_known(o) -> int:
    _object_graph_lock()
    known: int = _is_known_object(o)
    _object_graph_unlock()
    return known


def _is_sweep_candidate(o) -> int:
    if ptr_is_null(o):
        return 0
    if is_tagged_int(o):
        return 0
    if _is_known_object(o) == 0:
        return 0
    flags: int = load_i32(o, 12)
    return 1 if (flags & 1024) != 0 else 0


def _has_sweep_candidate() -> int:
    _object_graph_lock()
    node = _object_head()
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) == 0:
            node = _object_node_next(node)
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        if (flags & 1024) != 0:
            _object_graph_unlock()
            return 1
        node = _object_node_next(node)
    _object_graph_unlock()
    return 0


def _clear_slot(slot_base, slot_offset: int) -> None:
    child = load_ptr(slot_base, slot_offset)
    store_ptr(slot_base, slot_offset, null())
    if ptr_is_null(child) or is_tagged_int(child):
        return
    if _is_sweep_candidate(child) != 0:
        return
    py_decref(child)


def _clear_referents(o) -> None:
    if ptr_is_null(o) or is_tagged_int(o):
        return
    tag: int = load_i32(o, 8)
    if _py_obj_has_no_pointer_slots(o) != 0:
        return
    _py_obj_visit_covered_slots(o, 5, 0)  # _PY_OBJ_VISIT_CLEAR
    if tag == 5:  # PY_TYPE_LIST
        store_i64(o, 16, 0)
    elif tag == 7:  # PY_TYPE_TUPLE
        store_i64(o, 16, 0)
    elif tag == 6:  # PY_TYPE_DICT
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                store_i64(entries, off, 0)
                i = i + 1
        indices = load_ptr(o, 32)
        if ptr_is_null(indices) == 0:
            cap: int = load_i64(o, 24)
            i = 0
            while i < cap:
                store_i64(indices, i * 8, -1)
                i = i + 1
        store_i64(o, 16, 0)
        store_i64(o, 48, 0)
    elif tag == 8:  # PY_TYPE_SET
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            cap: int = load_i64(o, 24)
            i: int = 0
            while i < cap:
                store_ptr(entries, i * 16 + 8, null())
                store_i64(entries, i * 16, 0)
                i = i + 1
        store_i64(o, 16, 0)
        store_i64(o, 32, 0)


def _clear_unreachable(o) -> None:
    # PASS-1 of the two-phase sweep: invalidate weakrefs and clear this
    # unreachable object's referent slots, WITHOUT freeing it and WITHOUT
    # clearing its 1024 (unreachable) flag. Keeping every pending-sweep object
    # flagged is what makes _clear_slot's _is_sweep_candidate guard skip the
    # decref of sibling cycle members — the old single-pass clear+free freed
    # an object mid-sweep, so a later object's _clear_slot saw the already-freed
    # target as a non-candidate and decref'd it -> refcount underflow
    # (BAD_INCREF tag=-1). See investigation
    # gc-5backend-object-lifetime-contract-no-libpython.md.
    if ptr_is_null(o) or is_tagged_int(o):
        return
    py_weakref_invalidate(o)
    _clear_referents(o)


def _finish_delayed_zpage_freeing_note(o, delayed: int) -> None:
    if delayed != 0:
        pcc_gc_note_object_freeing(o)


def _finalize_unreachable(o) -> None:
    # PASS-2 of the two-phase sweep: free an object whose referents were ALREADY
    # cleared by _clear_unreachable. Must not be called before every pending
    # object has been cleared (see _clear_unreachable / _sweep_unreachable).
    if ptr_is_null(o) or is_tagged_int(o):
        return
    backend: int = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    flags: int = load_i32(o, 12)
    delay_zpage_freeing_note: int = 0
    if backend == 4 and (flags & 65536) != 0:
        delay_zpage_freeing_note = 1
    # Only actual finalization publishes this bit; zero-count forwarding
    # shells remain active until their normal retirement path.
    store_i32(o, 12, flags | 524288)
    if delay_zpage_freeing_note == 0:
        pcc_gc_note_object_freeing(o)
    pcc_refcount_forget(o)
    py_gc_untrack(o)
    store_i64(o, 0, 0)
    tag: int = load_i32(o, 8)
    if tag == 2:
        py_dealloc_int(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 3:
        py_dealloc_float(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 4:
        py_dealloc_str(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 5:
        py_dealloc_list(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 7:
        py_dealloc_tuple(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 6:
        py_dealloc_dict(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 8:
        py_dealloc_set(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 9:
        py_dealloc_func(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 10:
        py_class_dealloc(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 11:
        py_instance_dealloc(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 12:
        py_dealloc_exc(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 13:
        py_dealloc_file(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 14:
        py_dealloc_iter(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 15:
        py_dealloc_gen(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 20:
        py_dealloc_coroutine(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 29:
        py_dealloc_continuation(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 19:
        py_dealloc_memoryview(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 21:
        py_dealloc_weakref(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 22:
        py_dealloc_thread_lock(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 23:
        py_dealloc_thread_rlock(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 24:
        py_dealloc_thread_event(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 25:
        py_dealloc_thread_condition(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 26:
        py_dealloc_thread_semaphore(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 27:
        py_dealloc_thread_thread(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 28:
        py_dealloc_task(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag == 30:
        py_dealloc_virtual_thread(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if pcc_capi_dealloc_cext_object(o, tag) != 0:
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    if tag >= 104:
        py_instance_dealloc(o)
        _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)
        return
    py_dealloc_generic(o)
    _finish_delayed_zpage_freeing_note(o, delay_zpage_freeing_note)


def _recheck_reachability_after_finalizers() -> None:
    # PEP 442 reachability recheck. A __del__ dispatched in PASS 0 may have
    # RESURRECTED an unreachable object — stored it somewhere a root can reach
    # (e.g. appended `self` to a module-global list). Such an object must NOT be
    # cleared/freed, else we clear+free a live object: heap corruption /
    # double-free on #1/#2 (keeper still points at the freed block) or a cleared
    # field -> AttributeError on #3/#4.
    #
    # Re-mark from roots (seed whitens all but PRESERVES the 1024 sweep-candidate
    # flag; gray roots; drain transitively, so the trace propagates through the
    # mutated container to the resurrected object). Any object that is STILL a
    # sweep candidate (1024) but is now reachable (no longer white / flag 8) was
    # resurrected -> clear its 1024 flag so PASS 1/PASS 2 skip it. Objects still
    # unreachable stay white|1024 and are reclaimed exactly as before, so the
    # non-resurrection path is unchanged by construction. (Cost: a second mark
    # over the heap per collect that has candidates; a targeted recheck over only
    # the unreachable set would be cheaper — tracked as a follow-up optimization.
    # Correctness first.) See gc-5backend-finalizer-resurrection-no-libpython.md.
    _seed_roots()
    _drain_all_gray_unlocked()
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = _object_node_next(node)
        if _object_node_is_active(node) != 0:
            o = load_ptr(node, 0)
            flags: int = load_i32(o, 12)
            if (flags & 1024) != 0 and (flags & 8) == 0:
                store_i32(o, 12, flags & ~1024)
        node = nxt


def _sweep_unreachable(budget: int) -> int:
    if budget <= 0:
        return 0
    # PASS 0 (CPython PEP 442): run __del__ finalizers on the unreachable
    # members BEFORE any clear/free, while their fields are still intact, so a
    # cycle member's __del__ sees the real object state (e.g. `self.name`).
    # py_user_del_dispatch runs __del__ at most once and sets PY_FLAG_FINALIZED,
    # so the PASS-2 dealloc (py_instance_dealloc -> py_user_del_dispatch) does
    # not re-run it. Without this the tracing collect reclaimed finalizer-bearing
    # cycle members without running their __del__ (the dispatch happened only in
    # PASS 2, AFTER PASS 1 had cleared their fields). See investigation
    # gc-5backend-cycle-finalizer-not-run-no-libpython.md.
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = _object_node_next(node)
        if _object_node_is_active(node) != 0:
            o = load_ptr(node, 0)
            flags: int = load_i32(o, 12)
            if (flags & 1024) != 0 and (flags & (64 | 16384)) == 0:
                tag: int = load_i32(o, 8)
                if pcc_capi_is_cext_type_tag(tag) == 0:
                    py_user_del_dispatch(o)
        node = nxt
    # PEP 442: after finalizers, exclude any object a __del__ resurrected from
    # the clear/free passes below (clears its 1024 flag if now reachable).
    _recheck_reachability_after_finalizers()
    # Two-phase sweep (CPython-style clear-then-free): PASS 1 clears the
    # referents of up to `budget` unreachable objects WITHOUT freeing any, so
    # while cycles are being broken every still-pending object keeps its 1024
    # flag and _clear_slot's _is_sweep_candidate guard correctly skips the
    # decref of sibling cycle members. PASS 2 then frees the SAME objects (same
    # object-list order + 1024 filter, flags unchanged by PASS 1). Freeing
    # interleaved with clearing (the old single pass) caused a use-after-free /
    # refcount underflow when one unreachable object referenced another that had
    # already been finalized. See investigation
    # gc-5backend-object-lifetime-contract-no-libpython.md.
    cleared: int = 0
    node = _object_head()
    while ptr_is_null(node) == 0 and cleared < budget:
        nxt = _object_node_next(node)
        if _object_node_is_active(node) == 0:
            node = nxt
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        if (flags & 1024) != 0 and (flags & (64 | 16384)) == 0:
            _clear_unreachable(o)
            cleared = cleared + 1
        node = nxt
    reclaimed: int = 0
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = _object_node_next(node)
        if _object_node_is_active(node) == 0:
            node = nxt
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        if (flags & 1024) != 0:
            if (flags & (64 | 16384)) != 0:
                store_i32(o, 12, flags & ~1024)
            elif reclaimed < cleared:
                _finalize_unreachable(o)
                reclaimed = reclaimed + 1
        node = nxt
    return reclaimed


def _mark_gray_if_known(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    forwarding = _forwarding_find(o)
    if ptr_is_null(forwarding) == 0:
        resolved = load_ptr(forwarding, 8)
        if ptr_is_null(resolved) == 0:
            if ptr_eq(resolved, o) == 0:
                o = resolved
    if _is_known_object(o) == 0:
        return
    flags: int = load_i32(o, 12)
    if (flags & 32) == 0:
        if (flags & 16) == 0:
            _inc_gray_count()
        store_i32(o, 12, (flags & ~56) | 16)


def _mark_root_gray_if_known(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    forwarding = _forwarding_find(o)
    if ptr_is_null(forwarding) == 0:
        resolved = load_ptr(forwarding, 8)
        if ptr_is_null(resolved) == 0:
            if ptr_eq(resolved, o) == 0:
                o = resolved
    if _is_known_object(o) == 0:
        return
    flags: int = load_i32(o, 12)
    if (flags & 16) == 0:
        _inc_gray_count()
    store_i32(o, 12, (flags & ~56) | 16)


def _mark_forwarded_source_inactive(from_obj) -> None:
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return
    node = pcc_gc_object_index_find(from_obj)
    if ptr_is_null(node) != 0:
        return
    if _object_node_freeing(node) != 0:
        return
    _live_bytes_subtract(_object_node_size(node))
    _backend4_zpage_remove(from_obj)
    _set_object_node_freeing(node, 1)


def _generational_oldify_copy(from_obj):
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 3:
        return null()
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return null()
    if _is_known_object(from_obj) == 0:
        existing_unknown = _forwarding_find(from_obj)
        if ptr_is_null(existing_unknown) == 0:
            return load_ptr(existing_unknown, 8)
        return null()
    flags: int = load_i32(from_obj, 12)
    existing = _forwarding_find(from_obj)
    if ptr_is_null(existing) == 0:
        target = load_ptr(existing, 8)
        if ptr_is_null(target) == 0:
            return target

    if (flags & 128) == 0:
        return null()
    if (flags & 64) != 0:
        return null()
    tag: int = load_i32(from_obj, 8)
    if _relocate_copy_supported_tag(tag) == 0:
        return null()
    size: int = _object_known_size(from_obj)
    if size < 16:
        return null()

    to_obj = malloc(size)
    if ptr_is_null(to_obj) != 0:
        return null()
    memmove(to_obj, from_obj, size)
    store_i64(to_obj, 0, 1)
    new_flags: int = load_i32(to_obj, 12)
    store_i32(to_obj, 12, (new_flags & ~(128 | 4096 | 512 | 2048)) | 256)
    if _relocate_copy_payload(from_obj, to_obj, tag, size) == 0:
        py_decref(to_obj)
        return null()
    store_i64(to_obj, 0, 0)

    node = _object_node_alloc()
    if ptr_is_null(node) != 0:
        free(to_obj)
        return null()
    old_head = _object_head()
    store_ptr(node, 0, to_obj)
    store_i64(node, 8, size)
    store_ptr(node, 16, old_head)
    store_ptr(node, 24, null())
    store_i64(node, 32, 0)
    store_ptr(node, 40, null())
    store_ptr(node, 48, null())
    _set_object_node_gc_refs(node, 0)
    if ptr_is_null(old_head) == 0:
        _set_object_node_prev(old_head, node)
    _set_object_head(node)
    if pcc_gc_object_index_insert(to_obj, node) < 0:
        _unlink_object_node(node)
        _object_node_release(node)
        free(to_obj)
        return null()
    live: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
    store_i32(global_addr("pcc_gc_live_bytes"), 0, live + size)

    if _install_forwarding_unlocked(from_obj, to_obj) != 0:
        pcc_gc_object_index_remove(to_obj)
        _unlink_object_node(node)
        _object_node_release(node)
        live2: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
        if size >= live2:
            store_i32(global_addr("pcc_gc_live_bytes"), 0, 0)
        else:
            store_i32(global_addr("pcc_gc_live_bytes"), 0, live2 - size)
        _identity_remove(to_obj)
        free(to_obj)
        return null()

    _mark_forwarded_source_inactive(from_obj)
    source_flags: int = load_i32(from_obj, 12)
    store_i32(from_obj, 12, (source_flags & ~128) | 256)
    return to_obj


def _promote_young_if_known(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    if _is_known_object(o) == 0:
        return
    flags: int = load_i32(o, 12)
    if (flags & 128) != 0:
        oldified = _generational_oldify_copy(o)
        if ptr_is_null(oldified) == 0:
            _trace_referents_for_promotion(oldified)
            return
        backend: int = _init_config()
        if backend == 3 and (load_i32(o, 12) & 4096) != 0:
            promoted_flags: int = load_i32(o, 12)
            store_i32(o, 12, (promoted_flags & ~(128 | 512)) | 256)
            _trace_referents_for_promotion(o)
            return
        store_i32(o, 12, (flags & ~128) | 256)
        if backend == 4:
            _backend4_zpage_note_owner_promoted(o)
        if backend == 3:
            _trace_referents_for_promotion(o)


def _promote_young_slot_mode(slot_base, slot_offset: int, recurse: int) -> None:
    child = load_ptr(slot_base, slot_offset)
    if ptr_is_null(child) != 0:
        return
    if is_tagged_int(child) != 0:
        return
    if not _gc_ptr_can_have_header(child):
        return
    if _is_known_object(child) == 0 and ptr_is_null(_forwarding_find(child)) != 0:
        return
    child_flags: int = load_i32(child, 12)
    if (child_flags & (128 | 2048)) == 0:
        return
    oldified = _generational_oldify_copy(child)
    if ptr_is_null(oldified) == 0:
        if ptr_eq(oldified, child) == 0:
            py_incref(oldified)
            store_ptr(slot_base, slot_offset, oldified)
            _trace_referents_for_promotion(oldified)
            py_decref(child)
            return
    if recurse == 0:
        return
    _promote_young_if_known(child)


def _promote_young_slot(slot_base, slot_offset: int) -> None:
    _promote_young_slot_mode(slot_base, slot_offset, 1)


def _promote_young_borrowed_slot_mode(
    slot_base,
    slot_offset: int,
    recurse: int,
) -> None:
    child = load_ptr(slot_base, slot_offset)
    if ptr_is_null(child) != 0:
        return
    if is_tagged_int(child) != 0:
        return
    if not _gc_ptr_can_have_header(child):
        return
    if _is_known_object(child) == 0 and ptr_is_null(_forwarding_find(child)) != 0:
        return
    child_flags: int = load_i32(child, 12)
    if (child_flags & (128 | 2048)) == 0:
        return
    oldified = _generational_oldify_copy(child)
    if ptr_is_null(oldified) == 0:
        if ptr_eq(oldified, child) == 0:
            store_ptr(slot_base, slot_offset, oldified)
            _trace_referents_for_promotion(oldified)
            return
    if recurse == 0:
        return
    _promote_young_if_known(child)


def _promote_young_borrowed_slot(slot_base, slot_offset: int) -> None:
    _promote_young_borrowed_slot_mode(slot_base, slot_offset, 1)


def _root_slot_value_is_stable(value) -> int:
    if ptr_is_null(value) != 0:
        return 1
    if is_tagged_int(value) != 0:
        return 1
    if not _gc_ptr_can_have_header(value):
        return 1
    if _is_known_object(value) == 0:
        if ptr_is_null(_forwarding_find(value)) != 0:
            return 1
        return 0
    flags: int = load_i32(value, 12)
    if (flags & (128 | 2048)) == 0:
        return 1
    return 0


def _promote_cached_frame_slot(
    slot_base,
    slot_offset: int,
    stable_base,
    borrowed: int,
) -> None:
    before = load_ptr(slot_base, slot_offset)
    if ptr_is_null(stable_base) == 0:
        if ptr_eq(load_ptr(stable_base, slot_offset), before) != 0:
            return
    if borrowed != 0:
        _promote_young_borrowed_slot(slot_base, slot_offset)
    else:
        _promote_young_slot(slot_base, slot_offset)
    if ptr_is_null(stable_base) != 0:
        return
    after = load_ptr(slot_base, slot_offset)
    if _root_slot_value_is_stable(after) != 0:
        store_ptr(stable_base, slot_offset, after)
    else:
        store_ptr(stable_base, slot_offset, null())


def _gray_exists() -> int:
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if load_i64(node, 32) != 0:
            node = nxt
            continue
        o = load_ptr(node, 0)
        if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
            node = nxt
            continue
        flags: int = load_i32(o, 12)
        if (flags & 16) != 0:
            return 1
        node = nxt
    return 0


def _resolve_root_slot_unlocked(slot_base, slot_offset: int):
    value = load_ptr(slot_base, slot_offset)
    if ptr_is_null(value) != 0:
        return value
    if is_tagged_int(value) != 0:
        return value
    if _is_known_object(value) == 0:
        forwarding_unknown = _forwarding_find(value)
        if ptr_is_null(forwarding_unknown) == 0:
            resolved_unknown = load_ptr(forwarding_unknown, 8)
            if ptr_is_null(resolved_unknown) == 0:
                if ptr_eq(resolved_unknown, value) == 0:
                    if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4:
                        store_ptr(slot_base, slot_offset, resolved_unknown)
                        return resolved_unknown
                    py_incref(resolved_unknown)
                    store_ptr(slot_base, slot_offset, resolved_unknown)
                    py_decref(value)
                    return resolved_unknown
        return value
    flags: int = load_i32(value, 12)
    if (flags & 2048) == 0:
        return value
    forwarding = _forwarding_find(value)
    if ptr_is_null(forwarding) != 0:
        store_i32(value, 12, flags & ~2048)
        return value
    resolved = load_ptr(forwarding, 8)
    if ptr_is_null(resolved) != 0:
        store_i32(value, 12, flags & ~2048)
        return value
    if ptr_eq(resolved, value) != 0:
        store_i32(value, 12, flags & ~2048)
        return value
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4:
        store_ptr(slot_base, slot_offset, resolved)
        return resolved
    py_incref(resolved)
    store_ptr(slot_base, slot_offset, resolved)
    py_decref(value)
    return resolved


def _mapped_root_count(frame_map) -> int:
    if ptr_is_null(frame_map) != 0:
        return 0
    root_count: int = load_i32(frame_map, 0)
    if root_count == -2147483648:
        return 0
    if root_count < 0:
        root_count = 0 - root_count
    if root_count > 100000:
        return 0
    return root_count


def _mapped_roots_are_borrowed(frame_map) -> int:
    if ptr_is_null(frame_map) != 0:
        return 0
    root_count: int = load_i32(frame_map, 0)
    if root_count < 0:
        return 1
    return 0


def _py_visit_mapped_root_slot(
    slot_base,
    slot_offset: int,
    stable_base,
    borrowed: int,
    mode: int,
    resolve: int,
) -> int:
    if mode == 1:  # _PY_ROOT_VISIT_GRAY
        if resolve != 0:
            _mark_root_gray_if_known(
                _resolve_root_slot_unlocked(slot_base, slot_offset)
            )
        else:
            _mark_root_gray_if_known(load_ptr(slot_base, slot_offset))
        return 0
    if mode == 2:  # _PY_ROOT_VISIT_PROMOTE
        _promote_cached_frame_slot(slot_base, slot_offset, stable_base, borrowed)
        return 0
    if mode == 3:  # _PY_ROOT_VISIT_REWRITE
        before = load_ptr(slot_base, slot_offset)
        after = _resolve_root_slot_unlocked(slot_base, slot_offset)
        if ptr_eq(before, after) == 0:
            return 1
        return 0
    return 0


def _py_visit_mapped_root_slots(
    root_count: int,
    root_slots,
    stable_values,
    borrowed: int,
    mode: int,
    resolve: int,
) -> int:
    if root_count <= 0 or ptr_is_null(root_slots) != 0:
        return 0
    result: int = 0
    i: int = 0
    while i < root_count:
        if mode == 3:  # _PY_ROOT_VISIT_REWRITE
            result += _py_visit_mapped_root_slot(
                root_slots,
                i * 8,
                stable_values,
                borrowed,
                mode,
                resolve,
            )
        else:
            _py_visit_mapped_root_slot(
                root_slots,
                i * 8,
                stable_values,
                borrowed,
                mode,
                resolve,
            )
        i = i + 1
    if mode == 3:  # _PY_ROOT_VISIT_REWRITE
        return result
    return root_count


def _py_visit_scheduler_root_slots(mode: int, resolve: int) -> int:
    result: int = 0
    node = global_load_ptr("pcc_gc_scheduler_root_head")
    while ptr_is_null(node) == 0:
        slot = load_ptr(node, 0)
        if ptr_is_null(slot) == 0:
            if mode == 3:  # _PY_ROOT_VISIT_REWRITE
                result += _py_visit_mapped_root_slot(
                    slot,
                    0,
                    null(),
                    0,
                    mode,
                    resolve,
                )
            else:
                _py_visit_mapped_root_slot(slot, 0, null(), 0, mode, resolve)
                result = result + 1
        node = load_ptr(node, 8)
    return result


def _py_visit_builtin_exception_cache_slots(mode: int, resolve: int) -> int:
    return _py_visit_mapped_root_slots(
        22,
        global_addr("py_exc_classes"),
        null(),
        0,
        mode,
        resolve,
    )


def _gray_mapped_roots(frame_map, root_slots, resolve: int) -> int:
    root_count: int = _mapped_root_count(frame_map)
    borrowed: int = _mapped_roots_are_borrowed(frame_map)
    return _py_visit_mapped_root_slots(
        root_count,
        root_slots,
        null(),
        borrowed,
        1,  # _PY_ROOT_VISIT_GRAY
        resolve,
    )


def _rewrite_mapped_roots(frame_map, root_slots) -> int:
    root_count: int = _mapped_root_count(frame_map)
    borrowed: int = _mapped_roots_are_borrowed(frame_map)
    return _py_visit_mapped_root_slots(
        root_count,
        root_slots,
        null(),
        borrowed,
        3,  # _PY_ROOT_VISIT_REWRITE
        0,
    )


def _gray_current_roots() -> None:
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if load_i64(node, 32) != 0:
            node = nxt
            continue
        o = load_ptr(node, 0)
        if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
            node = nxt
            continue
        flags: int = load_i32(o, 12)
        if (flags & 64) != 0:
            _mark_root_gray_if_known(o)
        node = nxt

    frame = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(frame) == 0:
        _py_visit_mapped_root_slots(
            load_i64(frame, 40),
            load_ptr(frame, 8),
            null(),
            load_i32(frame, 48) & 1,
            1,  # _PY_ROOT_VISIT_GRAY
            1,
        )
        frame = load_ptr(frame, 16)

    cont = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(cont) == 0:
        _py_visit_mapped_root_slots(
            load_i64(cont, 24),
            load_ptr(cont, 8),
            null(),
            load_i32(cont, 32),
            1,  # _PY_ROOT_VISIT_GRAY
            1,
        )
        cont = load_ptr(cont, 16)

    _py_visit_scheduler_root_slots(1, 1)
    _py_visit_builtin_exception_cache_slots(1, 1)


def _gray_refcount_external_roots() -> None:
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = _object_node_next(node)
        if _object_node_is_active(node) != 0:
            _set_object_node_gc_refs(node, load_i64(load_ptr(node, 0), 0))
        node = nxt

    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = _object_node_next(node)
        if _object_node_is_active(node) != 0:
            _subtract_referent_refs(load_ptr(node, 0))
        node = nxt

    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = _object_node_next(node)
        if _object_node_is_active(node) != 0:
            if _object_node_gc_refs(node) > 0:
                _mark_root_gray_if_known(load_ptr(node, 0))
        node = nxt


def _seed_roots() -> None:
    explicit_collect: int = load_i32(
        global_addr("pcc_gc_explicit_collect_active"),
        0,
    )
    _set_gray_count(0)
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if load_i64(node, 32) != 0:
            node = nxt
            continue
        o = load_ptr(node, 0)
        if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
            node = nxt
            continue
        flags: int = load_i32(o, 12)
        if (flags & 16384) != 0 and explicit_collect == 0:
            store_i32(o, 12, (flags & ~(56 | 16384)) | 32)
        else:
            store_i32(o, 12, (flags & ~(56 | 16384)) | 8)
        node = nxt
    _gray_refcount_external_roots()
    _gray_current_roots()


def _drain_all_gray_unlocked() -> int:
    processed: int = 0
    while True:
        local_processed: int = 0
        node = _object_head()
        while ptr_is_null(node) == 0:
            nxt = load_ptr(node, 16)
            if load_i64(node, 32) != 0:
                node = nxt
                continue
            o = load_ptr(node, 0)
            if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
                node = nxt
                continue
            flags: int = load_i32(o, 12)
            if (flags & 16) != 0:
                _trace_referents(o)
                _dec_gray_count()
                store_i32(o, 12, (flags & ~56) | 32)
                local_processed = local_processed + 1
                processed = processed + 1
            node = nxt
        if local_processed == 0:
            break
    return processed


def _begin_mark_cycle() -> None:
    _seed_roots()
    store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 0)
    _set_trace_cursor(_object_head())
    if _gray_count() == 0:
        _set_trace_cursor(null())
        store_i32(global_addr("pcc_gc_mark_active"), 0, 0)


def _finish_tracing_cycle() -> int:
    stw: int = pcc_stop_the_world()
    if stw != 0:
        return 0
    _gray_current_roots()
    _drain_all_gray_unlocked()
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if load_i64(node, 32) != 0:
            pcc_thread_safepoint()
            node = nxt
            continue
        o = load_ptr(node, 0)
        if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
            pcc_thread_safepoint()
            node = nxt
            continue
        flags: int = load_i32(o, 12)
        if (flags & 8) != 0:
            store_i32(o, 12, flags | 1024)
        else:
            store_i32(o, 12, flags & ~1024)
        pcc_thread_safepoint()
        node = nxt
    if stw == 0:
        pcc_resume_world()
    return 1


def _py_obj_visit_slot(
    slot_base,
    slot_offset: int,
    role: int,
    mode: int,
    recurse: int,
) -> None:
    if mode == 1:  # _PY_OBJ_VISIT_TRACE
        if role != 3:  # _PY_OBJ_SLOT_BORROWED_UPDATE_ONLY
            child = pcc_gc_load_ptr_extern(
                null(),
                ptr_add(slot_base, slot_offset),
            )
            _mark_gray_if_known(child)
        return
    if mode == 2:  # _PY_OBJ_VISIT_PROMOTE
        if role == 1:  # _PY_OBJ_SLOT_OWNED
            _promote_young_slot_mode(slot_base, slot_offset, recurse)
        else:
            _promote_young_borrowed_slot_mode(slot_base, slot_offset, recurse)
        return
    if mode == 3:  # _PY_OBJ_VISIT_UPDATE
        _remap_heal_slot(slot_base, slot_offset)
        return
    if mode == 4:  # _PY_OBJ_VISIT_SUBTRACT
        if role != 3:  # _PY_OBJ_SLOT_BORROWED_UPDATE_ONLY
            child = pcc_gc_load_ptr_extern(
                null(),
                ptr_add(slot_base, slot_offset),
            )
            _subtract_known_child_ref(child)
        return
    if mode == 5:  # _PY_OBJ_VISIT_CLEAR
        if role == 1:  # _PY_OBJ_SLOT_OWNED
            _clear_slot(slot_base, slot_offset)
        return
    if mode == 6:  # _PY_OBJ_VISIT_RELOCATE_COUNT
        ctx = global_load_ptr("pcc_gc_relocate_slot_pairs_ctx")
        if ptr_is_null(ctx) == 0:
            store_i64(ctx, 24, load_i64(ctx, 24) + 1)
        return
    if mode == 7:  # _PY_OBJ_VISIT_RELOCATE_FROM
        ctx = global_load_ptr("pcc_gc_relocate_slot_pairs_ctx")
        if ptr_is_null(ctx) != 0:
            return
        index: int = load_i64(ctx, 32)
        count: int = load_i64(ctx, 24)
        if index >= count:
            store_i32(ctx, 40, 0)
            return
        entries = load_ptr(ctx, 16)
        entry = ptr_add(entries, index * 24)
        slot = ptr_add(slot_base, slot_offset)
        store_ptr(entry, 0, slot)
        store_i32(entry, 8, role)
        from_obj = load_ptr(ctx, 0)
        to_obj = load_ptr(ctx, 8)
        inline_offset: int = ptr_diff(slot, from_obj)
        object_size: int = load_i64(ctx, 48)
        if role == 1 and inline_offset >= 0 and inline_offset + 8 <= object_size:
            store_ptr(to_obj, inline_offset, null())
        store_i64(ctx, 32, index + 1)
        return
    if mode == 8:  # _PY_OBJ_VISIT_RELOCATE_TO
        ctx = global_load_ptr("pcc_gc_relocate_slot_pairs_ctx")
        if ptr_is_null(ctx) != 0:
            return
        index: int = load_i64(ctx, 32)
        count: int = load_i64(ctx, 24)
        if index >= count:
            store_i32(ctx, 40, 0)
            return
        entries = load_ptr(ctx, 16)
        entry = ptr_add(entries, index * 24)
        if load_i32(entry, 8) != role:
            store_i32(ctx, 40, 0)
        store_ptr(entry, 16, ptr_add(slot_base, slot_offset))
        store_i64(ctx, 32, index + 1)
        return


def _py_obj_visit_core_container_owner_slots(o, mode: int, recurse: int) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if tag == 5:  # PY_TYPE_LIST
        length: int = load_i64(o, 16)
        items = load_ptr(o, 32)
        if ptr_is_null(items) == 0:
            i: int = 0
            while i < length:
                _py_obj_visit_slot(items, i * 8, 1, mode, recurse)
                i = i + 1
        return 1
    if tag == 7:  # PY_TYPE_TUPLE
        length: int = load_i64(o, 16)
        i: int = 0
        while i < length:
            _py_obj_visit_slot(o, 24 + i * 8, 1, mode, recurse)
            i = i + 1
        return 1
    if tag == 6:  # PY_TYPE_DICT
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                key = load_ptr(entries, off + 8)
                if ptr_is_null(key) == 0:
                    _py_obj_visit_slot(entries, off + 8, 1, mode, recurse)
                    _py_obj_visit_slot(entries, off + 16, 1, mode, recurse)
                i = i + 1
        return 1
    if tag == 8:  # PY_TYPE_SET
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            dummy = global_load_ptr("py_set_dummy")
            capacity: int = load_i64(o, 24)
            i: int = 0
            while i < capacity:
                key = load_ptr(entries, i * 16 + 8)
                if ptr_is_null(key) == 0:
                    if ptr_eq(key, dummy) == 0:
                        _py_obj_visit_slot(entries, i * 16 + 8, 1, mode, recurse)
                i = i + 1
        return 1
    return 0


def _py_obj_visit_fixed_owner_slots(o, mode: int, recurse: int) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if tag == 9:  # PY_TYPE_FUNC
        _py_obj_visit_slot(o, 24, 1, mode, recurse)
        _py_obj_visit_slot(o, 32, 1, mode, recurse)
        _py_obj_visit_slot(o, 40, 1, mode, recurse)
        _py_obj_visit_slot(o, 64, 1, mode, recurse)
        _py_obj_visit_slot(o, 80, 1, mode, recurse)
        _py_obj_visit_slot(o, 88, 1, mode, recurse)
        return 1
    if tag == 14:  # PY_TYPE_ITER
        _py_obj_visit_slot(o, 16, 1, mode, recurse)
        return 1
    if tag == 15:  # PY_TYPE_GEN
        _py_obj_visit_slot(o, 24, 1, mode, recurse)
        _py_obj_visit_slot(o, 48, 1, mode, recurse)
        return 1
    if tag == 20:  # PY_TYPE_COROUTINE
        _py_obj_visit_slot(o, 32, 1, mode, recurse)
        _py_obj_visit_slot(o, 40, 1, mode, recurse)
        _py_obj_visit_slot(o, 48, 1, mode, recurse)
        return 1
    if tag == 28:  # PY_TYPE_TASK
        _py_obj_visit_slot(o, 16, 1, mode, recurse)
        _py_obj_visit_slot(o, 24, 1, mode, recurse)
        _py_obj_visit_slot(o, 32, 1, mode, recurse)
        return 1
    if tag == 30:  # PY_TYPE_VIRTUAL_THREAD
        _py_obj_visit_slot(o, 16, 1, mode, recurse)
        _py_obj_visit_slot(o, 24, 1, mode, recurse)
        return 1
    if tag == 12:  # PY_TYPE_EXC
        _py_obj_visit_slot(o, 16, 1, mode, recurse)
        _py_obj_visit_slot(o, 24, 1, mode, recurse)
        _py_obj_visit_slot(o, 32, 1, mode, recurse)
        _py_obj_visit_slot(o, 40, 1, mode, recurse)
        return 1
    if tag == 101:  # PY_TYPE_PROPERTY
        _py_obj_visit_slot(o, 16, 1, mode, recurse)
        _py_obj_visit_slot(o, 24, 1, mode, recurse)
        _py_obj_visit_slot(o, 32, 1, mode, recurse)
        return 1
    if tag == 102:  # PY_TYPE_CLASSMETHOD
        _py_obj_visit_slot(o, 16, 1, mode, recurse)
        return 1
    if tag == 103:  # PY_TYPE_STATICMETHOD
        _py_obj_visit_slot(o, 16, 1, mode, recurse)
        return 1
    if tag == 19:  # PY_TYPE_MEMORYVIEW
        _py_obj_visit_slot(o, 16, 1, mode, recurse)
        return 1
    if tag == 27:  # PY_TYPE_THREAD
        _py_obj_visit_slot(o, 24, 1, mode, recurse)
        _py_obj_visit_slot(o, 32, 1, mode, recurse)
        _py_obj_visit_slot(o, 40, 1, mode, recurse)
        return 1
    return 0


def _py_obj_visit_weakref_slots(o, mode: int, recurse: int) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if tag != 21:  # PY_TYPE_WEAKREF
        return 0
    _py_obj_visit_slot(
        o,
        16,
        3,  # _PY_OBJ_SLOT_BORROWED_UPDATE_ONLY
        mode,
        recurse,
    )
    _py_obj_visit_slot(o, 24, 1, mode, recurse)  # _PY_OBJ_SLOT_OWNED
    return 1


def _py_obj_visit_continuation_owner_slots(o, mode: int, recurse: int) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if tag != 29:  # PY_TYPE_CONTINUATION
        return 0
    chunk = load_ptr(o, 24)
    if ptr_is_null(chunk) == 0:
        slots = load_ptr(chunk, 16)
        if ptr_is_null(slots) == 0:
            count: int = load_i64(chunk, 8)
            i: int = 0
            while i < count:
                _py_obj_visit_slot(slots, i * 8, 1, mode, recurse)
                i = i + 1
    return 1


def _py_obj_visit_class_slots(o, mode: int, recurse: int) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if tag != 10:  # PY_TYPE_CLASS
        return 0
    n_bases: int = load_i32(o, 24)
    bases = load_ptr(o, 32)
    if ptr_is_null(bases) == 0:
        i: int = 0
        while i < n_bases:
            _py_obj_visit_slot(
                bases,
                i * 8,
                2,  # _PY_OBJ_SLOT_BORROWED_TRACED
                mode,
                recurse,
            )
            i = i + 1
    n_mro: int = load_i32(o, 40)
    mro = load_ptr(o, 48)
    if ptr_is_null(mro) == 0:
        j: int = 0
        while j < n_mro:
            _py_obj_visit_slot(
                mro,
                j * 8,
                2,  # _PY_OBJ_SLOT_BORROWED_TRACED
                mode,
                recurse,
            )
            j = j + 1
    n_methods: int = load_i32(o, 56)
    methods = load_ptr(o, 64)
    if ptr_is_null(methods) == 0:
        k: int = 0
        while k < n_methods:
            _py_obj_visit_slot(
                methods,
                k * 16 + 8,
                3,  # _PY_OBJ_SLOT_BORROWED_UPDATE_ONLY
                mode,
                recurse,
            )
            k = k + 1
    _py_obj_visit_slot(
        o,
        96,
        3,  # _PY_OBJ_SLOT_BORROWED_UPDATE_ONLY
        mode,
        recurse,
    )
    _py_obj_visit_slot(o, 104, 1, mode, recurse)  # _PY_OBJ_SLOT_OWNED
    _py_obj_visit_slot(
        o,
        112,
        2,  # _PY_OBJ_SLOT_BORROWED_TRACED
        mode,
        recurse,
    )
    return 1


def _py_obj_visit_cext_object_slot(slot, role: int, ctx) -> None:
    if ptr_is_null(ctx) != 0:
        return
    mode: int = load_i32(ctx, 0)
    recurse: int = load_i32(ctx, 4)
    _py_obj_visit_slot(slot, 0, role, mode, recurse)


def _py_obj_visit_cext_object_slots(o, mode: int, recurse: int) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    ctx = malloc(8)
    if ptr_is_null(ctx) != 0:
        abort_extern()
        return 0
    store_i32(ctx, 0, mode)
    store_i32(ctx, 4, recurse)
    handled: int = pcc_capi_visit_cext_object_slots_i64(
        o,
        _py_obj_visit_cext_object_slot,
        ctx,
    )
    free(ctx)
    return handled


def _py_obj_visit_instance_owner_slots(o, mode: int, recurse: int) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return 0
    if (
        tag != 11 and tag != 200 and tag < 104
    ):  # PY_TYPE_INSTANCE / PY_TYPE_VALUEBOX / user tags
        return 0
    cls = load_ptr(o, 16)
    if ptr_is_null(cls) != 0:
        return 1
    _py_obj_visit_slot(o, 16, 2, mode, recurse)  # _PY_OBJ_SLOT_BORROWED_TRACED
    cls = load_ptr(o, 16)
    if ptr_is_null(cls) != 0:
        return 1
    n_fields: int = load_i32(cls, 72)
    if n_fields < 0:
        n_fields = 0
    i: int = 0
    while i < n_fields:
        _py_obj_visit_slot(o, 24 + i * 8, 1, mode, recurse)
        i = i + 1
    cls_flags: int = load_i32(cls, 12)
    if (cls_flags & 2) == 0:
        _py_obj_visit_slot(o, 24 + n_fields * 8, 1, mode, recurse)
    return 1


def _py_obj_has_no_pointer_slots(o) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if tag == 0:  # PY_TYPE_NONE
        return 1
    if tag == 1:  # PY_TYPE_BOOL
        return 1
    if tag == 2:  # PY_TYPE_INT
        return 1
    if tag == 3:  # PY_TYPE_FLOAT
        return 1
    if tag == 4:  # PY_TYPE_STR
        return 1
    if tag == 16:  # PY_TYPE_COMPLEX
        return 1
    if tag == 17:  # PY_TYPE_BYTES
        return 1
    if tag == 18:  # PY_TYPE_BYTEARRAY
        return 1
    if tag == 13:  # PY_TYPE_FILE
        return 1
    if tag == 32:  # PY_TYPE_CPY_HANDLE
        return 1
    # C-tier thread wait queues keep vthreads in external scheduler-rooted
    # waiter nodes, not object-inline PyObject slots. The pcc-Python mirror
    # keeps these primitive layouts no-slot as well.
    if tag == 22:  # PY_TYPE_THREAD_LOCK
        return 1
    if tag == 23:  # PY_TYPE_THREAD_RLOCK
        return 1
    if tag == 24:  # PY_TYPE_THREAD_EVENT
        return 1
    if tag == 25:  # PY_TYPE_THREAD_CONDITION
        return 1
    if tag == 26:  # PY_TYPE_THREAD_SEMAPHORE
        return 1
    return 0


def _py_obj_visit_covered_slots(o, mode: int, recurse: int) -> int:
    if _py_obj_has_no_pointer_slots(o) != 0:
        return 1
    handled: int = _py_obj_visit_core_container_owner_slots(o, mode, recurse)
    if handled != 0:
        return handled
    handled = _py_obj_visit_fixed_owner_slots(o, mode, recurse)
    if handled != 0:
        return handled
    handled = _py_obj_visit_weakref_slots(o, mode, recurse)
    if handled != 0:
        return handled
    handled = _py_obj_visit_continuation_owner_slots(o, mode, recurse)
    if handled != 0:
        return handled
    handled = _py_obj_visit_class_slots(o, mode, recurse)
    if handled != 0:
        return handled
    handled = _py_obj_visit_cext_object_slots(o, mode, recurse)
    if handled != 0:
        return handled
    return _py_obj_visit_instance_owner_slots(o, mode, recurse)


def _trace_referents(o) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    if _py_obj_has_no_pointer_slots(o) != 0:
        return
    if _py_obj_visit_covered_slots(o, 1, 0) != 0:  # _PY_OBJ_VISIT_TRACE
        return


def _subtract_known_child_ref(child) -> None:
    if ptr_is_null(child) != 0:
        return
    if is_tagged_int(child) != 0:
        return
    forwarding = _forwarding_find(child)
    if ptr_is_null(forwarding) == 0:
        resolved = load_ptr(forwarding, 8)
        if ptr_is_null(resolved) == 0:
            child = resolved
    node = pcc_gc_object_index_find(child)
    if ptr_is_null(node) != 0:
        return
    if _object_node_is_active(node) == 0:
        return
    _set_object_node_gc_refs(node, _object_node_gc_refs(node) - 1)


def _subtract_referent_refs(o) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    if _py_obj_has_no_pointer_slots(o) != 0:
        return
    if _py_obj_visit_covered_slots(o, 4, 0) != 0:  # _PY_OBJ_VISIT_SUBTRACT
        return


def _trace_referents_for_promotion_mode(o, recurse: int) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    if _py_obj_has_no_pointer_slots(o) != 0:
        return
    if _py_obj_visit_covered_slots(o, 2, recurse) != 0:  # _PY_OBJ_VISIT_PROMOTE
        return


def _trace_referents_for_promotion(o) -> None:
    _trace_referents_for_promotion_mode(o, 1)


def _promote_frame_roots(remaining_budget: int) -> None:
    if remaining_budget <= 0:
        return
    frame = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(frame) == 0:
        _py_visit_mapped_root_slots(
            load_i64(frame, 40),
            load_ptr(frame, 8),
            load_ptr(frame, 56),
            load_i32(frame, 48) & 1,
            2,  # _PY_ROOT_VISIT_PROMOTE
            0,
        )
        frame = load_ptr(frame, 16)

    cont = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(cont) == 0:
        _py_visit_mapped_root_slots(
            load_i64(cont, 24),
            load_ptr(cont, 8),
            load_ptr(cont, 40),
            load_i32(cont, 32),
            2,  # _PY_ROOT_VISIT_PROMOTE
            0,
        )
        cont = load_ptr(cont, 16)

    _py_visit_scheduler_root_slots(2, 0)
    _py_visit_builtin_exception_cache_slots(2, 0)


def _promote_tls_exception_root() -> None:
    cur = py_tls_exc_get()
    if ptr_is_null(cur) != 0:
        return
    if is_tagged_int(cur) != 0:
        return
    oldified = _generational_oldify_copy(cur)
    if ptr_is_null(oldified) == 0:
        if ptr_eq(oldified, cur) == 0:
            py_incref(oldified)
            py_tls_exc_set(oldified)
            _trace_referents_for_promotion(oldified)
            py_decref(cur)
            return
    _promote_young_if_known(cur)


def _backend3_remembered_owner_head():
    return global_load_ptr("pcc_gc_backend3_remembered_owner_head")


def _set_backend3_remembered_owner_head(head) -> None:
    global_store_ptr("pcc_gc_backend3_remembered_owner_head", head)


def _backend3_remember_owner(owner, owner_flags: int) -> None:
    if ptr_is_null(owner) != 0:
        return
    if is_tagged_int(owner) != 0:
        return
    if (owner_flags & 512) != 0:
        return
    node = malloc(16)
    if ptr_is_null(node) != 0:
        store_i32(global_addr("pcc_gc_backend3_remembered_overflow"), 0, 1)
        store_i32(owner, 12, owner_flags | 512)
        return
    store_ptr(node, 0, owner)
    store_ptr(node, 8, _backend3_remembered_owner_head())
    _set_backend3_remembered_owner_head(node)
    store_i32(owner, 12, owner_flags | 512)


def _backend3_clear_remembered_owners() -> None:
    node = _backend3_remembered_owner_head()
    _set_backend3_remembered_owner_head(null())
    store_i32(global_addr("pcc_gc_backend3_remembered_overflow"), 0, 0)
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 8)
        free(node)
        node = nxt


def _backend3_scan_remembered_owners(remaining_budget: int) -> int:
    local_processed: int = 0
    node = _object_head()
    while ptr_is_null(node) == 0 and local_processed < remaining_budget:
        if _object_node_is_active(node) == 0:
            node = _object_node_next(node)
            continue
        owner = load_ptr(node, 0)
        flags: int = load_i32(owner, 12)
        if (flags & 512) != 0:
            _trace_referents_for_promotion(owner)
            store_i32(owner, 12, flags & ~512)
            local_processed = local_processed + 1
            if (local_processed % 16) == 0:
                pcc_thread_safepoint()
        node = _object_node_next(node)
    return local_processed


def _backend3_drain_remembered_owners(remaining_budget: int) -> int:
    local_processed: int = 0
    if load_i32(global_addr("pcc_gc_backend3_remembered_overflow"), 0) != 0:
        _backend3_clear_remembered_owners()
        return _backend3_scan_remembered_owners(remaining_budget)
    while (
        ptr_is_null(_backend3_remembered_owner_head()) == 0
        and local_processed < remaining_budget
    ):
        node = _backend3_remembered_owner_head()
        _set_backend3_remembered_owner_head(load_ptr(node, 8))
        owner = load_ptr(node, 0)
        free(node)
        if _is_known_object(owner) == 0:
            continue
        flags: int = load_i32(owner, 12)
        if (flags & 512) == 0:
            continue
        _trace_referents_for_promotion(owner)
        store_i32(owner, 12, flags & ~512)
        local_processed = local_processed + 1
        if (local_processed % 16) == 0:
            pcc_thread_safepoint()
    return local_processed


@c_abi_export("pcc_gc_backend")
def pcc_gc_backend() -> int:
    return _init_config()


@c_abi_export("pcc_gc_set_backend")
def pcc_gc_set_backend(backend: int) -> int:
    _init_config()
    if backend < 0 or backend > 4:
        return -1
    old_backend: int = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend == 3 or backend == 4:
        store_i32(global_addr("pcc_gc_read_barrier_enabled"), 0, 1)
    if backend == 0:
        store_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0, 1)
    store_i32(global_addr("pcc_gc_backend_selected"), 0, backend)
    if backend != 3 and backend != 4:
        store_i32(global_addr("pcc_gc_read_barrier_enabled"), 0, 0)
    store_i32(global_addr("pcc_gc_mark_active"), 0, 0)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    _set_gray_count(0)
    store_i32(global_addr("pcc_gc_debt_bytes"), 0, 0)
    store_i32(global_addr("pcc_gc_last_alloc_bytes"), 0, 0)
    if backend == 0:
        _clear_object_list()
        store_i32(global_addr("pcc_gc_live_bytes"), 0, 0)
    if _backend_uses_forwarding() == 0:
        _forwarding_clear_all()
        _identity_clear_all()
    if backend != 4:
        pcc_gc_reset_relocation_set()
        _backend4_store_buffer_clear()
    if old_backend == 2 and backend != 2:
        _stop_cms_worker()
    _maybe_start_cms_worker()
    return 0


@c_abi_export("pcc_gc_backend_name")
def pcc_gc_backend_name(backend: int):
    if backend == 0:
        return cstr("refcount-cycle")
    if backend == 1:
        return cstr("incremental-tricolor")
    if backend == 2:
        return cstr("concurrent-mark-sweep")
    if backend == 3:
        return cstr("generational-minor-major")
    if backend == 4:
        return cstr("colored-relocating")
    return cstr("unknown")


@c_abi_export("pcc_gc_telemetry_reset")
def pcc_gc_telemetry_reset() -> None:
    _init_config()
    _object_graph_lock()
    _backend3_clear_remembered_owners()
    _object_graph_unlock()
    i: int = 0
    while i <= 5:
        store_i32(_counter_global(i), 0, 0)
        i = i + 1
    store_i32(global_addr("pcc_gc_metric_max_pause_us"), 0, 0)
    store_i32(global_addr("pcc_gc_metric_pause_count"), 0, 0)
    store_i32(global_addr("pcc_gc_metric_pause_sum_us"), 0, 0)
    store_i32(global_addr("pcc_gc_metric_pause_hist0"), 0, 0)
    store_i32(global_addr("pcc_gc_metric_pause_hist1"), 0, 0)
    store_i32(global_addr("pcc_gc_metric_pause_hist2"), 0, 0)
    store_i32(global_addr("pcc_gc_metric_pause_hist3"), 0, 0)
    pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_allocations"), 0)
    pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_collections"), 0)
    pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_bytes"), 0)
    store_i32(global_addr("pcc_gc_cms_queue_pushes"), 0, 0)
    store_i32(global_addr("pcc_gc_cms_worker_drains"), 0, 0)
    store_i32(global_addr("pcc_gc_cms_mutator_assists"), 0, 0)
    store_i32(global_addr("pcc_gc_cms_worker_traces"), 0, 0)
    pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_arena_refills"), 0)
    pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_arena_bumps"), 0)
    pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_arena_fallbacks"), 0)
    store_i32(global_addr("pcc_gc_cms_worker_stops"), 0, 0)
    store_i32(global_addr("pcc_gc_cms_wb_flushes"), 0, 0)
    store_i32(global_addr("pcc_gc_relocation_forwards"), 0, 0)
    store_i32(global_addr("pcc_gc_relocation_barrier_forwards"), 0, 0)
    store_i32(global_addr("pcc_gc_relocation_pin_rejects"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_genzgc_store_barriers"), 0, 0)
    _backend4_reset_store_buffer_epoch_state()
    store_i32(global_addr("pcc_gc_backend4_young_promotions"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_evacuation_candidates"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_evacuated_bytes_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_large_object_defers"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_large_object_deferred_bytes_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_large_object_reconsiderations_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_small_page_candidates"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_medium_page_candidates"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_evacuation_candidate_bytes_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_small_page_candidate_bytes_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_medium_page_candidate_bytes_count"), 0, 0)
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_small_page_candidate_zpage_bytes_count"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count"), 0, 0
    )
    store_i32(global_addr("pcc_gc_backend4_store_buffer_drain_batches_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_store_buffer_drained_entries_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_store_buffer_duplicate_skips_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_store_buffer_incomplete_drains_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_evacuation_incomplete_batches_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_store_buffer_max_batch_size_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_store_buffer_full_batches_count"), 0, 0)
    _backend4_reset_remembered_set_epoch_state()
    store_i32(global_addr("pcc_gc_backend4_remembered_set_duplicate_skips_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_store_buffer_medium_flushes_count"), 0, 0)
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_flushed_entries_count"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_full_flushes_count"), 0, 0
    )
    _backend4_reseed_relocation_epoch_state()
    _backend4_clear_large_deferred_flags()


@c_abi_export("pcc_gc_backend4_fragmentation_score")
def pcc_gc_backend4_fragmentation_score() -> int:
    return pcc_gc_relocation_set_size() + pcc_gc_backend4_forwarding_entries()


@c_abi_export("pcc_gc_backend4_generation_barrier_score")
def pcc_gc_backend4_generation_barrier_score() -> int:
    return load_i32(global_addr("pcc_gc_backend4_genzgc_store_barriers"), 0)


@c_abi_export("pcc_gc_backend4_store_buffer_entries")
def pcc_gc_backend4_store_buffer_entries() -> int:
    return load_i32(global_addr("pcc_gc_backend4_store_buffer_entries_count"), 0)


@c_abi_export("pcc_gc_backend4_generation_promotion_score")
def pcc_gc_backend4_generation_promotion_score() -> int:
    return load_i32(global_addr("pcc_gc_backend4_young_promotions"), 0)


@c_abi_export("pcc_gc_backend4_evacuation_candidate_score")
def pcc_gc_backend4_evacuation_candidate_score() -> int:
    return load_i32(global_addr("pcc_gc_backend4_evacuation_candidates"), 0)


@c_abi_export("pcc_gc_backend4_evacuated_bytes")
def pcc_gc_backend4_evacuated_bytes() -> int:
    return load_i32(global_addr("pcc_gc_backend4_evacuated_bytes_count"), 0)


@c_abi_export("pcc_gc_backend4_page_policy_score")
def pcc_gc_backend4_page_policy_score() -> int:
    return (
        pcc_gc_backend4_evacuation_candidate_score() + pcc_gc_backend4_evacuated_bytes()
    )


@c_abi_export("pcc_gc_backend4_page_pressure_score")
def pcc_gc_backend4_page_pressure_score() -> int:
    return (
        pcc_gc_backend4_evacuation_candidate_bytes()
        + pcc_gc_backend4_large_object_deferred_bytes()
    )


@c_abi_export("pcc_gc_backend4_fragmentation_backlog_bytes")
def pcc_gc_backend4_fragmentation_backlog_bytes() -> int:
    candidates: int = pcc_gc_backend4_evacuation_candidate_bytes()
    evacuated: int = pcc_gc_backend4_evacuated_bytes()
    deferred: int = pcc_gc_backend4_large_object_deferred_bytes()
    pending: int = 0
    if candidates > evacuated:
        pending = candidates - evacuated
    return pending + deferred


@c_abi_export("pcc_gc_backend4_evacuation_efficiency_per_mille")
def pcc_gc_backend4_evacuation_efficiency_per_mille() -> int:
    candidates: int = pcc_gc_backend4_evacuation_candidate_bytes()
    if candidates <= 0:
        return 1000
    evacuated: int = pcc_gc_backend4_evacuated_bytes()
    if evacuated <= 0:
        return 0
    if evacuated >= candidates:
        return 1000
    return (evacuated * 1000) // candidates


@c_abi_export("pcc_gc_backend4_fragmentation_policy_score")
def pcc_gc_backend4_fragmentation_policy_score() -> int:
    return (
        pcc_gc_backend4_fragmentation_backlog_bytes()
        + pcc_gc_backend4_evacuation_incomplete_batches()
    )


@c_abi_export("pcc_gc_backend4_small_page_limit_bytes")
def pcc_gc_backend4_small_page_limit_bytes() -> int:
    return 4096


@c_abi_export("pcc_gc_backend4_medium_page_limit_bytes")
def pcc_gc_backend4_medium_page_limit_bytes() -> int:
    return 65536


@c_abi_export("pcc_gc_backend4_large_defer_limit_bytes")
def pcc_gc_backend4_large_defer_limit_bytes() -> int:
    return 65536


@c_abi_export("pcc_gc_backend4_large_object_defer_score")
def pcc_gc_backend4_large_object_defer_score() -> int:
    return load_i32(global_addr("pcc_gc_backend4_large_object_defers"), 0)


@c_abi_export("pcc_gc_backend4_large_object_deferred_bytes")
def pcc_gc_backend4_large_object_deferred_bytes() -> int:
    return load_i32(global_addr("pcc_gc_backend4_large_object_deferred_bytes_count"), 0)


@c_abi_export("pcc_gc_backend4_large_object_reconsiderations")
def pcc_gc_backend4_large_object_reconsiderations() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_large_object_reconsiderations_count"), 0
    )


def _backend4_generation_count(flag: int) -> int:
    _object_graph_lock()
    node = _object_head()
    count: int = 0
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            if ptr_is_null(obj) == 0:
                if is_tagged_int(obj) == 0:
                    flags: int = load_i32(obj, 12)
                    if (flags & flag) != 0:
                        count = count + 1
        node = _object_node_next(node)
    _object_graph_unlock()
    return count


def _backend4_generation_bytes(flag: int) -> int:
    _object_graph_lock()
    node = _object_head()
    total: int = 0
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            if ptr_is_null(obj) == 0:
                if is_tagged_int(obj) == 0:
                    flags: int = load_i32(obj, 12)
                    if (flags & flag) != 0:
                        total = total + _object_node_size(node)
        node = _object_node_next(node)
    _object_graph_unlock()
    return total


@c_abi_export("pcc_gc_backend4_young_object_count")
def pcc_gc_backend4_young_object_count() -> int:
    return _backend4_generation_count(128)


@c_abi_export("pcc_gc_backend4_old_object_count")
def pcc_gc_backend4_old_object_count() -> int:
    return _backend4_generation_count(256)


@c_abi_export("pcc_gc_backend4_young_bytes")
def pcc_gc_backend4_young_bytes() -> int:
    return _backend4_generation_bytes(128)


@c_abi_export("pcc_gc_backend4_old_bytes")
def pcc_gc_backend4_old_bytes() -> int:
    return _backend4_generation_bytes(256)


def _backend4_page_class_for_size(size: int) -> int:
    if size <= 4096:
        return 0
    if size <= 65536:
        return 1
    return 2


def _backend4_align_alloc_size(size: int) -> int:
    if size <= 0:
        return 0
    return (size + 7) & -8


def _backend4_generation_for_flags(flags: int) -> int:
    if (flags & 256) != 0:
        return 2
    return 1


def _backend4_page_class_population(page_class: int, count_bytes: int) -> int:
    _object_graph_lock()
    node = _object_head()
    total: int = 0
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            if ptr_is_null(obj) == 0:
                if is_tagged_int(obj) == 0:
                    size: int = _object_node_size(node)
                    if _backend4_page_class_for_size(size) == page_class:
                        if count_bytes != 0:
                            total = total + size
                        else:
                            total = total + 1
        node = _object_node_next(node)
    _object_graph_unlock()
    return total


@c_abi_export("pcc_gc_backend4_small_page_object_count")
def pcc_gc_backend4_small_page_object_count() -> int:
    return _backend4_page_class_population(0, 0)


@c_abi_export("pcc_gc_backend4_medium_page_object_count")
def pcc_gc_backend4_medium_page_object_count() -> int:
    return _backend4_page_class_population(1, 0)


@c_abi_export("pcc_gc_backend4_large_page_object_count")
def pcc_gc_backend4_large_page_object_count() -> int:
    return _backend4_page_class_population(2, 0)


@c_abi_export("pcc_gc_backend4_small_page_live_bytes")
def pcc_gc_backend4_small_page_live_bytes() -> int:
    return _backend4_page_class_population(0, 1)


@c_abi_export("pcc_gc_backend4_medium_page_live_bytes")
def pcc_gc_backend4_medium_page_live_bytes() -> int:
    return _backend4_page_class_population(1, 1)


@c_abi_export("pcc_gc_backend4_large_page_live_bytes")
def pcc_gc_backend4_large_page_live_bytes() -> int:
    return _backend4_page_class_population(2, 1)


def _backend4_zpage_capacity_for_size(size: int) -> int:
    if size <= 4096:
        return 4096
    if size <= 65536:
        return 65536
    unit: int = 65536
    pages: int = (size + unit - 1) // unit
    if pages < 1:
        pages = 1
    return pages * unit


def _backend4_zpage_generation_for_owner(owner) -> int:
    if ptr_is_null(owner) == 0 and is_tagged_int(owner) == 0:
        if (load_i32(owner, 12) & 256) != 0:
            return 2
    return 1


def _backend4_active_page(page_class: int, generation: int):
    if page_class == 0:
        if generation == 2:
            return global_load_ptr("pcc_gc_backend4_active_small_old_page")
        return global_load_ptr("pcc_gc_backend4_active_small_young_page")
    if page_class == 1:
        if generation == 2:
            return global_load_ptr("pcc_gc_backend4_active_medium_old_page")
        return global_load_ptr("pcc_gc_backend4_active_medium_young_page")
    return null()


def _backend4_set_active_page(page) -> None:
    if ptr_is_null(page) != 0:
        return
    page_class: int = load_i32(page, 24)
    generation: int = load_i32(page, 28)
    if page_class == 0:
        if generation == 2:
            global_store_ptr("pcc_gc_backend4_active_small_old_page", page)
        else:
            global_store_ptr("pcc_gc_backend4_active_small_young_page", page)
    elif page_class == 1:
        if generation == 2:
            global_store_ptr("pcc_gc_backend4_active_medium_old_page", page)
        else:
            global_store_ptr("pcc_gc_backend4_active_medium_young_page", page)


def _backend4_clear_active_page(page) -> None:
    if ptr_is_null(page) != 0:
        return
    cur = global_load_ptr("pcc_gc_backend4_active_small_young_page")
    if ptr_eq(cur, page) != 0:
        global_store_ptr("pcc_gc_backend4_active_small_young_page", null())
    cur = global_load_ptr("pcc_gc_backend4_active_small_old_page")
    if ptr_eq(cur, page) != 0:
        global_store_ptr("pcc_gc_backend4_active_small_old_page", null())
    cur = global_load_ptr("pcc_gc_backend4_active_medium_young_page")
    if ptr_eq(cur, page) != 0:
        global_store_ptr("pcc_gc_backend4_active_medium_young_page", null())
    cur = global_load_ptr("pcc_gc_backend4_active_medium_old_page")
    if ptr_eq(cur, page) != 0:
        global_store_ptr("pcc_gc_backend4_active_medium_old_page", null())


def _backend4_zpage_find_reusable_page_for_gen(size: int, generation: int):
    if size <= 0 or size > 65536:
        return null()
    wanted_class: int = _backend4_page_class_for_size(size)
    alloc_size: int = _backend4_align_alloc_size(size)
    active = _backend4_active_page(wanted_class, generation)
    evac_head = _evacuation_page_head()
    if ptr_is_null(active) == 0:
        capacity: int = load_i64(active, 16)
        allocated: int = load_i64(active, 64)
        if (
            load_i32(active, 24) == wanted_class
            and load_i32(active, 28) == generation
            and capacity - allocated >= alloc_size
        ):
            if (
                ptr_is_null(evac_head) != 0
                or ptr_is_null(_backend4_evacuation_page_find(active)) != 0
            ):
                return active
        _backend4_clear_active_page(active)
    return null()


def _backend4_zpage_page_contains_addr(page, ptr, alloc_size: int) -> int:
    if ptr_is_null(page) != 0 or ptr_is_null(ptr) != 0 or alloc_size <= 0:
        return 0
    span = load_ptr(page, 72)
    span_capacity: int = load_i64(page, 80)
    if ptr_is_null(span) != 0 or span_capacity <= 0:
        return 0
    delta: int = ptr_diff(ptr, span)
    if delta >= 0 and delta + alloc_size <= span_capacity:
        return 1
    return 0


def _backend4_zpage_find_reusable_page(owner, size: int):
    if size <= 0 or size > 65536:
        return null()
    wanted_class: int = _backend4_page_class_for_size(size)
    wanted_generation: int = _backend4_zpage_generation_for_owner(owner)
    alloc_size: int = _backend4_align_alloc_size(size)
    active = _backend4_active_page(wanted_class, wanted_generation)
    evac_head = _evacuation_page_head()
    if ptr_is_null(active) == 0:
        capacity: int = load_i64(active, 16)
        allocated: int = load_i64(active, 64)
        if (
            load_i32(active, 24) == wanted_class
            and load_i32(active, 28) == wanted_generation
            and capacity - allocated >= alloc_size
        ):
            if (
                ptr_is_null(evac_head) != 0
                or ptr_is_null(_backend4_evacuation_page_find(active)) != 0
            ):
                return active
        _backend4_clear_active_page(active)
    return null()


def _backend4_zpage_pop_free_page(size: int):
    if size <= 0 or size > 65536:
        return null()
    wanted_class: int = _backend4_page_class_for_size(size)
    wanted_capacity: int = _backend4_zpage_capacity_for_size(size)
    prev = null()
    page = _zpage_free_page_head()
    while ptr_is_null(page) == 0:
        nxt = load_ptr(page, 56)
        if load_i32(page, 24) == wanted_class and load_i64(page, 16) == wanted_capacity:
            if ptr_is_null(prev) != 0:
                _set_zpage_free_page_head(nxt)
            else:
                store_ptr(prev, 56, nxt)
            store_ptr(page, 56, null())
            return page
        prev = page
        page = nxt
    return null()


def _backend4_zpage_reset(page, owner, size: int) -> None:
    if ptr_is_null(page) != 0:
        return
    capacity: int = _backend4_zpage_capacity_for_size(size)
    store_ptr(page, 0, owner)
    store_i64(page, 8, 0)
    store_i64(page, 16, capacity)
    store_i64(page, 64, 0)
    store_i32(page, 24, _backend4_page_class_for_size(size))
    store_i32(page, 28, _backend4_zpage_generation_for_owner(owner))
    store_i64(page, 32, 0)
    store_i64(page, 40, 0)
    store_i64(page, 48, 0)
    store_i64(page, 88, 0)
    store_i64(page, 96, 0)  # pending_forwardings
    store_i32(page, 104, 0)  # zombie
    store_ptr(page, 112, null())  # object_head
    span = load_ptr(page, 72)
    span_capacity: int = load_i64(page, 80)
    if ptr_is_null(span) != 0 or span_capacity < capacity:
        span = malloc(capacity + 256)
        store_ptr(page, 72, span)
        if ptr_is_null(span) != 0:
            store_i64(page, 80, 0)
        else:
            store_i64(page, 80, capacity)
    if ptr_is_null(span) == 0 and capacity > 0:
        memset(span, 0, capacity)


def _backend4_free_page_count_for_class(page_class: int) -> int:
    page = _zpage_free_page_head()
    count: int = 0
    while ptr_is_null(page) == 0:
        if load_i32(page, 24) == page_class:
            count = count + 1
        page = load_ptr(page, 56)
    return count


def _backend4_free_page_limit_for_class(page_class: int) -> int:
    if page_class == 0:
        return 8
    if page_class == 1:
        return 4
    return 0


def _backend4_zpage_clear_reusable_state(page) -> None:
    if ptr_is_null(page) != 0:
        return
    store_ptr(page, 0, null())
    store_i64(page, 8, 0)
    store_i64(page, 64, 0)
    store_i64(page, 32, 0)
    store_i64(page, 40, 0)
    store_i64(page, 48, 0)
    store_i64(page, 88, 0)
    store_i64(page, 96, 0)  # pending_forwardings
    store_i32(page, 104, 0)  # zombie
    store_ptr(page, 112, null())  # object_head


def _backend4_zpage_cache(page) -> None:
    if ptr_is_null(page) != 0:
        return
    _backend4_clear_active_page(page)
    _backend4_zpage_clear_reusable_state(page)
    store_ptr(page, 56, _zpage_free_page_head())
    _set_zpage_free_page_head(page)


def _backend4_zpage_recycle(page) -> None:
    if ptr_is_null(page) != 0:
        return
    _backend4_clear_active_page(page)
    page_class: int = load_i32(page, 24)
    if page_class > 1:
        _backend4_zpage_destroy(page)
        return
    limit: int = _backend4_free_page_limit_for_class(page_class)
    if limit <= 0 or _backend4_free_page_count_for_class(page_class) >= limit:
        _backend4_zpage_destroy(page)
        return
    _backend4_zpage_cache(page)


def _backend4_zpage_destroy(page) -> None:
    if ptr_is_null(page) != 0:
        return
    _backend4_clear_active_page(page)
    # Correctness-first retirement: old SSA values, delayed trashcan entries,
    # and stale borrowed pointers can outlive owner-index membership. Keep the
    # backing span recognizable, but remove the page from the reusable cache so
    # free-list scans and reuse stay bounded. Physical release needs a stronger
    # remap/epoch proof than the current runtime has.
    _backend4_zpage_clear_reusable_state(page)
    store_ptr(page, 56, _zpage_retained_page_head())
    _set_zpage_retained_page_head(page)


def _backend4_zpage_page_head(page):
    if ptr_is_null(page) != 0:
        return null()
    return load_ptr(page, 112)


def _backend4_zpage_node_alloc():
    head = global_load_ptr("pcc_gc_backend4_zpage_node_free_head")
    if ptr_is_null(head) == 0:
        nxt = load_ptr(head, 16)
        global_store_ptr("pcc_gc_backend4_zpage_node_free_head", nxt)
        count: int = load_i32(global_addr("pcc_gc_backend4_zpage_node_free_count"), 0)
        if count > 0:
            store_i32(
                global_addr("pcc_gc_backend4_zpage_node_free_count"),
                0,
                count - 1,
            )
        return head
    node = malloc(72)
    return node


def _backend4_zpage_node_release(node) -> None:
    if ptr_is_null(node) != 0:
        return
    count: int = load_i32(global_addr("pcc_gc_backend4_zpage_node_free_count"), 0)
    if count >= 8192:
        free(node)
        return
    store_ptr(node, 16, global_load_ptr("pcc_gc_backend4_zpage_node_free_head"))
    global_store_ptr("pcc_gc_backend4_zpage_node_free_head", node)
    store_i32(global_addr("pcc_gc_backend4_zpage_node_free_count"), 0, count + 1)


def _backend4_zpage_set_page_head(page, head) -> None:
    if ptr_is_null(page) != 0:
        return
    store_ptr(page, 112, head)
    if ptr_is_null(head) == 0:
        store_ptr(page, 0, load_ptr(head, 0))
    else:
        store_ptr(page, 0, null())


def _backend4_zpage_link_node(node) -> None:
    if ptr_is_null(node) != 0:
        return
    page = load_ptr(node, 8)
    store_ptr(node, 40, null())
    nxt = _zpage_head()
    store_ptr(node, 16, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 40, node)
    _set_zpage_head(node)
    pcc_gc_zpage_owner_index_upsert(load_ptr(node, 0), node)
    page_head = _backend4_zpage_page_head(page)
    store_ptr(node, 56, null())
    store_ptr(node, 48, page_head)
    if ptr_is_null(page_head) == 0:
        store_ptr(page_head, 56, node)
    _backend4_zpage_set_page_head(page, node)


def _backend4_zpage_unlink_node(node) -> None:
    if ptr_is_null(node) != 0:
        return
    pcc_gc_zpage_owner_index_remove(load_ptr(node, 0))
    prev = load_ptr(node, 40)
    nxt = load_ptr(node, 16)
    if ptr_is_null(prev) != 0:
        if ptr_eq(_zpage_head(), node) != 0:
            _set_zpage_head(nxt)
    else:
        store_ptr(prev, 16, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 40, prev)
    page = load_ptr(node, 8)
    page_prev = load_ptr(node, 56)
    page_next = load_ptr(node, 48)
    if ptr_is_null(page) == 0:
        if ptr_is_null(page_prev) != 0:
            _backend4_zpage_set_page_head(page, page_next)
        else:
            store_ptr(page_prev, 48, page_next)
        if ptr_is_null(page_next) == 0:
            store_ptr(page_next, 56, page_prev)
        if ptr_is_null(page_prev) == 0:
            head = _backend4_zpage_page_head(page)
            if ptr_is_null(head) == 0:
                store_ptr(page, 0, load_ptr(head, 0))
            else:
                store_ptr(page, 0, null())
    store_ptr(node, 16, null())
    store_ptr(node, 40, null())
    store_ptr(node, 48, null())
    store_ptr(node, 56, null())


def _backend4_zpage_find(owner):
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return null()
    obj_node = pcc_gc_object_index_find(owner)
    if ptr_is_null(obj_node) == 0 and _object_node_freeing(obj_node) == 0:
        znode = _object_node_zpage(obj_node)
        if ptr_is_null(znode) == 0:
            return znode
    return pcc_gc_zpage_owner_index_find(owner)


def _backend4_zpage_note_owner_promoted(owner) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return
    page = load_ptr(node, 8)
    if ptr_is_null(page) == 0:
        store_i32(page, 28, 2)


def _backend4_park_page(page) -> None:
    # one-epoch defer (remap design): parked pages keep their spans
    # mapped until the NEXT remap destroys them, so stale SSA/borrowed
    # pointers from the current step window can still read old headers.
    if ptr_is_null(page) != 0:
        return
    _backend4_clear_active_page(page)
    store_ptr(page, 112, null())  # object_head
    store_ptr(page, 56, global_load_ptr("pcc_gc_backend4_parked_head"))
    global_store_ptr("pcc_gc_backend4_parked_head", page)


def _backend4_drain_parked_pages() -> None:
    page = global_load_ptr("pcc_gc_backend4_parked_head")
    global_store_ptr("pcc_gc_backend4_parked_head", null())
    while ptr_is_null(page) == 0:
        nxt = load_ptr(page, 56)
        store_ptr(page, 56, null())
        _backend4_zpage_destroy(page)
        page = nxt


def _remap_heal_slot(base, offset: int) -> None:
    v = load_ptr(base, offset)
    if ptr_is_null(v) != 0 or is_tagged_int(v) != 0:
        return
    if (load_i32(v, 12) & 2048) == 0:
        return
    node = _forwarding_find(v)
    if ptr_is_null(node) != 0:
        return
    to = load_ptr(node, 8)
    if ptr_is_null(to) != 0:
        return
    # bits only: under count-on-NEW the slot's reference is already
    # accounted on the new copy
    store_ptr(base, offset, to)


def _remap_referents(o) -> None:
    # Slot-rewriting sibling of _trace_referents; the shared helper keeps
    # per-type coverage in sync with trace/subtract/promote/clear and with
    # the C tier's pcc_gc_update_referents.
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    if _py_obj_has_no_pointer_slots(o) != 0:
        return
    if _py_obj_visit_covered_slots(o, 3, 0) != 0:  # _PY_OBJ_VISIT_UPDATE
        return


def _backend4_remap_and_retire() -> None:
    # Mirrors pcc_gc_backend4_remap_and_retire_unlocked (C tier); see
    # docs/plans/gc4-relocation-remap-plan.md stage 2. Caller holds the
    # object graph lock.
    if pcc_gc_backend() != 4:
        return
    _backend4_drain_parked_pages()
    if ptr_is_null(_forwarding_head()) != 0:
        return
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if load_i64(node, 32) == 0:
            _remap_referents(load_ptr(node, 0))
        node = nxt
    frame = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(frame) == 0:
        _py_visit_mapped_root_slots(
            load_i64(frame, 40),
            load_ptr(frame, 8),
            null(),
            load_i32(frame, 48) & 1,
            3,  # _PY_ROOT_VISIT_REWRITE
            0,
        )
        frame = load_ptr(frame, 16)
    cont = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(cont) == 0:
        _py_visit_mapped_root_slots(
            load_i64(cont, 24),
            load_ptr(cont, 8),
            null(),
            load_i32(cont, 32),
            3,  # _PY_ROOT_VISIT_REWRITE
            0,
        )
        cont = load_ptr(cont, 16)
    _py_visit_scheduler_root_slots(3, 0)
    _py_visit_builtin_exception_cache_slots(3, 0)
    # Retire ONE EPOCH LATE (mirror of the C tier): mark entries with
    # the RETIRING flag (131072) at the remap that healed the heap and
    # only retire them at the NEXT remap, so stale SSA pointers stored
    # into slots between remaps keep resolving until this remap's heal
    # pass (above) has rewritten them.
    fwd = _forwarding_head()
    while ptr_is_null(fwd) == 0:
        nxt = load_ptr(fwd, 16)
        old = load_ptr(fwd, 0)
        if ptr_is_null(old) != 0 or is_tagged_int(old) != 0:
            # defensive: unlink manually so the loop cannot spin
            _forwarding_target_unlink(fwd)
            _forwarding_unlink_main(fwd)
            py_decref(load_ptr(fwd, 8))
            free(fwd)
            pop3: int = load_i32(global_addr("pcc_gc_forwarding_population"), 0)
            if pop3 > 0:
                store_i32(global_addr("pcc_gc_forwarding_population"), 0, pop3 - 1)
            fwd = nxt
            continue
        old_flags: int = load_i32(old, 12)
        if (old_flags & 131072) == 0:
            store_i32(old, 12, old_flags | 131072)
            fwd = nxt
            continue
        store_i32(old, 12, old_flags & ~(2048 | 131072))
        _identity_remove(old)
        dead = pcc_gc_object_index_find(old)
        if ptr_is_null(dead) == 0:
            if _object_node_freeing(dead) == 0:
                _live_bytes_subtract(_object_node_size(dead))
            pcc_gc_object_index_remove(old)
            _unlink_object_node(dead)
            _object_node_release(dead)
        _forwarding_remove(old)
        fwd = nxt


def _backend4_note_forwarding_removed_on_page(page) -> None:
    if ptr_is_null(page) != 0:
        return
    fwd: int = load_i64(page, 96)
    if fwd > 0:
        fwd = fwd - 1
        store_i64(page, 96, fwd)
    if (
        load_i32(page, 104) != 0
        and fwd <= 0
        and load_i64(page, 32) <= 0
        and load_i64(page, 88) <= 0
    ):
        store_i32(page, 104, 0)
        _backend4_zpage_unlink_page(page)
        # one-epoch defer: park instead of recycling so stale
        # SSA/borrowed pointers from the current step window can still
        # read old headers; the NEXT remap destroys parked pages.
        _backend4_park_page(page)


def _backend4_zpage_note_forwarding_removed(from_obj) -> None:
    if pcc_gc_backend() != 4:
        return
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return
    if (load_i32(from_obj, 12) & 65536) == 0:
        return
    _backend4_note_forwarding_removed_on_page(
        _backend4_zpage_find_page_for_addr(from_obj, 16)
    )


def _backend4_zpage_find_page_for_addr(ptr, size: int):
    if ptr_is_null(ptr) != 0 or size <= 0:
        return null()
    alloc_size: int = _backend4_align_alloc_size(size)
    wanted_class: int = _backend4_page_class_for_size(size)
    if wanted_class < 2:
        active = _backend4_active_page(wanted_class, 1)
        if _backend4_zpage_page_contains_addr(active, ptr, alloc_size) != 0:
            return active
        active = _backend4_active_page(wanted_class, 2)
        if _backend4_zpage_page_contains_addr(active, ptr, alloc_size) != 0:
            return active
    page = _zpage_page_head()
    while ptr_is_null(page) == 0:
        if _backend4_zpage_page_contains_addr(page, ptr, alloc_size) != 0:
            return page
        page = load_ptr(page, 56)
    return null()


def _backend4_zpage_list_owns_addr(page, ptr) -> int:
    if ptr_is_null(ptr) != 0:
        return 0
    while ptr_is_null(page) == 0:
        span = load_ptr(page, 72)
        span_capacity: int = load_i64(page, 80)
        if ptr_is_null(span) == 0 and span_capacity > 0:
            delta: int = ptr_diff(ptr, span)
            if delta >= 0 and delta < span_capacity:
                return 1
        page = load_ptr(page, 56)
    return 0


def _backend4_zpage_owns_addr(ptr) -> int:
    if pcc_gc_backend() != 4:
        return 0
    if _backend4_zpage_list_owns_addr(_zpage_page_head(), ptr) != 0:
        return 1
    if _backend4_zpage_list_owns_addr(_zpage_free_page_head(), ptr) != 0:
        return 1
    return _backend4_zpage_list_owns_addr(_zpage_retained_page_head(), ptr)


@c_abi_export("pcc_gc_backend4_try_zpage_alloc")
def pcc_gc_backend4_try_zpage_alloc(size: int, flags: int):
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 4:
        return null()
    if size < 16:
        return null()
    alloc_size: int = _backend4_align_alloc_size(size)
    generation: int = _backend4_generation_for_flags(flags)
    _object_graph_lock()
    page_needs_reset: int = 0
    wanted_class: int = _backend4_page_class_for_size(size)
    page = null()
    active = _backend4_active_page(wanted_class, generation)
    if ptr_is_null(active) == 0:
        capacity: int = load_i64(active, 16)
        allocated: int = load_i64(active, 64)
        if (
            load_i32(active, 24) == wanted_class
            and load_i32(active, 28) == generation
            and capacity - allocated >= alloc_size
        ):
            evac_head = _evacuation_page_head()
            if (
                ptr_is_null(evac_head) != 0
                or ptr_is_null(_backend4_evacuation_page_find(active)) != 0
            ):
                page = active
        if ptr_is_null(page) != 0:
            _backend4_clear_active_page(active)
    if ptr_is_null(page) != 0:
        page = _backend4_zpage_find_reusable_page_for_gen(size, generation)
    if ptr_is_null(page) != 0:
        page = _backend4_zpage_pop_free_page(size)
        if ptr_is_null(page) == 0:
            page_needs_reset = 1
    if ptr_is_null(page) != 0:
        page = malloc(120)
        if ptr_is_null(page) != 0:
            _object_graph_unlock()
            return null()
        memset(page, 0, 120)
        page_needs_reset = 1
    if page_needs_reset != 0:
        _backend4_zpage_reset(page, null(), size)
        store_i32(page, 28, generation)
        store_ptr(page, 56, _zpage_page_head())
        _set_zpage_page_head(page)
    span = load_ptr(page, 72)
    capacity: int = load_i64(page, 16)
    span_capacity: int = load_i64(page, 80)
    allocated: int = load_i64(page, 64)
    if (
        ptr_is_null(span) != 0
        or span_capacity < capacity
        or allocated < 0
        or capacity - allocated < alloc_size
    ):
        _object_graph_unlock()
        return null()
    obj = ptr_add(span, allocated)
    memset(obj, 0, alloc_size)
    store_i64(page, 64, allocated + alloc_size)
    store_i64(page, 88, load_i64(page, 88) + 1)
    _backend4_set_active_page(page)
    _object_graph_unlock()
    return obj


def _backend4_zpage_track_alloc(owner, size: int):
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return null()
    node = _backend4_zpage_node_alloc()
    if ptr_is_null(node) != 0:
        return null()
    page = null()
    existing_offset: int = -1
    if (load_i32(owner, 12) & 65536) != 0:
        page = _backend4_zpage_find_page_for_addr(owner, size)
        if ptr_is_null(page) == 0:
            span = load_ptr(page, 72)
            existing_offset = ptr_diff(owner, span)
    if ptr_is_null(page) != 0:
        page = _backend4_zpage_find_reusable_page(owner, size)
    if ptr_is_null(page) != 0:
        page = _backend4_zpage_pop_free_page(size)
    if ptr_is_null(page) != 0:
        page = malloc(120)
        if ptr_is_null(page) != 0:
            _backend4_zpage_node_release(node)
            return null()
        memset(page, 0, 120)
    if existing_offset < 0 and load_i64(page, 32) <= 0:
        _backend4_zpage_reset(page, owner, size)
        store_ptr(page, 56, _zpage_page_head())
        _set_zpage_page_head(page)
    store_ptr(node, 0, owner)
    store_ptr(node, 8, page)
    allocated: int = load_i64(page, 64)
    if existing_offset >= 0:
        pending: int = load_i64(page, 88)
        if pending > 0:
            store_i64(page, 88, pending - 1)
        store_i64(node, 24, existing_offset)
    else:
        store_i64(node, 24, allocated)
    store_i64(node, 32, size)
    store_ptr(node, 64, null())
    if existing_offset < 0:
        store_i64(page, 64, allocated + _backend4_align_alloc_size(size))
    store_i64(page, 8, load_i64(page, 8) + size)
    store_i64(page, 32, load_i64(page, 32) + 1)
    _backend4_set_active_page(page)
    _backend4_zpage_link_node(node)
    return node


def _backend4_zpage_unlink_page(page) -> None:
    if ptr_is_null(page) != 0:
        return
    prev = null()
    cur = _zpage_page_head()
    while ptr_is_null(cur) == 0:
        nxt = load_ptr(cur, 56)
        if ptr_eq(cur, page) != 0:
            if ptr_is_null(prev) != 0:
                _set_zpage_page_head(nxt)
            else:
                store_ptr(prev, 56, nxt)
            return
        prev = cur
        cur = nxt


def _backend4_zpage_find_owner_for_page(page):
    if ptr_is_null(page) != 0:
        return null()
    node = _backend4_zpage_page_head(page)
    if ptr_is_null(node) != 0:
        return null()
    return load_ptr(node, 0)


def _backend4_zpage_remove_payload_spans(owner_node) -> None:
    # per-owner chain (node@48): O(own spans), not O(all spans) — the
    # global-list walk was 95% of gc4 churn wall time.
    if ptr_is_null(owner_node) != 0:
        return
    node = load_ptr(owner_node, 64)
    store_ptr(owner_node, 64, null())
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 40)
        page = load_ptr(node, 32)
        size: int = load_i64(node, 16)
        if ptr_is_null(page) == 0 and size > 0:
            offset: int = load_i64(node, 24)
            allocated: int = load_i64(page, 64)
            if offset >= 0 and allocated == offset + size:
                store_i64(page, 64, offset)
            used: int = load_i64(page, 8)
            if used >= size:
                store_i64(page, 8, used - size)
            else:
                store_i64(page, 8, 0)
        free(node)
        node = nxt


def _backend4_zpage_remove_payload_span_base(owner_node, base) -> int:
    if ptr_is_null(owner_node) != 0 or ptr_is_null(base) != 0:
        return 0
    removed: int = 0
    prev = null()
    node = load_ptr(owner_node, 64)
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 40)
        if ptr_eq(load_ptr(node, 8), base) == 0:
            prev = node
            node = nxt
            continue
        if ptr_is_null(prev) != 0:
            store_ptr(owner_node, 64, nxt)
        else:
            store_ptr(prev, 40, nxt)
        page = load_ptr(node, 32)
        size: int = load_i64(node, 16)
        if ptr_is_null(page) == 0 and size > 0:
            used: int = load_i64(page, 8)
            if used >= size:
                store_i64(page, 8, used - size)
            else:
                store_i64(page, 8, 0)
        free(node)
        removed = removed + 1
        node = nxt
    return removed


def _backend4_zpage_remove(owner) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    obj_node = pcc_gc_object_index_find(owner)
    node = null()
    if ptr_is_null(obj_node) == 0:
        node = _object_node_zpage(obj_node)
        _set_object_node_zpage(obj_node, null())
    indexed = pcc_gc_zpage_owner_index_find(owner)
    if ptr_is_null(node) != 0:
        node = indexed
    if ptr_is_null(node) != 0:
        scan = _zpage_head()
        while ptr_is_null(scan) == 0:
            if ptr_eq(load_ptr(scan, 0), owner) != 0:
                node = scan
                break
            scan = load_ptr(scan, 16)
    if ptr_is_null(node) != 0:
        return
    page = load_ptr(node, 8)
    _backend4_zpage_unlink_node(node)
    if ptr_is_null(page) == 0:
        _backend4_zpage_remove_payload_spans(node)
        size: int = load_i64(node, 32)
        if size <= 0:
            size = _object_known_size(owner)
        used: int = load_i64(page, 8)
        if size > 0 and used >= size:
            store_i64(page, 8, used - size)
        elif size > 0:
            store_i64(page, 8, 0)
        count: int = load_i64(page, 32)
        if count > 0:
            count = count - 1
            store_i64(page, 32, count)
        if ptr_eq(load_ptr(page, 0), owner) != 0:
            store_ptr(page, 0, _backend4_zpage_find_owner_for_page(page))
        pending: int = load_i64(page, 88)
        if count <= 0 and pending <= 0:
            if load_i64(page, 96) <= 0:
                _backend4_zpage_unlink_page(page)
                _backend4_zpage_recycle(page)
            else:
                # Defer: un-healed slots may still reference this span
                # through forwarding entries; destroying it would free
                # memory the lazy-heal read barrier must still read.
                # The page stays on the page list (addr lookup) but is
                # never handed out for allocation.
                store_i32(page, 104, 1)
                _backend4_clear_active_page(page)
    _backend4_zpage_node_release(node)


def _backend4_zpage_population(metric: int) -> int:
    _object_graph_lock()
    page = _zpage_page_head()
    total: int = 0
    while ptr_is_null(page) == 0:
        if ptr_is_null(page) == 0:
            used: int = load_i64(page, 8)
            capacity: int = load_i64(page, 16)
            allocated: int = load_i64(page, 64)
            page_class: int = load_i32(page, 24)
            if metric == 0:
                total = total + 1
            elif metric == 1:
                total = total + capacity
            elif metric == 2:
                if capacity > used:
                    total = total + capacity - used
            elif metric == 3:
                if page_class == 2:
                    total = total + 1
            elif metric == 4:
                total = total + load_i64(page, 40)
            elif metric == 5:
                if load_i64(page, 48) > 0:
                    total = total + 1
            elif metric == 6:
                if capacity > used:
                    total = total + 1
            elif metric == 7:
                if load_i32(page, 28) == 1:
                    total = total + 1
            elif metric == 8:
                if load_i32(page, 28) == 2:
                    total = total + 1
            elif metric == 9:
                total = total + load_i64(page, 48)
            elif metric == 10:
                total = total + allocated
            elif metric == 11:
                if allocated > used:
                    total = total + allocated - used
            elif metric == 12:
                total = total + load_i64(page, 80)
        page = load_ptr(page, 56)
    _object_graph_unlock()
    return total


@c_abi_export("pcc_gc_backend4_zpage_count")
def pcc_gc_backend4_zpage_count() -> int:
    return _backend4_zpage_population(0)


@c_abi_export("pcc_gc_backend4_zpage_capacity_bytes")
def pcc_gc_backend4_zpage_capacity_bytes() -> int:
    return _backend4_zpage_population(1)


@c_abi_export("pcc_gc_backend4_zpage_fragmentation_bytes")
def pcc_gc_backend4_zpage_fragmentation_bytes() -> int:
    return _backend4_zpage_population(2)


@c_abi_export("pcc_gc_backend4_zpage_large_pages")
def pcc_gc_backend4_zpage_large_pages() -> int:
    return _backend4_zpage_population(3)


@c_abi_export("pcc_gc_backend4_zpage_remembered_slots")
def pcc_gc_backend4_zpage_remembered_slots() -> int:
    return _backend4_zpage_population(4)


@c_abi_export("pcc_gc_backend4_zpage_remembered_cards")
def pcc_gc_backend4_zpage_remembered_cards() -> int:
    return _backend4_zpage_population(9)


@c_abi_export("pcc_gc_backend4_zpage_remembered_card_ratio_per_mille")
def pcc_gc_backend4_zpage_remembered_card_ratio_per_mille() -> int:
    # Read-only density telemetry; selector policy stays on absolute pressure.
    pages: int = pcc_gc_backend4_zpage_count()
    if pages <= 0:
        return 0
    capacity: int = pages * 64
    if capacity <= 0:
        return 0
    cards: int = pcc_gc_backend4_zpage_remembered_cards()
    if cards <= 0:
        return 0
    if cards >= capacity:
        return 1000
    return (cards * 1000) // capacity


@c_abi_export("pcc_gc_backend4_zpage_dirty_pages")
def pcc_gc_backend4_zpage_dirty_pages() -> int:
    return _backend4_zpage_population(5)


@c_abi_export("pcc_gc_backend4_zpage_fragmented_pages")
def pcc_gc_backend4_zpage_fragmented_pages() -> int:
    return _backend4_zpage_population(6)


@c_abi_export("pcc_gc_backend4_zpage_young_pages")
def pcc_gc_backend4_zpage_young_pages() -> int:
    return _backend4_zpage_population(7)


@c_abi_export("pcc_gc_backend4_zpage_old_pages")
def pcc_gc_backend4_zpage_old_pages() -> int:
    return _backend4_zpage_population(8)


def _backend4_zpage_free_population(metric: int) -> int:
    _object_graph_lock()
    page = _zpage_free_page_head()
    total: int = 0
    while ptr_is_null(page) == 0:
        if metric == 0:
            total = total + 1
        elif metric == 1:
            total = total + load_i64(page, 16)
        elif metric == 2:
            total = total + load_i64(page, 80)
        page = load_ptr(page, 56)
    _object_graph_unlock()
    return total


@c_abi_export("pcc_gc_backend4_zpage_free_pages")
def pcc_gc_backend4_zpage_free_pages() -> int:
    return _backend4_zpage_free_population(0)


@c_abi_export("pcc_gc_backend4_zpage_free_capacity_bytes")
def pcc_gc_backend4_zpage_free_capacity_bytes() -> int:
    return _backend4_zpage_free_population(1)


@c_abi_export("pcc_gc_backend4_zpage_free_span_bytes")
def pcc_gc_backend4_zpage_free_span_bytes() -> int:
    return _backend4_zpage_free_population(2)


@c_abi_export("pcc_gc_backend4_zpage_used_bytes")
def pcc_gc_backend4_zpage_used_bytes() -> int:
    capacity: int = pcc_gc_backend4_zpage_capacity_bytes()
    fragmentation: int = pcc_gc_backend4_zpage_fragmentation_bytes()
    if capacity <= fragmentation:
        return 0
    return capacity - fragmentation


@c_abi_export("pcc_gc_backend4_zpage_allocated_bytes")
def pcc_gc_backend4_zpage_allocated_bytes() -> int:
    return _backend4_zpage_population(10)


@c_abi_export("pcc_gc_backend4_zpage_reclaimable_gap_bytes")
def pcc_gc_backend4_zpage_reclaimable_gap_bytes() -> int:
    return _backend4_zpage_population(11)


@c_abi_export("pcc_gc_backend4_zpage_span_bytes")
def pcc_gc_backend4_zpage_span_bytes() -> int:
    return _backend4_zpage_population(12)


@c_abi_export("pcc_gc_backend4_zpage_owner_offset_bytes")
def pcc_gc_backend4_zpage_owner_offset_bytes(owner) -> int:
    _init_config()
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return -1
    return load_i64(node, 24)


@c_abi_export("pcc_gc_backend4_zpage_owner_size_bytes")
def pcc_gc_backend4_zpage_owner_size_bytes(owner) -> int:
    _init_config()
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return -1
    return load_i64(node, 32)


@c_abi_export("pcc_gc_backend4_zpage_owner_span_card")
def pcc_gc_backend4_zpage_owner_span_card(owner) -> int:
    offset: int = pcc_gc_backend4_zpage_owner_offset_bytes(owner)
    if offset < 0:
        return -1
    return (offset // 512) % 64


def _backend4_zpage_payload_offset_for_slot(owner_node, slot) -> int:
    if ptr_is_null(owner_node) != 0:
        return -1
    if ptr_is_null(slot) != 0:
        return -1
    span = load_ptr(owner_node, 64)
    while ptr_is_null(span) == 0:
        base = load_ptr(span, 8)
        size: int = load_i64(span, 16)
        offset: int = load_i64(span, 24)
        if ptr_is_null(base) == 0 and size > 0 and offset >= 0:
            delta: int = ptr_diff(slot, base)
            if delta >= 0 and delta < size:
                return offset + delta
        span = load_ptr(span, 40)
    return -1


@c_abi_export("pcc_gc_backend4_zpage_owner_slot_span_card")
def pcc_gc_backend4_zpage_owner_slot_span_card(owner, slot) -> int:
    _init_config()
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    if ptr_is_null(slot) != 0:
        return -1
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return -1
    span_offset: int = load_i64(node, 24)
    size: int = load_i64(node, 32)
    if span_offset < 0:
        return -1
    if size > 0:
        inline_delta: int = ptr_diff(slot, owner)
        if inline_delta >= 0 and inline_delta < size:
            span_offset = span_offset + inline_delta
        else:
            payload_offset: int = _backend4_zpage_payload_offset_for_slot(
                node,
                slot,
            )
            if payload_offset >= 0:
                span_offset = payload_offset
    return (span_offset // 512) % 64


@c_abi_export("pcc_gc_backend4_zpage_register_owner_payload_span")
def pcc_gc_backend4_zpage_register_owner_payload_span(
    owner, base, size_bytes: int
) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    if ptr_is_null(base) != 0 or size_bytes <= 0:
        return -1
    backend: int = _init_config()
    if backend != 4:
        return -1
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return -1
    page = load_ptr(node, 8)
    if ptr_is_null(page) != 0:
        return -1
    span_existing = load_ptr(node, 64)
    while ptr_is_null(span_existing) == 0:
        if ptr_eq(load_ptr(span_existing, 8), base) != 0:
            if ptr_eq(load_ptr(span_existing, 32), page) == 0:
                return -1
            offset_existing: int = load_i64(span_existing, 24)
            if offset_existing < 0:
                return -1
            capacity_existing: int = load_i64(page, 16)
            if size_bytes > capacity_existing - offset_existing:
                return -1
            old_size: int = load_i64(span_existing, 16)
            used_existing: int = load_i64(page, 8)
            if size_bytes >= old_size:
                store_i64(page, 8, used_existing + size_bytes - old_size)
            else:
                delta_existing: int = old_size - size_bytes
                if used_existing >= delta_existing:
                    store_i64(page, 8, used_existing - delta_existing)
                else:
                    store_i64(page, 8, 0)
            store_i64(span_existing, 16, size_bytes)
            end_existing: int = offset_existing + size_bytes
            allocated_existing: int = load_i64(page, 64)
            if allocated_existing < end_existing:
                store_i64(page, 64, end_existing)
            return offset_existing
        span_existing = load_ptr(span_existing, 40)
    allocated: int = load_i64(page, 64)
    capacity: int = load_i64(page, 16)
    if allocated > capacity or size_bytes > capacity - allocated:
        return -1
    span = malloc(48)
    if ptr_is_null(span) != 0:
        return -1
    store_ptr(span, 0, owner)
    store_ptr(span, 8, base)
    store_i64(span, 16, size_bytes)
    store_i64(span, 24, allocated)
    store_ptr(span, 32, page)
    store_ptr(span, 40, load_ptr(node, 64))
    store_ptr(node, 64, span)
    store_i64(page, 64, allocated + size_bytes)
    store_i64(page, 8, load_i64(page, 8) + size_bytes)
    return allocated


@c_abi_export("pcc_gc_backend4_zpage_unregister_owner_payload_span")
def pcc_gc_backend4_zpage_unregister_owner_payload_span(owner, base) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    if ptr_is_null(base) != 0:
        return -1
    _init_config()
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return 0
    return _backend4_zpage_remove_payload_span_base(node, base)


@c_abi_export("pcc_gc_backend4_zpage_retarget_owner_payload_span")
def pcc_gc_backend4_zpage_retarget_owner_payload_span(
    owner, old_base, new_base, size_bytes: int
) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    if ptr_is_null(old_base) != 0 or ptr_is_null(new_base) != 0:
        return -1
    if size_bytes <= 0:
        return -1
    _init_config()
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return -1
    page = load_ptr(node, 8)
    if ptr_is_null(page) != 0:
        return -1
    span = load_ptr(node, 64)
    while ptr_is_null(span) == 0:
        if ptr_eq(load_ptr(span, 8), old_base) != 0:
            if ptr_eq(load_ptr(span, 32), page) == 0:
                return -1
            offset: int = load_i64(span, 24)
            if offset < 0:
                return -1
            capacity: int = load_i64(page, 16)
            if size_bytes > capacity - offset:
                return -1
            old_size: int = load_i64(span, 16)
            used: int = load_i64(page, 8)
            if size_bytes >= old_size:
                store_i64(page, 8, used + size_bytes - old_size)
            else:
                delta: int = old_size - size_bytes
                if used >= delta:
                    store_i64(page, 8, used - delta)
                else:
                    store_i64(page, 8, 0)
            store_ptr(span, 8, new_base)
            store_i64(span, 16, size_bytes)
            end: int = offset + size_bytes
            allocated: int = load_i64(page, 64)
            if allocated < end:
                store_i64(page, 64, end)
            return offset
        span = load_ptr(span, 40)
    return -1


@c_abi_export("pcc_gc_backend4_zpage_fragmentation_per_mille")
def pcc_gc_backend4_zpage_fragmentation_per_mille() -> int:
    capacity: int = pcc_gc_backend4_zpage_capacity_bytes()
    if capacity <= 0:
        return 0
    fragmentation: int = pcc_gc_backend4_zpage_fragmentation_bytes()
    if fragmentation <= 0:
        return 0
    if fragmentation >= capacity:
        return 1000
    return (fragmentation * 1000) // capacity


@c_abi_export("pcc_gc_backend4_zpage_policy_score")
def pcc_gc_backend4_zpage_policy_score() -> int:
    return (
        pcc_gc_backend4_zpage_fragmentation_bytes()
        + pcc_gc_backend4_fragmentation_backlog_bytes()
        + pcc_gc_backend4_evacuation_incomplete_batches()
        + pcc_gc_backend4_zpage_remembered_slots()
        + pcc_gc_backend4_zpage_remembered_cards()
        + pcc_gc_backend4_zpage_dirty_pages()
        + pcc_gc_backend4_zpage_fragmented_pages()
        + pcc_gc_backend4_zpage_old_pages()
    )


@c_abi_export("pcc_gc_backend4_small_page_candidate_score")
def pcc_gc_backend4_small_page_candidate_score() -> int:
    return load_i32(global_addr("pcc_gc_backend4_small_page_candidates"), 0)


@c_abi_export("pcc_gc_backend4_medium_page_candidate_score")
def pcc_gc_backend4_medium_page_candidate_score() -> int:
    return load_i32(global_addr("pcc_gc_backend4_medium_page_candidates"), 0)


@c_abi_export("pcc_gc_backend4_evacuation_candidate_bytes")
def pcc_gc_backend4_evacuation_candidate_bytes() -> int:
    return load_i32(global_addr("pcc_gc_backend4_evacuation_candidate_bytes_count"), 0)


@c_abi_export("pcc_gc_backend4_small_page_candidate_bytes")
def pcc_gc_backend4_small_page_candidate_bytes() -> int:
    return load_i32(global_addr("pcc_gc_backend4_small_page_candidate_bytes_count"), 0)


@c_abi_export("pcc_gc_backend4_medium_page_candidate_bytes")
def pcc_gc_backend4_medium_page_candidate_bytes() -> int:
    return load_i32(global_addr("pcc_gc_backend4_medium_page_candidate_bytes_count"), 0)


@c_abi_export("pcc_gc_backend4_evacuation_candidate_zpage_bytes")
def pcc_gc_backend4_evacuation_candidate_zpage_bytes() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count"), 0
    )


@c_abi_export("pcc_gc_backend4_small_page_candidate_zpage_bytes")
def pcc_gc_backend4_small_page_candidate_zpage_bytes() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_small_page_candidate_zpage_bytes_count"), 0
    )


@c_abi_export("pcc_gc_backend4_medium_page_candidate_zpage_bytes")
def pcc_gc_backend4_medium_page_candidate_zpage_bytes() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count"), 0
    )


@c_abi_export("pcc_gc_backend4_evacuation_page_candidate_score")
def pcc_gc_backend4_evacuation_page_candidate_score() -> int:
    return _backend4_evacuation_page_population(0)


def _backend4_evacuation_page_population(metric: int) -> int:
    node = _evacuation_page_head()
    total: int = 0
    while ptr_is_null(node) == 0:
        page = load_ptr(node, 0)
        if ptr_is_null(page) == 0:
            if metric == 0:
                total = total + 1
            elif metric == 1:
                total = total + load_i64(page, 8)
            elif metric == 2:
                total = total + load_i64(page, 48)
        node = load_ptr(node, 8)
    return total


@c_abi_export("pcc_gc_backend4_evacuation_page_candidate_bytes")
def pcc_gc_backend4_evacuation_page_candidate_bytes() -> int:
    return _backend4_evacuation_page_population(1)


@c_abi_export("pcc_gc_backend4_evacuation_page_dirty_cards")
def pcc_gc_backend4_evacuation_page_dirty_cards() -> int:
    return _backend4_evacuation_page_population(2)


@c_abi_export("pcc_gc_backend4_store_buffer_drain_batches")
def pcc_gc_backend4_store_buffer_drain_batches() -> int:
    return load_i32(global_addr("pcc_gc_backend4_store_buffer_drain_batches_count"), 0)


@c_abi_export("pcc_gc_backend4_store_buffer_drained_entries")
def pcc_gc_backend4_store_buffer_drained_entries() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_store_buffer_drained_entries_count"), 0
    )


@c_abi_export("pcc_gc_backend4_store_buffer_duplicate_skips")
def pcc_gc_backend4_store_buffer_duplicate_skips() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_store_buffer_duplicate_skips_count"), 0
    )


@c_abi_export("pcc_gc_backend4_store_buffer_high_water")
def pcc_gc_backend4_store_buffer_high_water() -> int:
    return load_i32(global_addr("pcc_gc_backend4_store_buffer_high_water_count"), 0)


@c_abi_export("pcc_gc_backend4_store_buffer_owner_fanout_high_water")
def pcc_gc_backend4_store_buffer_owner_fanout_high_water() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_store_buffer_owner_fanout_high_water_count"), 0
    )


@c_abi_export("pcc_gc_backend4_store_buffer_owner_count_high_water")
def pcc_gc_backend4_store_buffer_owner_count_high_water() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_store_buffer_owner_count_high_water_count"), 0
    )


@c_abi_export("pcc_gc_backend4_store_buffer_incomplete_drains")
def pcc_gc_backend4_store_buffer_incomplete_drains() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_store_buffer_incomplete_drains_count"), 0
    )


@c_abi_export("pcc_gc_backend4_evacuation_incomplete_batches")
def pcc_gc_backend4_evacuation_incomplete_batches() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_evacuation_incomplete_batches_count"), 0
    )


@c_abi_export("pcc_gc_backend4_store_buffer_batch_capacity")
def pcc_gc_backend4_store_buffer_batch_capacity() -> int:
    return _backend4_store_buffer_batch_capacity()


@c_abi_export("pcc_gc_backend4_store_buffer_max_batch_size")
def pcc_gc_backend4_store_buffer_max_batch_size() -> int:
    return load_i32(global_addr("pcc_gc_backend4_store_buffer_max_batch_size_count"), 0)


@c_abi_export("pcc_gc_backend4_store_buffer_full_batches")
def pcc_gc_backend4_store_buffer_full_batches() -> int:
    return load_i32(global_addr("pcc_gc_backend4_store_buffer_full_batches_count"), 0)


@c_abi_export("pcc_gc_backend4_store_buffer_medium_capacity")
def pcc_gc_backend4_store_buffer_medium_capacity() -> int:
    return _backend4_store_buffer_medium_capacity()


@c_abi_export("pcc_gc_backend4_store_buffer_medium_pending")
def pcc_gc_backend4_store_buffer_medium_pending() -> int:
    return _backend4_store_buffer_medium_count()


@c_abi_export("pcc_gc_backend4_store_buffer_medium_flushes")
def pcc_gc_backend4_store_buffer_medium_flushes() -> int:
    return load_i32(global_addr("pcc_gc_backend4_store_buffer_medium_flushes_count"), 0)


@c_abi_export("pcc_gc_backend4_store_buffer_medium_flushed_entries")
def pcc_gc_backend4_store_buffer_medium_flushed_entries() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_flushed_entries_count"), 0
    )


@c_abi_export("pcc_gc_backend4_store_buffer_medium_full_flushes")
def pcc_gc_backend4_store_buffer_medium_full_flushes() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_store_buffer_medium_full_flushes_count"), 0
    )


@c_abi_export("pcc_gc_backend4_store_buffer_cross_thread_medium_flushes")
def pcc_gc_backend4_store_buffer_cross_thread_medium_flushes() -> int:
    return 0


@c_abi_export("pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries")
def pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries() -> int:
    return 0


@c_abi_export("pcc_gc_backend4_remembered_set_entries")
def pcc_gc_backend4_remembered_set_entries() -> int:
    return load_i32(global_addr("pcc_gc_backend4_remembered_set_entries_count"), 0)


@c_abi_export("pcc_gc_backend4_remembered_set_duplicate_skips")
def pcc_gc_backend4_remembered_set_duplicate_skips() -> int:
    return load_i32(
        global_addr("pcc_gc_backend4_remembered_set_duplicate_skips_count"), 0
    )


@c_abi_export("pcc_gc_backend4_remembered_set_high_water")
def pcc_gc_backend4_remembered_set_high_water() -> int:
    return load_i32(global_addr("pcc_gc_backend4_remembered_set_high_water_count"), 0)


@c_abi_export("pcc_gc_backend4_remembered_page_entries")
def pcc_gc_backend4_remembered_page_entries() -> int:
    return 0


@c_abi_export("pcc_gc_backend4_remembered_page_slot_entries")
def pcc_gc_backend4_remembered_page_slot_entries() -> int:
    return 0


@c_abi_export("pcc_gc_backend4_remembered_page_high_water")
def pcc_gc_backend4_remembered_page_high_water() -> int:
    return 0


@c_abi_export("pcc_gc_backend4_remembered_page_contains_slot")
def pcc_gc_backend4_remembered_page_contains_slot(slot) -> int:
    node = _remembered_set_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 8), slot) != 0:
            return 1
        node = load_ptr(node, 16)
    return 0


@c_abi_export("pcc_gc_backend4_remembered_page_clear_slot")
def pcc_gc_backend4_remembered_page_clear_slot(slot) -> int:
    return _backend4_remembered_set_remove_slot(slot)


@c_abi_export("pcc_gc_backend4_zpage_contains_remembered_card")
def pcc_gc_backend4_zpage_contains_remembered_card(owner, slot) -> int:
    # Mirror fallback: the pcc-Python runtime does not yet model pointer-page
    # card grouping, so this answers exact owner+slot membership.
    return _backend4_remembered_set_contains(owner, slot)


@c_abi_export("pcc_gc_backend4_zpage_clear_remembered_card")
def pcc_gc_backend4_zpage_clear_remembered_card(owner, slot) -> int:
    # Mirror fallback: exact owner+slot clear, not full card clear.
    if _backend4_remembered_set_contains(owner, slot) == 0:
        return 0
    return _backend4_remembered_set_remove_slot(slot)


@c_abi_export("pcc_gc_backend4_forwarding_entries")
def pcc_gc_backend4_forwarding_entries() -> int:
    _object_graph_lock()
    node = _forwarding_head()
    count: int = 0
    while ptr_is_null(node) == 0:
        count += 1
        node = load_ptr(node, 16)
    _object_graph_unlock()
    return count


@c_abi_export("pcc_gc_backend4_stable_id_entries")
def pcc_gc_backend4_stable_id_entries() -> int:
    node = _identity_head()
    count: int = 0
    while ptr_is_null(node) == 0:
        count += 1
        node = load_ptr(node, 16)
    return count


@c_abi_export("pcc_gc_backend4_verify_no_old_addresses")
def pcc_gc_backend4_verify_no_old_addresses() -> int:
    if pcc_gc_backend() != 4:
        return 1
    node = _forwarding_head()
    while ptr_is_null(node) == 0:
        from_obj = load_ptr(node, 0)
        to_obj = load_ptr(node, 8)
        if ptr_is_null(from_obj) != 0:
            return 0
        if ptr_is_null(to_obj) != 0:
            return 0
        if ptr_eq(from_obj, to_obj) != 0:
            return 0
        if (load_i32(to_obj, 12) & 256) != 0:
            return 0
        node = load_ptr(node, 16)
    return 1


def _step_tracing(remaining_budget: int) -> int:
    if remaining_budget <= 0:
        return 0

    _object_graph_lock()
    local_processed: int = 0
    active: int = load_i32(global_addr("pcc_gc_mark_active"), 0)
    requested: int = load_i32(global_addr("pcc_gc_cycle_requested"), 0)
    if active == 0:
        if requested == 0:
            _object_graph_unlock()
            return local_processed
        if pcc_threads_enabled() != 0:
            if load_i32(global_addr("pcc_gc_in_auto_step"), 0) != 0:
                _object_graph_unlock()
                return local_processed
        _begin_mark_cycle()

    node = _trace_cursor()
    if ptr_is_null(node) != 0:
        node = _object_head()
    while ptr_is_null(node) == 0 and local_processed < remaining_budget:
        nxt = load_ptr(node, 16)
        if load_i64(node, 32) != 0:
            node = nxt
            continue
        o = load_ptr(node, 0)
        if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
            node = nxt
            continue
        flags: int = load_i32(o, 12)
        if (flags & 16) != 0:
            _trace_referents(o)
            _dec_gray_count()
            store_i32(o, 12, (flags & ~56) | 32)
            local_processed = local_processed + 1
        node = nxt
    _set_trace_cursor(node)

    if ptr_is_null(_trace_cursor()) != 0:
        if _gray_count() != 0:
            _set_trace_cursor(_object_head())
        else:
            if _finish_tracing_cycle() != 0:
                _set_trace_cursor(null())
                _set_gray_count(0)
                store_i32(global_addr("pcc_gc_mark_active"), 0, 0)
                store_i32(global_addr("pcc_gc_cycle_requested"), 0, 0)

    _object_graph_unlock()
    return local_processed


def _step_generational_promotion(
    remaining_budget: int,
    promote_all_young: int,
) -> int:
    if remaining_budget <= 0:
        return 0
    _object_graph_lock()
    _promote_frame_roots(remaining_budget)
    _promote_tls_exception_root()
    local_processed: int = 0
    local_processed = local_processed + _backend3_drain_remembered_owners(
        remaining_budget - local_processed
    )
    if promote_all_young != 0:
        node = _object_head()
        while ptr_is_null(node) == 0 and local_processed < remaining_budget:
            if _object_node_is_active(node) == 0:
                node = _object_node_next(node)
                continue
            o = load_ptr(node, 0)
            flags: int = load_i32(o, 12)
            if (flags & 128) != 0:
                if ptr_is_null(_forwarding_find(o)) == 0:
                    node = _object_node_next(node)
                    continue
                _promote_young_if_known(o)
                after_flags: int = load_i32(o, 12)
                if (after_flags & 128) == 0 or ptr_is_null(_forwarding_find(o)) == 0:
                    local_processed = local_processed + 1
                    if (local_processed % 16) == 0:
                        pcc_thread_safepoint()
            node = _object_node_next(node)

    if local_processed > 0:
        pcc_thread_safepoint()
    _object_graph_unlock()
    return local_processed


def _step_colored_remembered_roots(remaining_budget: int) -> int:
    if remaining_budget <= 0:
        return 0
    _object_graph_lock()
    local_processed: int = 0
    local_drained: int = 0
    batch_limit: int = remaining_budget
    capacity: int = _backend4_store_buffer_batch_capacity()
    if batch_limit > capacity:
        batch_limit = capacity
    node = _store_buffer_head()
    _backend4_store_buffer_flush_medium_locked()
    node = _store_buffer_head()
    while ptr_is_null(node) == 0 and local_drained < batch_limit:
        nxt = load_ptr(node, 24)
        _set_store_buffer_head(nxt)
        owner = load_ptr(node, 0)
        slot = load_ptr(node, 8)
        value = load_ptr(node, 16)
        free(node)
        _backend4_store_buffer_dec()
        local_drained = local_drained + 1
        node = nxt
        if _is_known_object(owner) == 0:
            py_decref(value)
            continue
        flags: int = load_i32(owner, 12)
        if (flags & 512) != 0:
            _promote_young_if_known(value)
            if ptr_is_null(slot) == 0:
                _promote_young_slot(slot, 0)
            else:
                _trace_referents_for_promotion(owner)
            if _backend4_store_buffer_owner_pending(owner) == 0:
                store_i32(owner, 12, flags & ~512)
            local_processed = local_processed + 1
            py_decref(value)
            if (local_processed % 16) == 0:
                pcc_thread_safepoint()
        else:
            py_decref(value)
    if local_drained > 0:
        batches: int = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_drain_batches_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_drain_batches_count"),
            0,
            batches + 1,
        )
        drained_entries: int = load_i32(
            global_addr("pcc_gc_backend4_store_buffer_drained_entries_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_store_buffer_drained_entries_count"),
            0,
            drained_entries + local_drained,
        )
        _backend4_store_buffer_note_max_batch(local_drained)
        if local_drained >= _backend4_store_buffer_batch_capacity():
            full_batches: int = load_i32(
                global_addr("pcc_gc_backend4_store_buffer_full_batches_count"), 0
            )
            store_i32(
                global_addr("pcc_gc_backend4_store_buffer_full_batches_count"),
                0,
                full_batches + 1,
            )
        if ptr_is_null(_store_buffer_head()) == 0:
            incomplete: int = load_i32(
                global_addr("pcc_gc_backend4_store_buffer_incomplete_drains_count"), 0
            )
            store_i32(
                global_addr("pcc_gc_backend4_store_buffer_incomplete_drains_count"),
                0,
                incomplete + 1,
            )
    if local_processed > 0:
        pcc_thread_safepoint()
    _object_graph_unlock()
    return local_processed


def _step_colored_generation_aging(remaining_budget: int) -> int:
    if remaining_budget <= 0:
        return 0
    _object_graph_lock()
    local_processed: int = 0
    node = _object_head()
    while ptr_is_null(node) == 0 and local_processed < remaining_budget:
        if _object_node_is_active(node) == 0:
            node = _object_node_next(node)
            continue
        o = load_ptr(node, 0)
        if ptr_is_null(_forwarding_find(o)) == 0:
            node = _object_node_next(node)
            continue
        flags: int = load_i32(o, 12)
        if (flags & 128) != 0:
            store_i32(o, 12, (flags & ~128) | 256)
            _backend4_zpage_note_owner_promoted(o)
            promotions: int = load_i32(
                global_addr("pcc_gc_backend4_young_promotions"), 0
            )
            store_i32(
                global_addr("pcc_gc_backend4_young_promotions"), 0, promotions + 1
            )
            local_processed = local_processed + 1
            if (local_processed % 16) == 0:
                pcc_thread_safepoint()
        node = _object_node_next(node)
    if local_processed > 0:
        pcc_thread_safepoint()
    _object_graph_unlock()
    return local_processed


@c_abi_export("pcc_gc_step")
def pcc_gc_step(budget: int) -> int:
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if budget <= 0:
        return 0
    _counter_inc(5, 1)
    start_us: int = pcc_runtime_now_us()
    processed: int = 0

    if backend == 1 or backend == 2:
        processed = processed + _step_tracing(budget)
    elif backend == 3:
        processed = processed + _step_generational_promotion(budget, 1)
        if processed < budget:
            if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0:
                if (
                    load_i32(global_addr("pcc_gc_cycle_requested"), 0) != 0
                    or load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
                    or _has_sweep_candidate() != 0
                ):
                    processed = processed + _step_tracing(budget - processed)
    elif backend == 4:
        if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0:
            processed = processed + _step_tracing(budget - processed)
        else:
            processed = processed + _step_colored_remembered_roots(budget - processed)
            if processed < budget:
                processed = processed + _step_colored_generation_aging(
                    budget - processed
                )
            if processed < budget:
                processed = processed + pcc_gc_backend4_evacuation_page_drain(
                    budget - processed
                )
            if processed < budget:
                selected: int = _backend4_select_relocation_pages(budget - processed)
                if selected > 0:
                    moved: int = pcc_gc_backend4_evacuation_page_drain(
                        budget - processed
                    )
                    if moved > 0:
                        processed = processed + moved
                    else:
                        processed = processed + selected

            if processed < budget and (
                load_i32(global_addr("pcc_gc_cycle_requested"), 0) != 0
                or load_i32(global_addr("pcc_gc_mark_active"), 0) != 0
                or _has_sweep_candidate() != 0
            ):
                stw: int = pcc_stop_the_world()
                processed = processed + _step_tracing(budget - processed)
                if stw == 0:
                    pcc_resume_world()
            if processed == 0:
                if load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0:
                    _object_graph_lock()
                    before: int = load_i32(
                        global_addr("pcc_gc_forwarding_population"), 0
                    )
                    if ptr_is_null(_relocation_set_head()) != 0:
                        if before > 0:
                            _backend4_remap_and_retire()
                    after: int = load_i32(
                        global_addr("pcc_gc_forwarding_population"), 0
                    )
                    _object_graph_unlock()
                    if before > after:
                        processed = processed + (before - after)
                    elif before > 0:
                        processed = processed + 1
    if backend == 1 or backend == 2:
        _discharge_debt(processed)
        if load_i32(global_addr("pcc_gc_mark_active"), 0) == 0:
            if load_i32(global_addr("pcc_gc_cycle_requested"), 0) == 0:
                store_i32(global_addr("pcc_gc_debt_bytes"), 0, 0)
    _record_pause(start_us, pcc_runtime_now_us())
    return processed


@c_abi_export("pcc_gc_has_tracing_sweep")
def pcc_gc_has_tracing_sweep() -> int:
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 1 and backend != 2 and backend != 3 and backend != 4:
        return 0
    if _has_sweep_candidate() != 0:
        return 1
    return 0


@c_abi_export("pcc_gc_collect_tracing")
def pcc_gc_collect_tracing() -> int:
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 1 and backend != 2 and backend != 3 and backend != 4:
        return 0
    if _has_sweep_candidate() == 0:
        return 0
    stw: int = pcc_stop_the_world()
    if stw != 0:
        return 0
    reclaimed: int = _sweep_unreachable(1024)
    if stw == 0:
        pcc_resume_world()
    return reclaimed


@c_abi_export("pcc_gc_begin_explicit_tracing_collect")
def pcc_gc_begin_explicit_tracing_collect() -> None:
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    store_i32(global_addr("pcc_gc_explicit_collect_active"), 0, 1)
    if backend != 0:
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)


@c_abi_export("pcc_gc_end_explicit_tracing_collect")
def pcc_gc_end_explicit_tracing_collect() -> None:
    store_i32(global_addr("pcc_gc_explicit_collect_active"), 0, 0)


@c_abi_export("pcc_gc_free_object_memory")
def pcc_gc_free_object_memory(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    flags: int = load_i32(o, 12)
    if (flags & 65536) != 0:
        return
    if (backend == 1 or backend == 2) and flags == 0:
        return
    if backend == 4 and (flags & 262144) == 0:
        _object_graph_lock()
        zpage_owner_node = pcc_gc_object_index_find(o)
        zpage_indexed: int = 0
        if ptr_is_null(zpage_owner_node) == 0:
            if ptr_is_null(_object_node_zpage(zpage_owner_node)) == 0:
                zpage_indexed = 1
        zpage_addr_owned: int = 0
        if zpage_indexed == 0:
            zpage_addr_owned = _backend4_zpage_owns_addr(o)
        _object_graph_unlock()
        if zpage_indexed != 0 or zpage_addr_owned != 0:
            return
    if (flags & 4096) == 0 and backend != 3:
        free(o)
        return
    if (flags & 4096) != 0 or backend == 3:
        _object_graph_lock()
        node = pcc_gc_object_index_find(o)
        if ptr_is_null(node) == 0:
            block = _object_node_minor_block(node)
            if _object_node_freeing(node) == 0:
                live: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
                size: int = _object_node_size(node)
                if size >= live:
                    store_i32(global_addr("pcc_gc_live_bytes"), 0, 0)
                else:
                    store_i32(global_addr("pcc_gc_live_bytes"), 0, live - size)
            if backend == 4:
                _backend4_zpage_remove(o)
            pcc_gc_object_index_remove(o)
            _unlink_object_node(node)
            _object_node_release(node)
            if ptr_is_null(block) == 0 or (flags & 4096) != 0:
                _object_graph_unlock()
                _minor_release_block(block)
                return
            _object_graph_unlock()
            free(o)
            return
        _object_graph_unlock()
    if backend == 3:
        _object_graph_lock()
        owner_block = _minor_block_containing(o)
        _object_graph_unlock()
        if ptr_is_null(owner_block) == 0:
            _minor_release_block(owner_block)
            return
        # Normal GC3 objects carry color/generation bits. A zero-flag object
        # here is a stale/non-owned shell, not a safe malloc allocation.
        if flags == 0:
            return
    free(o)


@c_abi_export("pcc_gc_note_alloc")
def pcc_gc_note_alloc(bytes: int) -> None:
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if bytes < 0:
        bytes = 0
    _counter_inc(0, 1)
    if backend == 1:
        debt: int = load_i32(global_addr("pcc_gc_debt_bytes"), 0) + bytes
        store_i32(global_addr("pcc_gc_debt_bytes"), 0, debt)
        _maybe_auto_step()
    elif backend == 2:
        _maybe_start_cms_worker()
        _note_cms_alloc(bytes)
    elif backend == 3:
        return


@c_abi_export("pcc_gc_note_object_allocated_sized")
def pcc_gc_note_object_allocated_sized(o, size: int) -> None:
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    if size < 16:
        size = 16
    if backend == 0:
        return
    pending_block = null()
    if backend == 1 or backend == 2:
        flags: int = load_i32(o, 12)
        color: int = 8
        if load_i32(global_addr("pcc_gc_mark_active"), 0) != 0:
            color = 32
        store_i32(o, 12, (flags & ~56) | color | 16384)
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    elif backend == 3:
        pending_block = _pending_minor_block()
        flags: int = load_i32(o, 12)
        new_flags: int = (flags & ~(56 | 384)) | 136
        if ptr_is_null(pending_block) == 0:
            new_flags = new_flags | 4096
        store_i32(o, 12, new_flags)
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    elif backend == 4:
        flags: int = load_i32(o, 12)
        new_flags: int = (flags & ~(56 | 2048 | 8192)) | 8
        if (flags & 384) == 0:
            new_flags = new_flags | 128
        store_i32(o, 12, new_flags)
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    if (
        (backend == 3 or backend == 4)
        and ptr_is_null(pending_block) != 0
        and _backend3_graph_leaf_tag(load_i32(o, 8)) != 0
    ):
        _set_pending_minor_block(null())
        global_store_ptr("pcc_gc_last_alloc", o)
        return
    node = _object_node_alloc()
    if ptr_is_null(node) == 0:
        _object_graph_lock()
        old_head = _object_head()
        store_ptr(node, 0, o)
        store_i64(node, 8, size)
        store_ptr(node, 16, old_head)
        store_ptr(node, 24, pending_block)
        store_i64(node, 32, 0)
        store_ptr(node, 40, null())
        store_ptr(node, 48, null())
        _set_object_node_gc_refs(node, 0)
        if ptr_is_null(old_head) == 0:
            _set_object_node_prev(old_head, node)
        _set_object_head(node)
        pcc_gc_object_index_insert(o, node)
        live: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
        store_i32(global_addr("pcc_gc_live_bytes"), 0, live + size)
        if backend == 4:
            _set_object_node_zpage(node, _backend4_zpage_track_alloc(o, size))
        _object_graph_unlock()
    _set_pending_minor_block(null())
    global_store_ptr("pcc_gc_last_alloc", o)


@c_abi_export("pcc_gc_note_object_allocated")
def pcc_gc_note_object_allocated(o) -> None:
    pcc_gc_note_object_allocated_sized(o, 16)


@c_abi_export("pcc_gc_note_object_freeing")
def pcc_gc_note_object_freeing(o) -> None:
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if ptr_is_null(o) != 0:
        return
    _object_graph_lock()
    if backend == 3 or backend == 4:
        _forwarding_remove(o)
        _forwarding_remove_target(o)
    _identity_remove(o)
    if backend == 4:
        _relocation_set_remove(o)
        _backend4_store_buffer_remove(o)
        _backend4_remembered_set_remove(o)
        zpage_flags: int = load_i32(o, 12) & 65536
        zpage_owner_node = pcc_gc_object_index_find(o)
        zpage_indexed: int = 0
        if ptr_is_null(zpage_owner_node) == 0:
            if ptr_is_null(_object_node_zpage(zpage_owner_node)) == 0:
                zpage_indexed = 1
        zpage_addr_owned: int = 0
        if zpage_indexed == 0 and (load_i32(o, 12) & 262144) == 0:
            zpage_addr_owned = _backend4_zpage_owns_addr(o)
        if zpage_flags != 0 or zpage_indexed != 0 or zpage_addr_owned != 0:
            if zpage_flags == 0:
                store_i32(o, 12, load_i32(o, 12) | 65536)
            _backend4_zpage_remove(o)
    if _gc_tracks_objects() == 0:
        _object_graph_unlock()
        return
    node = pcc_gc_object_index_find(o)
    if ptr_is_null(node) == 0:
        if _object_node_freeing(node) == 0:
            _live_bytes_subtract(_object_node_size(node))
        _set_object_node_freeing(node, 1)
        if ptr_is_null(_object_node_minor_block(node)) == 0:
            _object_graph_unlock()
            return
        pcc_gc_object_index_remove(o)
        _unlink_object_node(node)
        _object_node_release(node)
        _object_graph_unlock()
        return
    last = global_load_ptr("pcc_gc_last_alloc")
    if ptr_eq(last, o) != 0:
        global_store_ptr("pcc_gc_last_alloc", null())
    _object_graph_unlock()


@c_abi_export("pcc_gc_object_id")
def pcc_gc_object_id(o) -> int:
    _init_config()
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    _object_graph_lock()
    node = _identity_ensure(o)
    if ptr_is_null(node) != 0:
        _object_graph_unlock()
        return 0
    stable_id: int = load_i64(node, 8)
    _object_graph_unlock()
    return stable_id


@c_abi_export("pcc_gc_reset_relocation_set")
def pcc_gc_reset_relocation_set() -> None:
    _init_config()
    _object_graph_lock()
    node = _relocation_set_head()
    _set_relocation_set_head(null())
    _backend4_evacuation_page_clear()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 8)
        obj = load_ptr(node, 0)
        if ptr_is_null(obj) == 0:
            if is_tagged_int(obj) == 0:
                if ptr_is_null(_forwarding_find(obj)) != 0:
                    flags: int = load_i32(obj, 12)
                    store_i32(obj, 12, flags & ~2048)
        free(node)
        node = nxt
    obj_node = _object_head()
    while ptr_is_null(obj_node) == 0:
        obj = load_ptr(obj_node, 0)
        if ptr_is_null(obj) == 0:
            if is_tagged_int(obj) == 0:
                flags: int = load_i32(obj, 12)
                store_i32(obj, 12, flags & ~8192)
        obj_node = _object_node_next(obj_node)
    store_i32(global_addr("pcc_gc_backend4_evacuation_candidates"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_evacuation_candidate_bytes_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_small_page_candidates"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_medium_page_candidates"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_small_page_candidate_bytes_count"), 0, 0)
    store_i32(global_addr("pcc_gc_backend4_medium_page_candidate_bytes_count"), 0, 0)
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_small_page_candidate_zpage_bytes_count"), 0, 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count"), 0, 0
    )
    _object_graph_unlock()


@c_abi_export("pcc_gc_relocation_set_contains")
def pcc_gc_relocation_set_contains(o) -> int:
    _init_config()
    _object_graph_lock()
    if ptr_is_null(_relocation_set_find(o)) == 0:
        _object_graph_unlock()
        return 1
    _object_graph_unlock()
    return 0


@c_abi_export("pcc_gc_relocation_set_size")
def pcc_gc_relocation_set_size() -> int:
    _init_config()
    _object_graph_lock()
    size: int = 0
    node = _relocation_set_head()
    while ptr_is_null(node) == 0:
        size = size + 1
        node = load_ptr(node, 8)
    _object_graph_unlock()
    return size


def _relocate_copy_supported_tag(tag: int) -> int:
    if tag == 2:  # PY_TYPE_INT
        return 1
    if tag == 3:  # PY_TYPE_FLOAT
        return 1
    if tag == 4:  # PY_TYPE_STR
        return 1
    if tag == 16:  # PY_TYPE_COMPLEX
        return 1
    if tag == 17:  # PY_TYPE_BYTES
        return 1
    if tag == 18:  # PY_TYPE_BYTEARRAY
        return 1
    return 0


def _colored_relocate_copy_supported_tag(tag: int) -> int:
    if tag == 101:  # PY_TYPE_PROPERTY
        return 1
    if tag == 102:  # PY_TYPE_CLASSMETHOD
        return 1
    if tag == 103:  # PY_TYPE_STATICMETHOD
        return 1
    if tag == 19:  # PY_TYPE_MEMORYVIEW
        return 1
    if tag == 9:  # PY_TYPE_FUNC
        return 1
    if tag == 14:  # PY_TYPE_ITER
        return 1
    if tag == 15:  # PY_TYPE_GEN
        return 1
    if tag == 20:  # PY_TYPE_COROUTINE
        return 1
    if tag == 29:  # PY_TYPE_CONTINUATION
        return 1
    if tag == 12:  # PY_TYPE_EXC
        return 1
    if tag == 10:  # PY_TYPE_CLASS
        return 1
    if tag == 21:  # PY_TYPE_WEAKREF
        return 1
    if tag == 27:  # PY_TYPE_THREAD
        return 1
    if tag == 5:  # PY_TYPE_LIST
        return 1
    if tag == 6:  # PY_TYPE_DICT
        return 1
    if tag == 7:  # PY_TYPE_TUPLE
        return 1
    if tag == 8:  # PY_TYPE_SET
        return 1
    if tag == 28:  # PY_TYPE_TASK
        return 1
    if tag == 30:  # PY_TYPE_VIRTUAL_THREAD
        return 1
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return 0
    if tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags
        return 1
    return _relocate_copy_supported_tag(tag)


def _relocate_slot_pairs_dispose(ctx) -> None:
    if ptr_is_null(ctx) != 0:
        return
    entries = load_ptr(ctx, 16)
    if ptr_is_null(entries) == 0:
        free(entries)
    if ptr_eq(global_load_ptr("pcc_gc_relocate_slot_pairs_ctx"), ctx) != 0:
        global_store_ptr("pcc_gc_relocate_slot_pairs_ctx", null())
    free(ctx)


def _relocate_slot_pairs_prepare(from_obj, to_obj, size: int):
    ctx = malloc(56)
    if ptr_is_null(ctx) != 0:
        return null()
    memset(ctx, 0, 56)
    store_ptr(ctx, 0, from_obj)
    store_ptr(ctx, 8, to_obj)
    store_i64(ctx, 48, size)
    store_i32(ctx, 40, 1)
    global_store_ptr("pcc_gc_relocate_slot_pairs_ctx", ctx)
    if _py_obj_visit_covered_slots(from_obj, 6, 0) == 0:
        _relocate_slot_pairs_dispose(ctx)
        return null()
    count: int = load_i64(ctx, 24)
    if count < 0 or count > 384307168202282325:
        _relocate_slot_pairs_dispose(ctx)
        return null()
    if count > 0:
        entries = malloc(count * 24)
        if ptr_is_null(entries) != 0:
            _relocate_slot_pairs_dispose(ctx)
            return null()
        memset(entries, 0, count * 24)
        store_ptr(ctx, 16, entries)
    store_i64(ctx, 32, 0)
    if _py_obj_visit_covered_slots(from_obj, 7, 0) == 0:
        _relocate_slot_pairs_dispose(ctx)
        return null()
    if load_i64(ctx, 32) != count or load_i32(ctx, 40) == 0:
        _relocate_slot_pairs_dispose(ctx)
        return null()
    return ctx


def _relocate_copy_slots(from_obj, to_obj, ctx) -> int:
    if ptr_is_null(ctx) != 0:
        return 0
    count: int = load_i64(ctx, 24)
    store_i64(ctx, 32, 0)
    store_i32(ctx, 40, 1)
    if _py_obj_visit_covered_slots(to_obj, 8, 0) == 0:
        return 0
    if load_i64(ctx, 32) != count or load_i32(ctx, 40) == 0:
        return 0
    entries = load_ptr(ctx, 16)
    i: int = 0
    while i < count:
        entry = ptr_add(entries, i * 24)
        from_slot = load_ptr(entry, 0)
        role: int = load_i32(entry, 8)
        to_slot = load_ptr(entry, 16)
        _remap_heal_slot(from_slot, 0)
        value = load_ptr(from_slot, 0)
        if ptr_eq(value, from_obj) != 0:
            value = to_obj
        if role == 1:  # _PY_OBJ_SLOT_OWNED
            py_incref(value)
        store_ptr(to_slot, 0, value)
        _backend4_remembered_set_retarget_slot(from_obj, to_obj, from_slot, to_slot)
        i = i + 1
    return 1


def _relocate_copy_payload_fail(ctx) -> int:
    _relocate_slot_pairs_dispose(ctx)
    return 0


def _relocate_copy_payload_finish(
    from_obj,
    to_obj,
    tag: int,
    ctx,
    continuation_src_chunk,
    continuation_dst_chunk,
    continuation_mounted: int,
) -> int:
    if _relocate_copy_slots(from_obj, to_obj, ctx) == 0:
        return _relocate_copy_payload_fail(ctx)
    if tag == 21:  # PY_TYPE_WEAKREF
        prev = load_ptr(from_obj, 32)
        nxt = load_ptr(from_obj, 40)
        store_ptr(to_obj, 32, prev)
        store_ptr(to_obj, 40, nxt)
        if ptr_is_null(prev) != 0:
            global_store_ptr("py_weakref_head", to_obj)
        else:
            store_ptr(prev, 40, to_obj)
        if ptr_is_null(nxt) == 0:
            store_ptr(nxt, 32, to_obj)
        store_ptr(from_obj, 32, from_obj)
        store_ptr(from_obj, 40, null())
    if (
        tag == 29
        and ptr_is_null(continuation_src_chunk) == 0
        and continuation_mounted == 0
    ):
        _retarget_continuation_root_slots(
            load_ptr(continuation_src_chunk, 16),
            continuation_src_chunk,
            load_ptr(continuation_dst_chunk, 16),
            continuation_dst_chunk,
        )
    _relocate_slot_pairs_dispose(ctx)
    return 1


def _relocate_copy_payload(from_obj, to_obj, tag: int, size: int) -> int:
    ctx = _relocate_slot_pairs_prepare(from_obj, to_obj, size)
    if ptr_is_null(ctx) != 0:
        return 0
    result: int = 0
    continuation_src_chunk = null()
    continuation_dst_chunk = null()

    if tag == 29:  # PY_TYPE_CONTINUATION
        src_chunk = load_ptr(from_obj, 24)
        mounted: int = load_i64(from_obj, 32)
        continuation_src_chunk = src_chunk
        store_ptr(to_obj, 24, null())
        if ptr_is_null(src_chunk) != 0:
            return _relocate_copy_payload_finish(
                from_obj, to_obj, tag, ctx, null(), null(), mounted
            )
        n_slots: int = load_i64(src_chunk, 8)
        if n_slots < 0 or n_slots > 1152921504606846975:
            return _relocate_copy_payload_fail(ctx)
        src_slots = load_ptr(src_chunk, 16)
        if n_slots > 0 and ptr_is_null(src_slots) != 0:
            return _relocate_copy_payload_fail(ctx)
        dst_chunk = malloc(24)
        if ptr_is_null(dst_chunk) != 0:
            return _relocate_copy_payload_fail(ctx)
        continuation_dst_chunk = dst_chunk
        memset(dst_chunk, 0, 24)
        store_i32(dst_chunk, 0, load_i32(src_chunk, 0))
        store_i32(dst_chunk, 4, 0)
        store_i64(dst_chunk, 8, n_slots)
        store_ptr(dst_chunk, 16, null())
        dst_slots = null()
        if n_slots > 0:
            dst_slots = malloc(n_slots * 8)
            if ptr_is_null(dst_slots) != 0:
                free(dst_chunk)
                return _relocate_copy_payload_fail(ctx)
            store_ptr(dst_chunk, 16, dst_slots)
            memmove(dst_slots, src_slots, n_slots * 8)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj,
                dst_slots,
                n_slots * 8,
            )
        store_ptr(to_obj, 24, dst_chunk)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, src_chunk, dst_chunk, mounted
        )

    if tag == 12:  # PY_TYPE_EXC
        traceback = load_ptr(from_obj, 48)
        n_frames: int = load_i32(from_obj, 56)
        cap_frames: int = load_i32(from_obj, 60)

        store_ptr(to_obj, 48, null())
        store_i32(to_obj, 56, 0)
        store_i32(to_obj, 60, 0)

        if n_frames < 0 or cap_frames < 0 or n_frames > cap_frames:
            return _relocate_copy_payload_fail(ctx)
        if cap_frames > 0 and ptr_is_null(traceback) != 0:
            return _relocate_copy_payload_fail(ctx)
        if cap_frames > 384307168202282325:
            return _relocate_copy_payload_fail(ctx)
        if cap_frames > 0:
            copied_traceback = malloc(cap_frames * 24)
            if ptr_is_null(copied_traceback) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(copied_traceback, traceback, cap_frames * 24)
            store_ptr(to_obj, 48, copied_traceback)

        store_i32(to_obj, 56, n_frames)
        store_i32(to_obj, 60, cap_frames)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 10:  # PY_TYPE_CLASS
        n_bases: int = load_i32(from_obj, 24)
        bases = load_ptr(from_obj, 32)
        n_mro: int = load_i32(from_obj, 40)
        mro = load_ptr(from_obj, 48)
        n_methods: int = load_i32(from_obj, 56)
        methods = load_ptr(from_obj, 64)
        n_fields: int = load_i32(from_obj, 72)
        field_names = load_ptr(from_obj, 80)
        store_i32(to_obj, 24, 0)
        store_ptr(to_obj, 32, null())
        store_i32(to_obj, 40, 0)
        store_ptr(to_obj, 48, null())
        store_i32(to_obj, 56, 0)
        store_ptr(to_obj, 64, null())
        store_i32(to_obj, 72, 0)
        store_ptr(to_obj, 80, null())
        store_ptr(to_obj, 104, null())

        if n_bases < 0 or n_mro < 0 or n_methods < 0 or n_fields < 0:
            return _relocate_copy_payload_fail(ctx)
        if n_bases > 1152921504606846975 or n_mro > 1152921504606846975:
            return _relocate_copy_payload_fail(ctx)
        if n_methods > 576460752303423487 or n_fields > 1152921504606846975:
            return _relocate_copy_payload_fail(ctx)
        if n_bases > 0:
            if ptr_is_null(bases) != 0:
                return _relocate_copy_payload_fail(ctx)
            bases_copy = malloc(n_bases * 8)
            if ptr_is_null(bases_copy) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(bases_copy, bases, n_bases * 8)
            store_ptr(to_obj, 32, bases_copy)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj, bases_copy, n_bases * 8
            )
        if n_mro > 0:
            if ptr_is_null(mro) != 0:
                return _relocate_copy_payload_fail(ctx)
            mro_copy = malloc(n_mro * 8)
            if ptr_is_null(mro_copy) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(mro_copy, mro, n_mro * 8)
            store_ptr(to_obj, 48, mro_copy)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj, mro_copy, n_mro * 8
            )
        if n_methods > 0:
            if ptr_is_null(methods) != 0:
                return _relocate_copy_payload_fail(ctx)
            methods_copy = malloc(n_methods * 16)
            if ptr_is_null(methods_copy) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(methods_copy, methods, n_methods * 16)
            store_ptr(to_obj, 64, methods_copy)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj, methods_copy, n_methods * 16
            )
        if n_fields > 0:
            if ptr_is_null(field_names) != 0:
                return _relocate_copy_payload_fail(ctx)
            field_names_copy = malloc(n_fields * 8)
            if ptr_is_null(field_names_copy) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(field_names_copy, field_names, n_fields * 8)
            store_ptr(to_obj, 80, field_names_copy)
        store_i32(to_obj, 24, n_bases)
        store_i32(to_obj, 40, n_mro)
        store_i32(to_obj, 56, n_methods)
        store_i32(to_obj, 72, n_fields)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 21:  # PY_TYPE_WEAKREF
        store_ptr(to_obj, 32, null())
        store_ptr(to_obj, 40, null())
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 27:  # PY_TYPE_THREAD
        handle = load_ptr(from_obj, 16)
        if ptr_is_null(handle) == 0:
            return _relocate_copy_payload_fail(ctx)
        store_ptr(to_obj, 16, null())
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 28:  # PY_TYPE_TASK
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 30:  # PY_TYPE_VIRTUAL_THREAD
        queued: int = load_i64(from_obj, 40)
        if queued != 0:
            return _relocate_copy_payload_fail(ctx)
        store_i64(to_obj, 40, 0)
        store_ptr(to_obj, 56, null())
        store_ptr(to_obj, 64, null())
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if pcc_capi_is_cext_type_tag(tag) != 0:
        return _relocate_copy_payload_fail(ctx)

    if tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags
        cls = pcc_gc_load_ptr_extern(from_obj, ptr_add(from_obj, 16))
        if size < 24:
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(cls) != 0:
            return _relocate_copy_payload_fail(ctx)
        if load_i32(cls, 8) != 10:  # PY_TYPE_CLASS
            return _relocate_copy_payload_fail(ctx)

        n_fields: int = load_i32(cls, 72)
        if n_fields < 0:
            n_fields = 0
        n_slots: int = n_fields
        class_flags: int = load_i32(cls, 12)
        if (class_flags & 2) == 0:
            n_slots = n_slots + 1
        if n_slots < 0:
            return _relocate_copy_payload_fail(ctx)
        if size < 24 + n_slots * 8:
            return _relocate_copy_payload_fail(ctx)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 6:  # PY_TYPE_DICT
        dict_size: int = load_i64(from_obj, 16)
        capacity: int = load_i64(from_obj, 24)
        src_indices = load_ptr(from_obj, 32)
        src_entries = load_ptr(from_obj, 40)
        entries_used: int = load_i64(from_obj, 48)

        store_i64(to_obj, 16, 0)
        store_i64(to_obj, 24, 0)
        store_ptr(to_obj, 32, null())
        store_ptr(to_obj, 40, null())
        store_i64(to_obj, 48, 0)

        if capacity < 0 or entries_used < 0 or dict_size < 0:
            return _relocate_copy_payload_fail(ctx)
        if entries_used > capacity or dict_size > entries_used:
            return _relocate_copy_payload_fail(ctx)
        if capacity > 0:
            if ptr_is_null(src_indices) != 0 or ptr_is_null(src_entries) != 0:
                return _relocate_copy_payload_fail(ctx)
            if capacity > 384307168202282325:
                return _relocate_copy_payload_fail(ctx)
            indices = malloc(capacity * 8)
            if ptr_is_null(indices) != 0:
                return _relocate_copy_payload_fail(ctx)
            entries = malloc(capacity * 24)
            if ptr_is_null(entries) != 0:
                free(indices)
                return _relocate_copy_payload_fail(ctx)
            memmove(indices, src_indices, capacity * 8)
            memmove(entries, src_entries, capacity * 24)
            store_ptr(to_obj, 32, indices)
            store_ptr(to_obj, 40, entries)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj, entries, capacity * 24
            )
        store_i64(to_obj, 24, capacity)
        store_i64(to_obj, 16, dict_size)
        store_i64(to_obj, 48, entries_used)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 8:  # PY_TYPE_SET
        set_size: int = load_i64(from_obj, 16)
        capacity: int = load_i64(from_obj, 24)
        fill: int = load_i64(from_obj, 32)
        src_entries = load_ptr(from_obj, 40)

        store_i64(to_obj, 16, 0)
        store_i64(to_obj, 24, 0)
        store_i64(to_obj, 32, 0)
        store_ptr(to_obj, 40, null())

        if capacity < 0:
            return _relocate_copy_payload_fail(ctx)
        if capacity > 0:
            if ptr_is_null(src_entries) != 0:
                return _relocate_copy_payload_fail(ctx)
            if capacity > 576460752303423487:
                return _relocate_copy_payload_fail(ctx)
            entries = malloc(capacity * 16)
            if ptr_is_null(entries) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(entries, src_entries, capacity * 16)
            store_ptr(to_obj, 40, entries)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj, entries, capacity * 16
            )
        store_i64(to_obj, 16, set_size)
        store_i64(to_obj, 24, capacity)
        store_i64(to_obj, 32, fill)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 7:  # PY_TYPE_TUPLE
        length: int = load_i64(from_obj, 16)
        if length < 0:
            return _relocate_copy_payload_fail(ctx)
        if size < 24 + length * 8:
            return _relocate_copy_payload_fail(ctx)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == 5:  # PY_TYPE_LIST
        length: int = load_i64(from_obj, 16)
        capacity: int = load_i64(from_obj, 24)
        src_items = load_ptr(from_obj, 32)

        store_i64(to_obj, 16, 0)
        store_i64(to_obj, 24, 0)
        store_ptr(to_obj, 32, null())

        if length < 0 or capacity < length:
            return _relocate_copy_payload_fail(ctx)
        if capacity > 0:
            if ptr_is_null(src_items) != 0:
                return _relocate_copy_payload_fail(ctx)
            items = malloc(capacity * 8)
            if ptr_is_null(items) != 0:
                return _relocate_copy_payload_fail(ctx)
            memset(items, 0, capacity * 8)
            memmove(items, src_items, length * 8)
            store_ptr(to_obj, 32, items)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj, items, capacity * 8
            )
        store_i64(to_obj, 16, length)
        store_i64(to_obj, 24, capacity)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    return _relocate_copy_payload_finish(from_obj, to_obj, tag, ctx, null(), null(), 0)


def _backend_uses_forwarding() -> int:
    backend: int = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend == 3 or backend == 4:
        return 1
    return 0


def _backend4_evacuation_policy_accept(size: int) -> int:
    if size <= 0:
        return 0
    if size <= 4096:
        return 1
    if size <= 65536:
        return 1
    return 0


def _backend4_evacuation_policy_defer_large(size: int) -> int:
    if size > 65536:
        return 1
    return 0


def _backend4_large_page_evacuation_policy_accept(page, size: int) -> int:
    if ptr_is_null(page) != 0:
        return 0
    if size <= 65536:
        return 0
    if load_i32(page, 24) != 2:
        return 0
    if load_i64(page, 16) > load_i64(page, 8):
        return 1
    return 0


def _backend4_clear_large_deferred_flags() -> None:
    _object_graph_lock()
    node = _object_head()
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            if ptr_is_null(obj) == 0:
                if is_tagged_int(obj) == 0:
                    flags: int = load_i32(obj, 12)
                    if (flags & 32768) != 0:
                        reconsidered: int = load_i32(
                            global_addr(
                                "pcc_gc_backend4_large_object_reconsiderations_count"
                            ),
                            0,
                        )
                        store_i32(
                            global_addr(
                                "pcc_gc_backend4_large_object_reconsiderations_count"
                            ),
                            0,
                            reconsidered + 1,
                        )
                        store_i32(obj, 12, flags & ~32768)
        node = _object_node_next(node)
    _object_graph_unlock()


def _backend4_reseed_relocation_epoch_state() -> None:
    if pcc_gc_backend() != 4:
        return
    _object_graph_lock()
    node = _relocation_set_head()
    candidates: int = 0
    candidate_bytes: int = 0
    small_candidates: int = 0
    medium_candidates: int = 0
    small_bytes: int = 0
    medium_bytes: int = 0
    zpage_bytes: int = 0
    small_zpage_bytes: int = 0
    medium_zpage_bytes: int = 0
    _backend4_evacuation_page_clear()
    while ptr_is_null(node) == 0:
        obj = load_ptr(node, 0)
        size: int = _object_known_size(obj)
        if size > 0:
            candidates = candidates + 1
            candidate_bytes = candidate_bytes + size
            if size <= 4096:
                small_candidates = small_candidates + 1
                small_bytes = small_bytes + size
            elif size <= 65536:
                medium_candidates = medium_candidates + 1
                medium_bytes = medium_bytes + size
        node = load_ptr(node, 8)
    page = _zpage_page_head()
    while ptr_is_null(page) == 0:
        if _backend4_relocation_set_contains_page(page) != 0:
            _backend4_evacuation_page_add(page)
            page_bytes: int = load_i64(page, 8)
            page_class: int = load_i32(page, 24)
            if page_bytes > 0:
                zpage_bytes = zpage_bytes + page_bytes
                if page_class == 0:
                    small_zpage_bytes = small_zpage_bytes + page_bytes
                elif page_class == 1:
                    medium_zpage_bytes = medium_zpage_bytes + page_bytes
        page = load_ptr(page, 56)
    store_i32(global_addr("pcc_gc_backend4_evacuation_candidates"), 0, candidates)
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_bytes_count"),
        0,
        candidate_bytes,
    )
    store_i32(global_addr("pcc_gc_backend4_small_page_candidates"), 0, small_candidates)
    store_i32(
        global_addr("pcc_gc_backend4_medium_page_candidates"), 0, medium_candidates
    )
    store_i32(
        global_addr("pcc_gc_backend4_small_page_candidate_bytes_count"), 0, small_bytes
    )
    store_i32(
        global_addr("pcc_gc_backend4_medium_page_candidate_bytes_count"),
        0,
        medium_bytes,
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count"),
        0,
        zpage_bytes,
    )
    store_i32(
        global_addr("pcc_gc_backend4_small_page_candidate_zpage_bytes_count"),
        0,
        small_zpage_bytes,
    )
    store_i32(
        global_addr("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count"),
        0,
        medium_zpage_bytes,
    )
    _object_graph_unlock()


def _backend4_evacuation_page_find(page):
    if ptr_is_null(page) != 0:
        return null()
    node = _evacuation_page_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), page) != 0:
            return node
        node = load_ptr(node, 8)
    return null()


def _backend4_evacuation_page_add(page) -> int:
    if ptr_is_null(page) != 0:
        return 0
    if ptr_is_null(_backend4_evacuation_page_find(page)) == 0:
        return 0
    _backend4_clear_active_page(page)
    node = malloc(16)
    if ptr_is_null(node) != 0:
        return 0
    store_ptr(node, 0, page)
    store_ptr(node, 8, _evacuation_page_head())
    _set_evacuation_page_head(node)
    return 1


def _backend4_evacuation_page_remove(page) -> None:
    if ptr_is_null(page) != 0:
        return
    prev = null()
    node = _evacuation_page_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 8)
        if ptr_eq(load_ptr(node, 0), page) != 0:
            if ptr_is_null(prev) != 0:
                _set_evacuation_page_head(nxt)
            else:
                store_ptr(prev, 8, nxt)
            free(node)
            return
        prev = node
        node = nxt


def _backend4_evacuation_page_clear() -> None:
    node = _evacuation_page_head()
    _set_evacuation_page_head(null())
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 8)
        free(node)
        node = nxt


def _backend4_relocation_set_contains_page(page) -> int:
    if ptr_is_null(page) != 0:
        return 0
    rel = _relocation_set_head()
    while ptr_is_null(rel) == 0:
        obj = load_ptr(rel, 0)
        node = _backend4_zpage_find(obj)
        if ptr_is_null(node) == 0 and ptr_eq(load_ptr(node, 8), page) != 0:
            return 1
        rel = load_ptr(rel, 8)
    return 0


def _backend4_zpage_page_for_owner(owner):
    if ptr_is_null(owner) != 0:
        return null()
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return null()
    return load_ptr(node, 8)


def _backend4_note_page_candidate(size: int, page) -> None:
    if size <= 0:
        return
    total_bytes: int = load_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_bytes_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_bytes_count"),
        0,
        total_bytes + size,
    )
    if size <= 4096:
        small: int = load_i32(global_addr("pcc_gc_backend4_small_page_candidates"), 0)
        store_i32(global_addr("pcc_gc_backend4_small_page_candidates"), 0, small + 1)
        small_bytes: int = load_i32(
            global_addr("pcc_gc_backend4_small_page_candidate_bytes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_small_page_candidate_bytes_count"),
            0,
            small_bytes + size,
        )
    elif size <= 65536:
        medium: int = load_i32(global_addr("pcc_gc_backend4_medium_page_candidates"), 0)
        store_i32(global_addr("pcc_gc_backend4_medium_page_candidates"), 0, medium + 1)
        medium_bytes: int = load_i32(
            global_addr("pcc_gc_backend4_medium_page_candidate_bytes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_medium_page_candidate_bytes_count"),
            0,
            medium_bytes + size,
        )
    if ptr_is_null(page) != 0:
        return
    page_bytes: int = load_i64(page, 8)
    if page_bytes <= 0:
        return
    zpage_total: int = load_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count"), 0
    )
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count"),
        0,
        zpage_total + page_bytes,
    )
    page_class: int = load_i32(page, 24)
    if page_class == 0:
        small_zpage: int = load_i32(
            global_addr("pcc_gc_backend4_small_page_candidate_zpage_bytes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_small_page_candidate_zpage_bytes_count"),
            0,
            small_zpage + page_bytes,
        )
    elif page_class == 1:
        medium_zpage: int = load_i32(
            global_addr("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count"),
            0,
            medium_zpage + page_bytes,
        )


def _backend4_zpage_candidate_score(node, allow_large_pages: int) -> int:
    if ptr_is_null(node) != 0:
        return -1
    obj = load_ptr(node, 0)
    page = load_ptr(node, 8)
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0 or ptr_is_null(page) != 0:
        return -1
    if ptr_is_null(_relocation_set_find(obj)) == 0:
        return -1
    flags: int = load_i32(obj, 12)
    if (flags & (64 | 8192)) != 0:
        return -1
    tag: int = load_i32(obj, 8)
    if _colored_relocate_copy_supported_tag(tag) == 0:
        return -1
    if tag == 27:
        if ptr_is_null(load_ptr(obj, 16)) == 0:
            return -1
    size: int = _object_known_size(obj)
    large_page_accepted: int = 0
    if _backend4_evacuation_policy_accept(size) == 0:
        if allow_large_pages != 0:
            large_page_accepted = _backend4_large_page_evacuation_policy_accept(
                page, size
            )
    if _backend4_evacuation_policy_accept(size) == 0 and large_page_accepted == 0:
        if _backend4_evacuation_policy_defer_large(size) != 0:
            if (flags & 32768) == 0:
                store_i32(obj, 12, flags | 32768)
                deferred: int = load_i32(
                    global_addr("pcc_gc_backend4_large_object_defers"), 0
                )
                store_i32(
                    global_addr("pcc_gc_backend4_large_object_defers"),
                    0,
                    deferred + 1,
                )
                deferred_bytes: int = load_i32(
                    global_addr("pcc_gc_backend4_large_object_deferred_bytes_count"),
                    0,
                )
                store_i32(
                    global_addr("pcc_gc_backend4_large_object_deferred_bytes_count"),
                    0,
                    deferred_bytes + size,
                )
        return -1
    capacity: int = load_i64(page, 16)
    page_used: int = load_i64(page, 8)
    score: int = capacity - page_used
    if score < 0:
        score = 0
    score = score + load_i64(page, 40)
    score = score + load_i64(page, 48)
    score = score + _backend4_owner_remembered_slots(obj)
    if (flags & 256) != 0:
        score = score + 1
    if score <= 0:
        return -1
    return score


def _backend4_add_candidate_node(node, allow_large_pages: int) -> int:
    if ptr_is_null(node) != 0:
        return 0
    obj = load_ptr(node, 0)
    page = load_ptr(node, 8)
    size: int = _object_known_size(obj)
    count_page: int = 0
    if ptr_is_null(_backend4_evacuation_page_find(page)) != 0:
        count_page = 1
    if _relocation_set_add(obj) == 0:
        return 0
    candidates: int = load_i32(global_addr("pcc_gc_backend4_evacuation_candidates"), 0)
    store_i32(
        global_addr("pcc_gc_backend4_evacuation_candidates"),
        0,
        candidates + 1,
    )
    if count_page != 0:
        if _backend4_evacuation_page_add(page) != 0:
            _backend4_note_page_candidate(size, page)
        else:
            _backend4_note_page_candidate(size, null())
    else:
        _backend4_note_page_candidate(size, null())
    return 1


def _backend4_select_page_objects(
    seed_node,
    budget: int,
    allow_large_pages: int,
) -> int:
    if ptr_is_null(seed_node) != 0 or budget <= 0:
        return 0
    seed_page = load_ptr(seed_node, 8)
    seed_obj = load_ptr(seed_node, 0)
    if ptr_is_null(seed_page) != 0:
        return 0
    selected: int = 0
    pass_no: int = 0
    while pass_no < 2 and selected < budget:
        node = _zpage_head()
        while ptr_is_null(node) == 0 and selected < budget:
            obj = load_ptr(node, 0)
            same_seed: int = ptr_eq(obj, seed_obj)
            if ptr_eq(load_ptr(node, 8), seed_page) != 0:
                if (pass_no == 0 and same_seed != 0) or (
                    pass_no != 0 and same_seed == 0
                ):
                    if _backend4_zpage_candidate_score(node, allow_large_pages) > 0:
                        if _backend4_add_candidate_node(node, allow_large_pages) != 0:
                            selected = selected + 1
                            if (selected % 16) == 0:
                                pcc_thread_safepoint()
            node = load_ptr(node, 16)
        pass_no = pass_no + 1
    return selected


@c_abi_export("pcc_gc_select_relocation_set")
def pcc_gc_select_relocation_set(budget: int) -> int:
    backend: int = _init_config()
    if backend != 4:
        return 0
    if budget <= 0:
        return 0
    _object_graph_lock()
    selected: int = 0
    while selected < budget:
        best_node = null()
        best_score: int = -1
        node = _zpage_head()
        while ptr_is_null(node) == 0:
            score: int = _backend4_zpage_candidate_score(node, 0)
            if score > best_score:
                best_node = node
                best_score = score
            node = load_ptr(node, 16)
        if ptr_is_null(best_node) != 0:
            break
        added: int = _backend4_select_page_objects(best_node, budget - selected, 0)
        if added <= 0:
            break
        selected = selected + added
    _object_graph_unlock()
    return selected


def _backend4_select_relocation_pages(page_budget: int) -> int:
    if pcc_gc_backend() != 4:
        return 0
    if page_budget <= 0:
        return 0
    _object_graph_lock()
    selected: int = 0
    pages: int = 0
    while pages < page_budget:
        best_node = null()
        best_score: int = -1
        node = _zpage_head()
        while ptr_is_null(node) == 0:
            page = load_ptr(node, 8)
            score: int = _backend4_zpage_candidate_score(node, 1)
            if (
                score > best_score
                and ptr_is_null(_backend4_evacuation_page_find(page)) != 0
            ):
                best_node = node
                best_score = score
            node = load_ptr(node, 16)
        if ptr_is_null(best_node) != 0:
            break
        best_page = load_ptr(best_node, 8)
        object_budget: int = load_i64(best_page, 32)
        if object_budget < 1:
            object_budget = 1
        before_selected: int = selected
        added: int = _backend4_select_page_objects(best_node, object_budget, 1)
        if added <= 0:
            break
        selected = selected + added
        if selected > before_selected:
            pages = pages + 1
    _object_graph_unlock()
    return selected


def _relocate_copy_unlocked(from_obj, size: int):
    if pcc_gc_backend() != 4:
        return null()
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return null()
    if size < 16:
        return null()
    if ptr_is_null(_forwarding_find(from_obj)) == 0:
        return null()
    if ptr_is_null(_relocation_set_find(from_obj)) != 0:
        return null()
    flags: int = load_i32(from_obj, 12)
    if (flags & 64) != 0:
        return null()
    tag: int = load_i32(from_obj, 8)
    if _colored_relocate_copy_supported_tag(tag) == 0:
        return null()
    known_size: int = _object_known_size(from_obj)
    if known_size <= 0 or size > known_size:
        return null()
    to_obj = pcc_gc_alloc(size, tag, flags & ~10240)
    if ptr_is_null(to_obj) != 0:
        return null()
    # The header memmove below clobbers to_obj's flags with from's.
    # Allocation-origin bits (ZPAGE_ALLOC 65536 / MINOR_ARENA 4096 /
    # MALLOC_ALLOC 262144)
    # describe WHERE to_obj physically lives and must survive the copy:
    # losing ZPAGE_ALLOC undercounts pending_forwardings on chained
    # relocations (page destroyed while forwarded -> UAF).
    to_residency: int = load_i32(to_obj, 12) & 331776
    memmove(to_obj, from_obj, size)
    store_i64(to_obj, 0, 1)
    new_flags: int = load_i32(to_obj, 12)
    store_i32(to_obj, 12, (new_flags & ~342016) | to_residency)
    if _relocate_copy_payload(from_obj, to_obj, tag, size) == 0:
        py_decref(to_obj)
        return null()
    if _install_forwarding_unlocked(from_obj, to_obj) != 0:
        py_decref(to_obj)
        return null()
    # Count-on-NEW (remap design R2): move the OLD copy's entire
    # outstanding refcount onto the new copy and make the old copy an
    # immortal shell (freed by page retirement after the remap pass,
    # never by refcount). Mirrors py_gc_backend.c.
    outstanding: int = load_i64(from_obj, 0)
    if outstanding > 0:
        store_i64(to_obj, 0, load_i64(to_obj, 0) + outstanding)
    store_i32(from_obj, 12, load_i32(from_obj, 12) | 1)
    from_page = _backend4_zpage_page_for_owner(from_obj)
    evacuated: int = load_i32(global_addr("pcc_gc_backend4_evacuated_bytes_count"), 0)
    store_i32(global_addr("pcc_gc_backend4_evacuated_bytes_count"), 0, evacuated + size)
    _relocation_set_remove(from_obj)
    if ptr_is_null(from_page) == 0:
        if _backend4_relocation_set_contains_page(from_page) == 0:
            _backend4_evacuation_page_remove(from_page)
    _backend4_zpage_remove(from_obj)
    return to_obj


@c_abi_export("pcc_gc_relocate_copy")
def pcc_gc_relocate_copy(from_obj, size: int):
    _init_config()
    _object_graph_lock()
    to_obj = _relocate_copy_unlocked(from_obj, size)
    _object_graph_unlock()
    return to_obj


def _relocate_selected(budget: int) -> int:
    if pcc_gc_backend() != 4:
        return 0
    if budget <= 0:
        return 0
    moved: int = 0
    node = _relocation_set_head()
    while ptr_is_null(node) == 0 and moved < budget:
        nxt = load_ptr(node, 8)
        obj = load_ptr(node, 0)
        to_obj = pcc_gc_relocate_copy(obj, _object_known_size(obj))
        if ptr_is_null(to_obj) == 0:
            py_decref(to_obj)
            moved = moved + 1
            if (moved % 16) == 0:
                pcc_thread_safepoint()
        node = nxt
    if moved > 0 and ptr_is_null(_relocation_set_head()) == 0:
        incomplete: int = load_i32(
            global_addr("pcc_gc_backend4_evacuation_incomplete_batches_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_evacuation_incomplete_batches_count"),
            0,
            incomplete + 1,
        )
    if ptr_is_null(_relocation_set_head()) != 0:
        if load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0:
            # Evacuation drained: heal roots/slots and mark forwarding
            # entries retiring. A later idle step performs retirement.
            _object_graph_lock()
            _backend4_remap_and_retire()
            _object_graph_unlock()
    return moved


@c_abi_export("pcc_gc_backend4_evacuation_drain")
def pcc_gc_backend4_evacuation_drain(budget: int) -> int:
    _init_config()
    return _relocate_selected(budget)


def _relocate_selected_page(page) -> int:
    if ptr_is_null(page) != 0:
        return 0
    moved: int = 0
    node = _relocation_set_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 8)
        obj = load_ptr(node, 0)
        owner_page = _backend4_zpage_page_for_owner(obj)
        if ptr_eq(owner_page, page) != 0:
            to_obj = _relocate_copy_unlocked(obj, _object_known_size(obj))
            if ptr_is_null(to_obj) == 0:
                py_decref(to_obj)
                moved = moved + 1
                if (moved % 16) == 0:
                    pcc_thread_safepoint()
        node = nxt
    return moved


@c_abi_export("pcc_gc_backend4_evacuation_page_drain")
def pcc_gc_backend4_evacuation_page_drain(page_budget: int) -> int:
    backend: int = _init_config()
    if backend != 4:
        return 0
    if page_budget <= 0:
        return 0
    moved: int = 0
    pages: int = 0
    _object_graph_lock()
    while pages < page_budget:
        head = _evacuation_page_head()
        if ptr_is_null(head) != 0:
            break
        page = load_ptr(head, 0)
        page_moved: int = _relocate_selected_page(page)
        if page_moved <= 0:
            break
        moved = moved + page_moved
        pages = pages + 1
    if moved > 0 and ptr_is_null(_relocation_set_head()) == 0:
        incomplete: int = load_i32(
            global_addr("pcc_gc_backend4_evacuation_incomplete_batches_count"), 0
        )
        store_i32(
            global_addr("pcc_gc_backend4_evacuation_incomplete_batches_count"),
            0,
            incomplete + 1,
        )
    if ptr_is_null(_relocation_set_head()) != 0:
        if load_i32(global_addr("pcc_gc_forwarding_population"), 0) > 0:
            _backend4_remap_and_retire()
    _object_graph_unlock()
    return moved


def _install_forwarding_unlocked(from_obj, to_obj) -> int:
    if _backend_uses_forwarding() == 0:
        return -1
    if ptr_is_null(from_obj) != 0 or ptr_is_null(to_obj) != 0:
        return -1
    if is_tagged_int(from_obj) != 0 or is_tagged_int(to_obj) != 0:
        return -1
    if ptr_eq(from_obj, to_obj) != 0:
        return -1
    if _is_known_object(from_obj) == 0 or _is_known_object(to_obj) == 0:
        return -1
    flags: int = load_i32(from_obj, 12)
    if (flags & 64) != 0:
        rejects: int = load_i32(global_addr("pcc_gc_relocation_pin_rejects"), 0)
        store_i32(global_addr("pcc_gc_relocation_pin_rejects"), 0, rejects + 1)
        return -2
    from_identity = _identity_ensure(from_obj)
    if ptr_is_null(from_identity) != 0:
        return -1
    if _identity_assign(to_obj, load_i64(from_identity, 8)) == 0:
        return -1
    node = _forwarding_find(from_obj)
    if ptr_is_null(node) == 0:
        old_target = load_ptr(node, 8)
        if ptr_eq(old_target, to_obj) == 0:
            target_head = _forwarding_target_prepare(to_obj, node)
            if ptr_is_null(target_head) != 0:
                return -1
            py_incref(to_obj)
            _forwarding_target_unlink(node)
            store_ptr(node, 8, to_obj)
            _forwarding_target_attach_prepared(node, target_head)
            py_decref(old_target)
    else:
        node = malloc(56)
        if ptr_is_null(node) != 0:
            return -1
        py_incref(to_obj)
        store_ptr(node, 0, from_obj)
        store_ptr(node, 8, to_obj)
        store_ptr(node, 48, null())
        store_ptr(node, 16, _forwarding_head())
        store_ptr(node, 24, null())
        store_ptr(node, 32, null())
        store_ptr(node, 40, null())
        old_head = _forwarding_head()
        if ptr_is_null(old_head) == 0:
            store_ptr(old_head, 24, node)
        _set_forwarding_head(node)
        if pcc_gc_forwarding_index_insert(from_obj, node) < 0:
            _set_forwarding_head(load_ptr(node, 16))
            nxt = load_ptr(node, 16)
            if ptr_is_null(nxt) == 0:
                store_ptr(nxt, 24, null())
            py_decref(to_obj)
            free(node)
            return -1
        target_head = _forwarding_target_prepare(to_obj, node)
        if ptr_is_null(target_head) != 0:
            pcc_gc_forwarding_index_remove(from_obj)
            _forwarding_unlink_main(node)
            py_decref(to_obj)
            free(node)
            return -1
        _forwarding_target_attach_prepared(node, target_head)
        pop0: int = load_i32(global_addr("pcc_gc_forwarding_population"), 0)
        store_i32(global_addr("pcc_gc_forwarding_population"), 0, pop0 + 1)
        if pcc_gc_backend() == 4 and (flags & 65536) != 0:
            zn = _backend4_zpage_find(from_obj)
            if ptr_is_null(zn) == 0:
                zpage = load_ptr(zn, 8)
                if ptr_is_null(zpage) == 0:
                    store_i64(zpage, 96, load_i64(zpage, 96) + 1)
                    store_ptr(node, 48, zpage)
    store_i32(from_obj, 12, flags | 2048)
    target_flags: int = load_i32(to_obj, 12)
    store_i32(to_obj, 12, target_flags | 8192)
    forwards: int = load_i32(global_addr("pcc_gc_relocation_forwards"), 0)
    store_i32(global_addr("pcc_gc_relocation_forwards"), 0, forwards + 1)
    return 0


@c_abi_export("pcc_gc_install_forwarding")
def pcc_gc_install_forwarding(from_obj, to_obj) -> int:
    _init_config()
    _object_graph_lock()
    rc: int = _install_forwarding_unlocked(from_obj, to_obj)
    _object_graph_unlock()
    return rc


@c_abi_export("pcc_gc_note_load")
def pcc_gc_note_load() -> None:
    slot = global_addr("pcc_gc_metric_load")
    v: int = load_i32(slot, 0)
    store_i32(slot, 0, v + 1)


def _note_relocation_read_unlocked(o):
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return o
    if _is_known_object(o) == 0:
        unknown_node = _forwarding_find(o)
        if ptr_is_null(unknown_node) == 0:
            unknown_target = load_ptr(unknown_node, 8)
            if ptr_is_null(unknown_target) == 0:
                forwards_unknown: int = load_i32(
                    global_addr("pcc_gc_relocation_barrier_forwards"), 0
                )
                store_i32(
                    global_addr("pcc_gc_relocation_barrier_forwards"),
                    0,
                    forwards_unknown + 1,
                )
                return unknown_target
        return o
    flags: int = load_i32(o, 12)
    node = _forwarding_find(o)
    if ptr_is_null(node) == 0:
        target = load_ptr(node, 8)
        if ptr_is_null(target) == 0:
            forwards: int = load_i32(
                global_addr("pcc_gc_relocation_barrier_forwards"), 0
            )
            store_i32(
                global_addr("pcc_gc_relocation_barrier_forwards"),
                0,
                forwards + 1,
            )
            return target
    if (flags & 2048) != 0:
        store_i32(o, 12, flags & ~2048)
    return o


@c_abi_export("pcc_gc_note_relocation_read")
def pcc_gc_note_relocation_read(o):
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return o
    if _is_known_object(o) != 0:
        flags: int = load_i32(o, 12)
        if (flags & 2048) == 0:
            return o
    _object_graph_lock()
    resolved = _note_relocation_read_unlocked(o)
    _object_graph_unlock()
    return resolved


@c_abi_export("pcc_gc_note_store")
def pcc_gc_note_store() -> None:
    slot = global_addr("pcc_gc_metric_store")
    v: int = load_i32(slot, 0)
    store_i32(slot, 0, v + 1)


@c_abi_export("pcc_gc_note_slot_write_barrier")
def pcc_gc_note_slot_write_barrier(owner, slot, value) -> None:
    if ptr_is_null(value) != 0:
        return
    if is_tagged_int(value) != 0:
        return
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    barrier_backend: int = backend
    if ptr_is_null(owner) != 0:
        if barrier_backend == 1 or barrier_backend == 2 or barrier_backend == 4:
            if _is_known_object(value) == 0:
                return
            if load_i32(global_addr("pcc_gc_mark_active"), 0) == 0:
                return
            value_flags: int = load_i32(value, 12)
            should_gray: bool = (value_flags & 8) != 0
            if barrier_backend == 2:
                should_gray = (value_flags & 16) == 0
            if should_gray:
                store_i32(value, 12, (value_flags & ~56) | 16)
                store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
                if barrier_backend == 2:
                    flushes: int = load_i32(global_addr("pcc_gc_cms_wb_flushes"), 0)
                    store_i32(global_addr("pcc_gc_cms_wb_flushes"), 0, flushes + 1)
        return
    if is_tagged_int(owner) != 0:
        return
    if barrier_backend == 1:
        if _is_known_object(owner) == 0 or _is_known_object(value) == 0:
            return
        owner_flags: int = load_i32(owner, 12)
        value_flags: int = load_i32(value, 12)
        if (owner_flags & 32) == 0:
            return
        if (value_flags & 8) != 0:
            store_i32(value, 12, (value_flags & ~56) | 16)
            store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
    elif barrier_backend == 2:
        if _is_known_object(owner) == 0 or _is_known_object(value) == 0:
            return
        if load_i32(global_addr("pcc_gc_mark_active"), 0) == 0:
            return
        value_flags = load_i32(value, 12)
        should_gray_value: bool = (value_flags & 8) != 0
        if barrier_backend == 2:
            should_gray_value = (value_flags & 16) == 0
        if should_gray_value:
            store_i32(value, 12, (value_flags & ~56) | 16)
            store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
            flushes: int = load_i32(global_addr("pcc_gc_cms_wb_flushes"), 0)
            store_i32(global_addr("pcc_gc_cms_wb_flushes"), 0, flushes + 1)
    elif backend == 3 or backend == 4:
        if not _gc_ptr_can_have_header(owner):
            return
        if not _gc_ptr_can_have_header(value):
            return
        owner_flags = load_i32(owner, 12)
        value_flags = load_i32(value, 12)
        if (owner_flags & 256) == 0 or (value_flags & 128) == 0:
            return
        _object_graph_lock()
        if _is_known_object(owner) == 0 or _is_known_object(value) == 0:
            _object_graph_unlock()
            return
        owner_flags = load_i32(owner, 12)
        value_flags = load_i32(value, 12)
        if (owner_flags & 256) != 0:
            if (value_flags & 128) != 0:
                if backend == 4:
                    if _backend4_store_buffer_enqueue(owner, slot, value) != 0:
                        score: int = load_i32(
                            global_addr("pcc_gc_backend4_genzgc_store_barriers"), 0
                        )
                        store_i32(
                            global_addr("pcc_gc_backend4_genzgc_store_barriers"),
                            0,
                            score + 1,
                        )
                else:
                    _backend3_remember_owner(owner, owner_flags)
        _object_graph_unlock()


@c_abi_export("pcc_gc_note_write_barrier")
def pcc_gc_note_write_barrier(owner, value) -> None:
    pcc_gc_note_slot_write_barrier(owner, null(), value)


@c_abi_export("pcc_gc_note_safepoint")
def pcc_gc_note_safepoint() -> None:
    _counter_inc(3, 1)


@c_abi_export("pcc_gc_note_pin")
def pcc_gc_note_pin(delta: int) -> None:
    _counter_inc(4, delta)


@c_abi_export("pcc_gc_scheduler_root_register_handle")
def pcc_gc_scheduler_root_register_handle(slot):
    _init_config()
    if ptr_is_null(slot) != 0:
        return null()
    node = malloc(24)
    if ptr_is_null(node) != 0:
        return null()
    store_ptr(node, 0, slot)
    store_ptr(node, 16, null())
    _object_graph_lock()
    head = global_load_ptr("pcc_gc_scheduler_root_head")
    store_ptr(node, 8, head)
    if ptr_is_null(head) == 0:
        store_ptr(head, 16, node)
    global_store_ptr("pcc_gc_scheduler_root_head", node)
    _object_graph_unlock()
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    return node


@c_abi_export("pcc_gc_scheduler_root_register")
def pcc_gc_scheduler_root_register(slot) -> None:
    pcc_gc_scheduler_root_register_handle(slot)


def _scheduler_root_unlink_locked(node) -> int:
    if ptr_is_null(node) != 0:
        return 0
    prev = load_ptr(node, 16)
    nxt = load_ptr(node, 8)
    if ptr_is_null(prev) == 0:
        store_ptr(prev, 8, nxt)
    elif ptr_eq(global_load_ptr("pcc_gc_scheduler_root_head"), node) != 0:
        global_store_ptr("pcc_gc_scheduler_root_head", nxt)
    else:
        scan_prev = null()
        cur = global_load_ptr("pcc_gc_scheduler_root_head")
        found: int = 0
        while ptr_is_null(cur) == 0:
            if ptr_eq(cur, node) != 0:
                found = 1
                break
            scan_prev = cur
            cur = load_ptr(cur, 8)
        if found == 0:
            return 0
        if ptr_is_null(scan_prev) != 0:
            global_store_ptr("pcc_gc_scheduler_root_head", nxt)
        else:
            store_ptr(scan_prev, 8, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 16, prev)
    store_ptr(node, 8, null())
    store_ptr(node, 16, null())
    return 1


@c_abi_export("pcc_gc_scheduler_root_unregister_handle")
def pcc_gc_scheduler_root_unregister_handle(handle) -> None:
    _init_config()
    if ptr_is_null(handle) != 0:
        return
    _object_graph_lock()
    removed: int = _scheduler_root_unlink_locked(handle)
    _object_graph_unlock()
    if removed != 0:
        free(handle)
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)


@c_abi_export("pcc_gc_scheduler_root_unregister")
def pcc_gc_scheduler_root_unregister(slot) -> None:
    _init_config()
    if ptr_is_null(slot) != 0:
        return
    dead = null()
    _object_graph_lock()
    prev = null()
    node = global_load_ptr("pcc_gc_scheduler_root_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 8)
        if ptr_eq(load_ptr(node, 0), slot) != 0:
            _scheduler_root_unlink_locked(node)
            dead = node
            break
        prev = node
        node = nxt
    _object_graph_unlock()
    if ptr_is_null(dead) == 0:
        free(dead)
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)


@c_abi_export("pcc_gc_scheduler_root_count")
def pcc_gc_scheduler_root_count() -> int:
    _init_config()
    node = global_load_ptr("pcc_gc_scheduler_root_head")
    count: int = 0
    while ptr_is_null(node) == 0:
        count += 1
        node = load_ptr(node, 8)
    return count


@c_abi_export("pcc_gc_frame_root_slot_count")
def pcc_gc_frame_root_slot_count() -> int:
    _init_config()
    node = global_load_ptr("pcc_gc_frame_head")
    count: int = 0
    while ptr_is_null(node) == 0:
        count += load_i64(node, 40)
        node = load_ptr(node, 16)
    return count


@c_abi_export("pcc_gc_continuation_root_slot_count")
def pcc_gc_continuation_root_slot_count() -> int:
    _init_config()
    node = global_load_ptr("pcc_gc_continuation_root_head")
    count: int = 0
    while ptr_is_null(node) == 0:
        count += load_i64(node, 24)
        node = load_ptr(node, 16)
    return count


@c_abi_export("pcc_gc_coroutine_root_score")
def pcc_gc_coroutine_root_score() -> int:
    return (
        pcc_gc_scheduler_root_count()
        + pcc_gc_frame_root_slot_count()
        + pcc_gc_continuation_root_slot_count()
    )


def _slot_in_root_span(slot, slots, count: int) -> int:
    if ptr_is_null(slot) != 0 or ptr_is_null(slots) != 0 or count <= 0:
        return 0
    i: int = 0
    while i < count:
        if ptr_eq(ptr_add(slots, i * 8), slot) != 0:
            return 1
        i += 1
    return 0


@c_abi_export("pcc_gc_slot_is_runtime_root")
def pcc_gc_slot_is_runtime_root(slot) -> int:
    _init_config()
    if ptr_is_null(slot) != 0:
        return 0
    _object_graph_lock()
    node = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(node) == 0:
        if _slot_in_root_span(slot, load_ptr(node, 8), load_i64(node, 40)) != 0:
            _object_graph_unlock()
            return 1
        node = load_ptr(node, 16)
    node = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(node) == 0:
        if _slot_in_root_span(slot, load_ptr(node, 8), load_i64(node, 24)) != 0:
            _object_graph_unlock()
            return 1
        node = load_ptr(node, 16)
    node = global_load_ptr("pcc_gc_scheduler_root_head")
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), slot) != 0:
            _object_graph_unlock()
            return 1
        node = load_ptr(node, 8)
    _object_graph_unlock()
    return 0


@c_abi_export("pcc_gc_register_continuation_root")
def pcc_gc_register_continuation_root(frame_map, slots) -> None:
    _init_config()
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return
    raw_count: int = load_i32(frame_map, 0)
    if raw_count == -2147483648:
        return
    borrowed: int = 0
    root_count: int = raw_count
    if root_count < 0:
        borrowed = 1
        root_count = 0 - root_count
    if root_count <= 0 or root_count > 100000:
        return
    node_size: int = 48 + root_count * 8
    node = malloc(node_size)
    if ptr_is_null(node) != 0:
        return
    memset(node, 0, node_size)
    stable = ptr_add(node, 48)
    store_ptr(node, 0, frame_map)
    store_ptr(node, 8, slots)
    store_i64(node, 24, root_count)
    store_i32(node, 32, borrowed)
    store_ptr(node, 40, stable)
    _object_graph_lock()
    store_ptr(node, 16, global_load_ptr("pcc_gc_continuation_root_head"))
    global_store_ptr("pcc_gc_continuation_root_head", node)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    _object_graph_unlock()


@c_abi_export("pcc_gc_unregister_continuation_root")
def pcc_gc_unregister_continuation_root(slots) -> None:
    _init_config()
    if ptr_is_null(slots) != 0:
        return
    _object_graph_lock()
    prev = null()
    node = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if ptr_eq(load_ptr(node, 8), slots) != 0:
            if ptr_is_null(prev) != 0:
                global_store_ptr("pcc_gc_continuation_root_head", nxt)
            else:
                store_ptr(prev, 16, nxt)
            store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
            _object_graph_unlock()
            free(node)
            return
        prev = node
        node = nxt
    _object_graph_unlock()


@c_abi_export("pcc_gc_trace_continuation_roots")
def pcc_gc_trace_continuation_roots() -> int:
    _init_config()
    traced: int = 0
    _object_graph_lock()
    node = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(node) == 0:
        traced += _gray_mapped_roots(load_ptr(node, 0), load_ptr(node, 8), 0)
        node = load_ptr(node, 16)
    _object_graph_unlock()
    return traced


@c_abi_export("pcc_gc_rewrite_continuation_roots")
def pcc_gc_rewrite_continuation_roots() -> int:
    _init_config()
    rewritten: int = 0
    _object_graph_lock()
    node = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(node) == 0:
        rewritten += _rewrite_mapped_roots(load_ptr(node, 0), load_ptr(node, 8))
        node = load_ptr(node, 16)
    _object_graph_unlock()
    return rewritten


def _scheduler_queue_entry_free(entry) -> None:
    if ptr_is_null(entry):
        return
    _object_graph_lock()
    barrier_before: int = load_i32(global_addr("pcc_gc_relocation_barrier_forwards"), 0)
    _resolve_root_slot_unlocked(entry, 0)
    if pcc_gc_backend() == 4:
        if ptr_is_null(load_ptr(entry, 0)) == 0:
            if load_i32(global_addr("pcc_gc_relocation_forwards"), 0) > 0:
                if (
                    load_i32(global_addr("pcc_gc_relocation_barrier_forwards"), 0)
                    == barrier_before
                ):
                    store_i32(
                        global_addr("pcc_gc_relocation_barrier_forwards"),
                        0,
                        barrier_before + 1,
                    )
    pcc_gc_scheduler_root_unregister_handle(load_ptr(entry, 16))
    store_ptr(entry, 16, null())
    _object_graph_unlock()
    pcc_gc_store_root_extern(entry, null())
    free(entry)


def _scheduler_queue_entry_alloc(queue):
    entry = null()
    if ptr_is_null(queue) == 0:
        mutex = load_ptr(queue, 0)
        if ptr_is_null(mutex) == 0 and pcc_mutex_lock(mutex) == 0:
            entry = load_ptr(queue, 32)
            if ptr_is_null(entry) == 0:
                store_ptr(queue, 32, load_ptr(entry, 8))
                count: int = load_i64(queue, 40)
                if count > 0:
                    store_i64(queue, 40, count - 1)
            pcc_mutex_unlock(mutex)
    if ptr_is_null(entry) != 0:
        entry = malloc(24)
    if ptr_is_null(entry) == 0:
        memset(entry, 0, 24)
    return entry


def _scheduler_queue_entry_recycle(queue, entry) -> None:
    if ptr_is_null(entry) != 0:
        return
    memset(entry, 0, 24)
    if ptr_is_null(queue) != 0:
        free(entry)
        return
    mutex = load_ptr(queue, 0)
    if ptr_is_null(mutex) != 0 or pcc_mutex_lock(mutex) != 0:
        free(entry)
        return
    count: int = load_i64(queue, 40)
    # 4096 == C #define PCC_GC_SCHEDULER_QUEUE_ENTRY_POOL_LIMIT; inlined because a
    # module-level const emits a `.modvar.` global that is zeroed in the stripped
    # runtime-library .o build (see test_runtime_substrate_spike).
    if count >= 4096:
        pcc_mutex_unlock(mutex)
        free(entry)
        return
    store_ptr(entry, 8, load_ptr(queue, 32))
    store_ptr(queue, 32, entry)
    store_i64(queue, 40, count + 1)
    pcc_mutex_unlock(mutex)


def _scheduler_queue_entry_release(queue, entry) -> None:
    if ptr_is_null(entry) != 0:
        return
    _object_graph_lock()
    _resolve_root_slot_unlocked(entry, 0)
    pcc_gc_scheduler_root_unregister_handle(load_ptr(entry, 16))
    store_ptr(entry, 16, null())
    _object_graph_unlock()
    pcc_gc_store_root_extern(entry, null())
    _scheduler_queue_entry_recycle(queue, entry)


@c_abi_export("pcc_gc_scheduler_queue_new")
def pcc_gc_scheduler_queue_new():
    queue = malloc(48)
    if ptr_is_null(queue):
        return null()
    mutex = pcc_mutex_new()
    if ptr_is_null(mutex):
        free(queue)
        return null()
    store_ptr(queue, 0, mutex)
    store_ptr(queue, 8, null())  # head
    store_ptr(queue, 16, null())  # tail
    store_i64(queue, 24, 0)  # length
    store_ptr(queue, 32, null())  # free_head
    store_i64(queue, 40, 0)  # free_count
    return queue


@c_abi_export("pcc_gc_scheduler_queue_free")
def pcc_gc_scheduler_queue_free(queue) -> None:
    if ptr_is_null(queue):
        return
    mutex = load_ptr(queue, 0)
    if ptr_is_null(mutex) == 0:
        pcc_mutex_lock(mutex)
    entry = load_ptr(queue, 8)
    store_ptr(queue, 8, null())
    store_ptr(queue, 16, null())
    store_i64(queue, 24, 0)
    if ptr_is_null(mutex) == 0:
        pcc_mutex_unlock(mutex)
    while ptr_is_null(entry) == 0:
        nxt = load_ptr(entry, 8)
        _scheduler_queue_entry_free(entry)
        entry = nxt
    entry = load_ptr(queue, 32)
    while ptr_is_null(entry) == 0:
        nxt = load_ptr(entry, 8)
        free(entry)
        entry = nxt
    store_ptr(queue, 32, null())
    store_i64(queue, 40, 0)
    if ptr_is_null(mutex) == 0:
        pcc_mutex_free(mutex)
    free(queue)


@c_abi_export("pcc_gc_scheduler_queue_push")
def pcc_gc_scheduler_queue_push(queue, value) -> int:
    if ptr_is_null(queue):
        return -1
    entry = _scheduler_queue_entry_alloc(queue)
    if ptr_is_null(entry):
        return -1
    _object_graph_lock()
    handle = pcc_gc_scheduler_root_register_handle(entry)
    if ptr_is_null(handle) != 0:
        _object_graph_unlock()
        _scheduler_queue_entry_recycle(queue, entry)
        return -1
    store_ptr(entry, 16, handle)
    pcc_gc_store_root_extern(entry, value)
    _object_graph_unlock()
    mutex = load_ptr(queue, 0)
    if pcc_mutex_lock(mutex) != 0:
        _scheduler_queue_entry_release(queue, entry)
        return -1
    tail = load_ptr(queue, 16)
    if ptr_is_null(tail):
        store_ptr(queue, 8, entry)
        store_ptr(queue, 16, entry)
    else:
        store_ptr(tail, 8, entry)
        store_ptr(queue, 16, entry)
    store_i64(queue, 24, load_i64(queue, 24) + 1)
    return pcc_mutex_unlock(mutex)


@c_abi_export("pcc_gc_scheduler_queue_pop_into")
def pcc_gc_scheduler_queue_pop_into(queue, out_slot) -> int:
    if ptr_is_null(queue):
        return -1
    mutex = load_ptr(queue, 0)
    if pcc_mutex_lock(mutex) != 0:
        return -1
    entry = load_ptr(queue, 8)
    if ptr_is_null(entry):
        pcc_mutex_unlock(mutex)
        return 0
    nxt = load_ptr(entry, 8)
    store_ptr(queue, 8, nxt)
    if ptr_is_null(nxt):
        store_ptr(queue, 16, null())
    store_i64(queue, 24, load_i64(queue, 24) - 1)
    pcc_mutex_unlock(mutex)
    _object_graph_lock()
    value = _resolve_root_slot_unlocked(entry, 0)
    if ptr_is_null(out_slot) == 0:
        pcc_gc_store_root_extern(out_slot, value)
    pcc_gc_scheduler_root_unregister_handle(load_ptr(entry, 16))
    store_ptr(entry, 16, null())
    _object_graph_unlock()
    pcc_gc_store_root_extern(entry, null())
    _scheduler_queue_entry_recycle(queue, entry)
    return 1


@c_abi_export("pcc_gc_scheduler_queue_len")
def pcc_gc_scheduler_queue_len(queue) -> int:
    if ptr_is_null(queue):
        return 0
    mutex = load_ptr(queue, 0)
    if pcc_mutex_lock(mutex) != 0:
        return -1
    length: int = load_i64(queue, 24)
    pcc_mutex_unlock(mutex)
    return length


@c_abi_export("pcc_gc_note_frame_enter")
def pcc_gc_note_frame_enter(frame_map, slots) -> None:
    if _frame_roots_disabled_fast() != 0:
        return
    _init_config()
    if _should_track_frame_roots() == 0:
        return
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return
    n_slots: int = load_i32(frame_map, 0)
    if n_slots == -2147483648:
        return
    borrowed: int = 0
    if n_slots < 0:
        borrowed = 1
        n_slots = 0 - n_slots
    if n_slots <= 0 or n_slots > 100000:
        return
    _object_graph_lock()
    node = _frame_node_alloc(n_slots)
    if ptr_is_null(node) != 0:
        _object_graph_unlock()
        return
    stable = ptr_add(node, 64)
    store_ptr(node, 0, frame_map)
    store_ptr(node, 8, slots)
    store_i64(node, 40, n_slots)
    store_i32(node, 48, borrowed)
    store_ptr(node, 56, stable)
    old_head = global_load_ptr("pcc_gc_frame_head")
    store_ptr(node, 16, old_head)
    store_ptr(node, 24, null())
    if ptr_is_null(old_head) == 0:
        store_ptr(old_head, 24, node)
    global_store_ptr("pcc_gc_frame_head", node)
    duplicate = pcc_gc_frame_index_replace(slots, node)
    if ptr_eq(duplicate, node) != 0:
        _frame_node_unlink(node)
        _frame_node_release(node)
        _object_graph_unlock()
        return
    store_ptr(node, 32, duplicate)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    _object_graph_unlock()


@c_abi_export("pcc_gc_note_frame_enter_lifo")
def pcc_gc_note_frame_enter_lifo(frame_map, slots) -> None:
    if _frame_roots_disabled_fast() != 0:
        return
    _init_config()
    if _should_track_frame_roots() == 0:
        return
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return
    n_slots: int = load_i32(frame_map, 0)
    if n_slots == -2147483648:
        return
    borrowed: int = 0
    if n_slots < 0:
        borrowed = 1
        n_slots = 0 - n_slots
    if n_slots <= 0 or n_slots > 100000:
        return
    _object_graph_lock()
    node = _frame_node_alloc(n_slots)
    if ptr_is_null(node) != 0:
        _object_graph_unlock()
        return
    stable = ptr_add(node, 64)
    store_ptr(node, 0, frame_map)
    store_ptr(node, 8, slots)
    store_i64(node, 40, n_slots)
    store_i32(node, 48, borrowed | 2)
    store_ptr(node, 56, stable)
    old_head = global_load_ptr("pcc_gc_frame_head")
    store_ptr(node, 16, old_head)
    store_ptr(node, 24, null())
    store_ptr(node, 32, null())
    if ptr_is_null(old_head) == 0:
        store_ptr(old_head, 24, node)
    global_store_ptr("pcc_gc_frame_head", node)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    _object_graph_unlock()


@c_abi_export("pcc_gc_note_frame_leave_lifo")
def pcc_gc_note_frame_leave_lifo(slots) -> None:
    if _frame_roots_disabled_fast() != 0:
        return
    _init_config()
    if _should_track_frame_roots() == 0:
        return
    if ptr_is_null(slots) != 0:
        return
    _object_graph_lock()
    node = global_load_ptr("pcc_gc_frame_head")
    if (
        ptr_is_null(node) == 0
        and ptr_eq(load_ptr(node, 8), slots) != 0
        and (load_i32(node, 48) & 2) != 0
    ):
        _frame_node_unlink(node)
        _frame_node_release(node)
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
        _object_graph_unlock()
        return
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 8), slots) != 0 and (load_i32(node, 48) & 2) != 0:
            _frame_node_unlink(node)
            _frame_node_release(node)
            store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
            _object_graph_unlock()
            return
        node = load_ptr(node, 16)
    _object_graph_unlock()


def _frame_node_unlink(node) -> None:
    if ptr_is_null(node) != 0:
        return
    prev = load_ptr(node, 24)
    nxt = load_ptr(node, 16)
    if ptr_is_null(prev) != 0:
        global_store_ptr("pcc_gc_frame_head", nxt)
    else:
        store_ptr(prev, 16, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 24, prev)
    store_ptr(node, 16, null())
    store_ptr(node, 24, null())
    store_ptr(node, 32, null())


def _frame_node_bucket(root_count: int) -> int:
    if root_count <= 0 or root_count > 16:
        return 0
    return root_count


def _frame_node_size(root_count: int) -> int:
    return 64 + root_count * 8


def _frame_node_pool_heads():
    heads = global_load_ptr("pcc_gc_frame_node_pool_heads")
    if ptr_is_null(heads) == 0:
        return heads
    heads = malloc(136)
    if ptr_is_null(heads) != 0:
        return heads
    memset(heads, 0, 136)
    global_store_ptr("pcc_gc_frame_node_pool_heads", heads)
    return heads


def _frame_node_pool_counts():
    counts = global_load_ptr("pcc_gc_frame_node_pool_counts")
    if ptr_is_null(counts) == 0:
        return counts
    counts = malloc(136)
    if ptr_is_null(counts) != 0:
        return counts
    memset(counts, 0, 136)
    global_store_ptr("pcc_gc_frame_node_pool_counts", counts)
    return counts


def _frame_node_alloc(root_count: int):
    node_size: int = _frame_node_size(root_count)
    bucket: int = _frame_node_bucket(root_count)
    if bucket != 0:
        heads = _frame_node_pool_heads()
        counts = _frame_node_pool_counts()
        if ptr_is_null(heads) == 0 and ptr_is_null(counts) == 0:
            offset: int = bucket * 8
            head = load_ptr(heads, offset)
            if ptr_is_null(head) == 0:
                nxt = load_ptr(head, 16)
                store_ptr(heads, offset, nxt)
                count: int = load_i64(counts, offset)
                if count > 0:
                    store_i64(counts, offset, count - 1)
                memset(head, 0, node_size)
                return head
    node = malloc(node_size)
    if ptr_is_null(node) == 0:
        memset(node, 0, node_size)
    return node


def _frame_node_release(node) -> None:
    if ptr_is_null(node) != 0:
        return
    root_count: int = load_i64(node, 40)
    bucket: int = _frame_node_bucket(root_count)
    if bucket == 0:
        free(node)
        return
    heads = _frame_node_pool_heads()
    counts = _frame_node_pool_counts()
    if ptr_is_null(heads) != 0 or ptr_is_null(counts) != 0:
        free(node)
        return
    offset: int = bucket * 8
    count: int = load_i64(counts, offset)
    if count >= 1024:
        free(node)
        return
    node_size: int = _frame_node_size(root_count)
    memset(node, 0, node_size)
    store_ptr(node, 16, load_ptr(heads, offset))
    store_ptr(heads, offset, node)
    store_i64(counts, offset, count + 1)


@c_abi_export("pcc_gc_note_frame_leave")
def pcc_gc_note_frame_leave(slots) -> None:
    if _frame_roots_disabled_fast() != 0:
        return
    backend: int = _init_config()
    if _should_track_frame_roots() == 0:
        return
    if ptr_is_null(slots) != 0:
        return
    _object_graph_lock()
    if backend == 0:
        if ptr_is_null(global_load_ptr("pcc_gc_frame_head")) != 0:
            _object_graph_unlock()
            return
    indexed = pcc_gc_frame_index_find(slots)
    if ptr_is_null(indexed) != 0:
        root_slots = global_load_ptr("pcc_gc_root_slots")
        if ptr_eq(root_slots, slots) != 0:
            global_store_ptr("pcc_gc_root_slots", null())
            store_i32(global_addr("pcc_gc_root_count"), 0, 0)
            store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
        _object_graph_unlock()
        return
    if ptr_eq(load_ptr(indexed, 8), slots) != 0:
        duplicate = load_ptr(indexed, 32)
        _frame_node_unlink(indexed)
        if ptr_is_null(duplicate) == 0:
            pcc_gc_frame_index_replace(slots, duplicate)
        else:
            pcc_gc_frame_index_remove(slots)
        _frame_node_release(indexed)
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    else:
        pcc_gc_frame_index_remove(slots)
        pcc_gc_frame_index_insert(load_ptr(indexed, 8), indexed)
    _object_graph_unlock()


@c_abi_export("pcc_gc_thread_unregister_buffers")
def pcc_gc_thread_unregister_buffers() -> None:
    # Called from pcc_threads.c::pcc_thread_trampoline on thread exit. The C
    # runtime (py_gc_backend.c) flushes+frees a PER-THREAD backend-4 medium
    # store-buffer state here. The pcc-Python runtime mirror keeps the
    # backend-4 store buffer as GLOBAL state (see _store_buffer_medium_head;
    # there is no per-thread TLS buffer), so there is nothing per-thread to
    # flush or free on thread exit. This mirror MUST exist: pcc_threads.c is
    # always compiled into every archive variant and references this symbol,
    # but for PCC_RUNTIME_HIGH=py the archive uses py_gc_backend.py (not the
    # .c), so without this stub libpy_runtime_pcc_py.a fails to link with
    # `Undefined symbols: _pcc_gc_thread_unregister_buffers`, breaking every
    # pcc1 / high=py test.
    return
