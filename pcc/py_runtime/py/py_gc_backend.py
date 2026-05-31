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
py_weakref_invalidate = extern("py_weakref_invalidate", (c_ptr,), c_void)
py_user_del_dispatch = extern("py_user_del_dispatch", (c_ptr,), c_void)
py_gc_untrack = extern("py_gc_untrack", (c_ptr,), c_void)
pcc_gc_note_object_freeing = extern("pcc_gc_note_object_freeing", (c_ptr,), c_void)
pcc_gc_load_ptr_extern = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_store_root_extern = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)
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


def _init_config() -> None:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0:
        return
    store_i32(global_addr("pcc_gc_config_initialized"), 0, 1)
    backend: int = _parse_env_i32(
        getenv(cstr("PCC_GC_BACKEND")),
        load_i32(global_addr("pcc_gc_backend_selected"), 0),
        0,
        4,
    )
    pause: int = _parse_env_i32(getenv(cstr("PCC_GC_PAUSE")), 200, 50, 1000)
    stepmul: int = _parse_env_i32(getenv(cstr("PCC_GC_STEPMUL")), 200, 1, 10000)
    stepmul = _parse_env_i32(getenv(cstr("PCC_GC_STEP_MUL")), stepmul, 1, 10000)
    threshold: int = _parse_env_i32(
        getenv(cstr("PCC_GC_DEBT_THRESHOLD")), 0, 0, 1073741824
    )
    minor_heap_size: int = _parse_env_i32(
        getenv(cstr("PCC_GC_MINOR_HEAP_SIZE")), 1048576, 256, 1073741824
    )
    minor_alloc_max: int = _parse_env_i32(
        getenv(cstr("PCC_GC_MINOR_ALLOC_MAX")), 256, 16, 1073741824
    )
    store_i32(global_addr("pcc_gc_backend_selected"), 0, backend)
    store_i32(global_addr("pcc_gc_pause"), 0, pause)
    store_i32(global_addr("pcc_gc_stepmul"), 0, stepmul)
    store_i32(global_addr("pcc_gc_debt_threshold_override"), 0, threshold)
    store_i32(global_addr("pcc_gc_minor_heap_size"), 0, minor_heap_size)
    store_i32(global_addr("pcc_gc_minor_alloc_max"), 0, minor_alloc_max)
    if backend != 0:
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    _maybe_start_cms_worker()


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
    if budget > 128:
        budget = 128
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
    current: int = load_i32(global_addr("pcc_gc_metric_max_pause_us"), 0)
    if pause > current:
        store_i32(global_addr("pcc_gc_metric_max_pause_us"), 0, pause)


def _maybe_auto_step() -> None:
    if load_i32(global_addr("pcc_gc_in_auto_step"), 0) != 0:
        return
    if pcc_gc_backend() != 1:
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
        _step_generational_promotion(1024)
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
    if ptr_eq(block, _minor_current()) != 0:
        pcc_py_atomic_i64_store(ptr_add(block, 8), 0)
        pcc_py_atomic_i32_store(global_addr("pcc_gc_minor_bytes"), 0)
        return
    if _minor_block_owner(block) != pcc_current_thread_id():
        return

    prev = null()
    pcc_py_gc_minor_graph_lock()
    node = _minor_blocks_head()
    while ptr_is_null(node) == 0:
        nxt = _minor_block_next(node)
        if ptr_eq(node, block) != 0:
            if ptr_is_null(prev) != 0:
                _set_minor_blocks_head(nxt)
            else:
                _set_minor_block_next(prev, nxt)
            pcc_py_gc_minor_graph_unlock()
            free(_minor_block_base(node))
            free(node)
            return
        prev = node
        node = nxt
    pcc_py_gc_minor_graph_unlock()


@c_abi_export("pcc_gc_try_minor_alloc")
def pcc_gc_try_minor_alloc(bytes: int):
    _init_config()
    _set_pending_minor_block(null())
    if pcc_gc_backend() != 3:
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
            _minor_collect_reset()
            block = null()
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
    return 1


def _object_node_prev(node):
    return load_ptr(node, 40)


def _set_object_node_prev(node, prev) -> None:
    store_ptr(node, 40, prev)


def _unlink_object_node(node) -> None:
    prev = _object_node_prev(node)
    nxt = _object_node_next(node)
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


def _should_track_frame_roots() -> int:
    if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0:
        return 1
    return load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0)


def _clear_object_list() -> None:
    _object_graph_lock()
    node = _object_head()
    while ptr_is_null(node) == 0:
        nxt = _object_node_next(node)
        free(node)
        node = nxt
    _set_object_head(null())
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
    node = _forwarding_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), from_obj) != 0:
            return node
        node = load_ptr(node, 16)
    return null()


def _forwarding_target_exists(target) -> int:
    if ptr_is_null(target) != 0 or is_tagged_int(target) != 0:
        return 0
    node = _forwarding_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 8), target) != 0:
            return 1
        node = load_ptr(node, 16)
    return 0


def _forwarding_remove(from_obj) -> None:
    if ptr_is_null(from_obj) != 0 or is_tagged_int(from_obj) != 0:
        return
    prev = null()
    node = _forwarding_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if ptr_eq(load_ptr(node, 0), from_obj) != 0:
            if ptr_is_null(prev) != 0:
                _set_forwarding_head(nxt)
            else:
                store_ptr(prev, 16, nxt)
            target = load_ptr(node, 8)
            py_decref(target)
            free(node)
            return
        prev = node
        node = nxt


def _forwarding_clear_all() -> None:
    node = _forwarding_head()
    _set_forwarding_head(null())
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        target = load_ptr(node, 8)
        py_decref(target)
        free(node)
        node = nxt


def _identity_head():
    return global_load_ptr("pcc_gc_identity_head")


def _set_identity_head(head) -> None:
    global_store_ptr("pcc_gc_identity_head", head)


def _identity_find(obj):
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return null()
    node = _identity_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), obj) != 0:
            return node
        node = load_ptr(node, 16)
    return null()


def _identity_ensure(obj):
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return null()
    node = _identity_find(obj)
    if ptr_is_null(node) == 0:
        return node
    node = malloc(24)
    if ptr_is_null(node) != 0:
        return null()
    stable_id: int = load_i32(global_addr("pcc_gc_next_object_id"), 0)
    if stable_id <= 0:
        stable_id = 1
    store_i32(global_addr("pcc_gc_next_object_id"), 0, stable_id + 1)
    store_ptr(node, 0, obj)
    store_i64(node, 8, stable_id)
    store_ptr(node, 16, _identity_head())
    _set_identity_head(node)
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
    node = malloc(24)
    if ptr_is_null(node) != 0:
        return 0
    store_ptr(node, 0, obj)
    store_i64(node, 8, stable_id)
    store_ptr(node, 16, _identity_head())
    _set_identity_head(node)
    return 1


def _identity_remove(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    prev = null()
    node = _identity_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if ptr_eq(load_ptr(node, 0), obj) != 0:
            if ptr_is_null(prev) != 0:
                _set_identity_head(nxt)
            else:
                store_ptr(prev, 16, nxt)
            free(node)
            return
        prev = node
        node = nxt


def _identity_clear_all() -> None:
    node = _identity_head()
    _set_identity_head(null())
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
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            page = load_ptr(node, 8)
            if ptr_is_null(page) != 0:
                return
            current: int = load_i64(page, 40)
            current = current + delta
            if current < 0:
                current = 0
            store_i64(page, 40, current)
            return
        node = load_ptr(node, 16)


def _backend4_zpage_note_remembered_card(owner, delta: int) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    if delta == 0:
        return
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            page = load_ptr(node, 8)
            if ptr_is_null(page) != 0:
                return
            current: int = load_i64(page, 48)
            current = current + delta
            if current < 0:
                current = 0
            store_i64(page, 48, current)
            return
        node = load_ptr(node, 16)


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
    if tag == 5:  # PY_TYPE_LIST
        length: int = load_i64(o, 16)
        items = load_ptr(o, 32)
        i: int = 0
        while i < length:
            _clear_slot(items, i * 8)
            i = i + 1
        store_i64(o, 16, 0)
    elif tag == 7:  # PY_TYPE_TUPLE
        length: int = load_i64(o, 16)
        i: int = 0
        while i < length:
            _clear_slot(o, 24 + i * 8)
            i = i + 1
        store_i64(o, 16, 0)
    elif tag == 6:  # PY_TYPE_DICT
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                key = load_ptr(entries, off + 8)
                if ptr_is_null(key) == 0:
                    _clear_slot(entries, off + 8)
                    _clear_slot(entries, off + 16)
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
            dummy = global_load_ptr("py_set_dummy")
            cap: int = load_i64(o, 24)
            i: int = 0
            while i < cap:
                key = load_ptr(entries, i * 16 + 8)
                if ptr_is_null(key) == 0:
                    store_ptr(entries, i * 16 + 8, null())
                    if ptr_eq(key, dummy) == 0:
                        if _is_sweep_candidate(key) == 0:
                            py_decref(key)
                else:
                    store_ptr(entries, i * 16 + 8, null())
                store_i64(entries, i * 16, 0)
                i = i + 1
        store_i64(o, 16, 0)
        store_i64(o, 32, 0)
    elif tag == 9:  # PY_TYPE_FUNC
        _clear_slot(o, 24)
        _clear_slot(o, 40)
    elif tag == 14:  # PY_TYPE_ITER
        _clear_slot(o, 16)
    elif tag == 15:  # PY_TYPE_GEN
        _clear_slot(o, 24)
        _clear_slot(o, 48)
    elif tag == 20:  # PY_TYPE_COROUTINE
        _clear_slot(o, 32)
        _clear_slot(o, 40)
        _clear_slot(o, 48)
    elif tag == 29:  # PY_TYPE_CONTINUATION
        chunk = load_ptr(o, 24)
        if ptr_is_null(chunk) == 0:
            slots = load_ptr(chunk, 16)
            count: int = load_i64(chunk, 8)
            i: int = 0
            while i < count:
                _clear_slot(slots, i * 8)
                i = i + 1
    elif tag == 28:  # PY_TYPE_TASK
        _clear_slot(o, 16)
        _clear_slot(o, 24)
        _clear_slot(o, 32)
    elif tag == 30:  # PY_TYPE_VIRTUAL_THREAD
        _clear_slot(o, 16)
        _clear_slot(o, 24)
    elif tag == 12:  # PY_TYPE_EXC
        _clear_slot(o, 16)
        _clear_slot(o, 24)
        _clear_slot(o, 32)
        _clear_slot(o, 40)
    elif tag == 10:  # PY_TYPE_CLASS
        _clear_slot(o, 104)
    elif tag == 101:  # PY_TYPE_PROPERTY
        _clear_slot(o, 16)
        _clear_slot(o, 24)
        _clear_slot(o, 32)
    elif tag == 102:  # PY_TYPE_CLASSMETHOD
        _clear_slot(o, 16)
    elif tag == 103:  # PY_TYPE_STATICMETHOD
        _clear_slot(o, 16)
    elif tag == 19:  # PY_TYPE_MEMORYVIEW
        _clear_slot(o, 16)
    elif tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags
        cls = load_ptr(o, 16)
        if ptr_is_null(cls) == 0:
            n_fields: int = load_i32(cls, 72)
            if n_fields < 0:
                n_fields = 0
            i: int = 0
            while i < n_fields:
                _clear_slot(o, 24 + i * 8)
                i = i + 1
            cls_flags: int = load_i32(cls, 12)
            if (cls_flags & 2) == 0:
                _clear_slot(o, 24 + n_fields * 8)
    elif tag == 21:  # PY_TYPE_WEAKREF
        _clear_slot(o, 24)
    elif tag == 27:  # PY_TYPE_THREAD
        _clear_slot(o, 24)  # callable
        _clear_slot(o, 32)  # args
        _clear_slot(o, 40)  # result


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


def _finalize_unreachable(o) -> None:
    # PASS-2 of the two-phase sweep: free an object whose referents were ALREADY
    # cleared by _clear_unreachable. Must not be called before every pending
    # object has been cleared (see _clear_unreachable / _sweep_unreachable).
    if ptr_is_null(o) or is_tagged_int(o):
        return
    pcc_gc_note_object_freeing(o)
    pcc_refcount_forget(o)
    py_gc_untrack(o)
    store_i64(o, 0, 0)
    tag: int = load_i32(o, 8)
    if tag == 2:
        py_dealloc_int(o)
        return
    if tag == 3:
        py_dealloc_float(o)
        return
    if tag == 4:
        py_dealloc_str(o)
        return
    if tag == 5:
        py_dealloc_list(o)
        return
    if tag == 7:
        py_dealloc_tuple(o)
        return
    if tag == 6:
        py_dealloc_dict(o)
        return
    if tag == 8:
        py_dealloc_set(o)
        return
    if tag == 9:
        py_dealloc_func(o)
        return
    if tag == 10:
        py_class_dealloc(o)
        return
    if tag == 11:
        py_instance_dealloc(o)
        return
    if tag == 12:
        py_dealloc_exc(o)
        return
    if tag == 14:
        py_dealloc_iter(o)
        return
    if tag == 15:
        py_dealloc_gen(o)
        return
    if tag == 20:
        py_dealloc_coroutine(o)
        return
    if tag == 29:
        py_dealloc_continuation(o)
        return
    if tag == 19:
        py_dealloc_memoryview(o)
        return
    if tag == 21:
        py_dealloc_weakref(o)
        return
    if tag == 22:
        py_dealloc_thread_lock(o)
        return
    if tag == 23:
        py_dealloc_thread_rlock(o)
        return
    if tag == 24:
        py_dealloc_thread_event(o)
        return
    if tag == 25:
        py_dealloc_thread_condition(o)
        return
    if tag == 26:
        py_dealloc_thread_semaphore(o)
        return
    if tag == 27:
        py_dealloc_thread_thread(o)
        return
    if tag == 28:
        py_dealloc_task(o)
        return
    if tag == 30:
        py_dealloc_virtual_thread(o)
        return
    if tag >= 104:
        py_instance_dealloc(o)
        return
    py_dealloc_generic(o)


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
    existing = _forwarding_find(from_obj)
    if ptr_is_null(existing) == 0:
        return load_ptr(existing, 8)
    if _is_known_object(from_obj) == 0:
        return null()

    flags: int = load_i32(from_obj, 12)
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
    store_i64(to_obj, 0, 0)
    new_flags: int = load_i32(to_obj, 12)
    store_i32(to_obj, 12, (new_flags & ~(128 | 4096 | 512 | 2048)) | 256)

    node = malloc(48)
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
    if ptr_is_null(old_head) == 0:
        _set_object_node_prev(old_head, node)
    _set_object_head(node)
    if pcc_gc_object_index_insert(to_obj, node) < 0:
        _unlink_object_node(node)
        free(node)
        free(to_obj)
        return null()
    live: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
    store_i32(global_addr("pcc_gc_live_bytes"), 0, live + size)

    if _install_forwarding_unlocked(from_obj, to_obj) != 0:
        pcc_gc_object_index_remove(to_obj)
        _unlink_object_node(node)
        free(node)
        live2: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
        if size >= live2:
            store_i32(global_addr("pcc_gc_live_bytes"), 0, 0)
        else:
            store_i32(global_addr("pcc_gc_live_bytes"), 0, live2 - size)
        _identity_remove(to_obj)
        free(to_obj)
        return null()

    _mark_forwarded_source_inactive(from_obj)
    store_i32(from_obj, 12, (flags & ~128) | 256)
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
        if ptr_is_null(_generational_oldify_copy(o)) == 0:
            return
        store_i32(o, 12, (flags & ~128) | 256)
        if pcc_gc_backend() == 4:
            _backend4_zpage_note_owner_promoted(o)


def _promote_young_slot(slot_base, slot_offset: int) -> None:
    child = load_ptr(slot_base, slot_offset)
    if ptr_is_null(child) != 0:
        return
    if is_tagged_int(child) != 0:
        return
    oldified = _generational_oldify_copy(child)
    if ptr_is_null(oldified) == 0:
        if ptr_eq(oldified, child) == 0:
            py_incref(oldified)
            store_ptr(slot_base, slot_offset, oldified)
            py_decref(child)
            return
    _promote_young_if_known(child)


def _promote_young_borrowed_slot(slot_base, slot_offset: int) -> None:
    child = load_ptr(slot_base, slot_offset)
    if ptr_is_null(child) != 0:
        return
    if is_tagged_int(child) != 0:
        return
    oldified = _generational_oldify_copy(child)
    if ptr_is_null(oldified) == 0:
        if ptr_eq(oldified, child) == 0:
            store_ptr(slot_base, slot_offset, oldified)
            return
    _promote_young_if_known(child)


def _gray_exists() -> int:
    node = _object_head()
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) == 0:
            node = _object_node_next(node)
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        if (flags & 16) != 0:
            return 1
        node = _object_node_next(node)
    return 0


def _resolve_root_slot_unlocked(slot_base, slot_offset: int):
    value = load_ptr(slot_base, slot_offset)
    if ptr_is_null(value) != 0:
        return value
    if is_tagged_int(value) != 0:
        return value
    if _is_known_object(value) == 0:
        return value
    flags: int = load_i32(value, 12)
    store_i32(value, 12, flags & ~2048)
    forwarding = _forwarding_find(value)
    if ptr_is_null(forwarding) != 0:
        return value
    resolved = load_ptr(forwarding, 8)
    if ptr_is_null(resolved) != 0:
        return value
    if ptr_eq(resolved, value) != 0:
        return value
    py_incref(resolved)
    store_ptr(slot_base, slot_offset, resolved)
    py_decref(value)
    return resolved


def _mapped_root_count(frame_map) -> int:
    if ptr_is_null(frame_map) != 0:
        return 0
    root_count: int = load_i32(frame_map, 0)
    if root_count < 0 or root_count > 100000:
        return 0
    return root_count


def _gray_mapped_roots(frame_map, root_slots, resolve: int) -> int:
    root_count: int = _mapped_root_count(frame_map)
    if root_count <= 0 or ptr_is_null(root_slots) != 0:
        return 0
    i: int = 0
    while i < root_count:
        if resolve != 0:
            _mark_root_gray_if_known(_resolve_root_slot_unlocked(root_slots, i * 8))
        else:
            _mark_root_gray_if_known(load_ptr(root_slots, i * 8))
        i = i + 1
    return root_count


def _rewrite_mapped_roots(frame_map, root_slots) -> int:
    root_count: int = _mapped_root_count(frame_map)
    if root_count <= 0 or ptr_is_null(root_slots) != 0:
        return 0
    rewritten: int = 0
    i: int = 0
    while i < root_count:
        before = load_ptr(root_slots, i * 8)
        after = _resolve_root_slot_unlocked(root_slots, i * 8)
        if ptr_eq(before, after) == 0:
            rewritten = rewritten + 1
        i = i + 1
    return rewritten


def _gray_current_roots() -> None:
    node = _object_head()
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) == 0:
            node = _object_node_next(node)
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        if (flags & 64) != 0:
            _mark_root_gray_if_known(o)
        node = _object_node_next(node)

    frame = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(frame) == 0:
        _gray_mapped_roots(load_ptr(frame, 0), load_ptr(frame, 8), 1)
        frame = load_ptr(frame, 16)

    cont = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(cont) == 0:
        _gray_mapped_roots(load_ptr(cont, 0), load_ptr(cont, 8), 1)
        cont = load_ptr(cont, 16)

    sched = global_load_ptr("pcc_gc_scheduler_root_head")
    while ptr_is_null(sched) == 0:
        slot = load_ptr(sched, 0)
        if ptr_is_null(slot) == 0:
            _mark_root_gray_if_known(_resolve_root_slot_unlocked(slot, 0))
        sched = load_ptr(sched, 8)


def _seed_roots() -> None:
    node = _object_head()
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) == 0:
            node = _object_node_next(node)
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        explicit_collect: int = load_i32(
            global_addr("pcc_gc_explicit_collect_active"),
            0,
        )
        if (flags & 16384) != 0 and explicit_collect == 0:
            store_i32(o, 12, (flags & ~(56 | 16384)) | 32)
        else:
            store_i32(o, 12, (flags & ~(56 | 16384)) | 8)
        node = _object_node_next(node)
    _gray_current_roots()


def _drain_all_gray_unlocked() -> int:
    processed: int = 0
    while True:
        local_processed: int = 0
        node = _object_head()
        while ptr_is_null(node) == 0:
            if _object_node_is_active(node) == 0:
                node = _object_node_next(node)
                continue
            o = load_ptr(node, 0)
            flags: int = load_i32(o, 12)
            if (flags & 16) != 0:
                _trace_referents(o)
                store_i32(o, 12, (flags & ~56) | 32)
                local_processed = local_processed + 1
                processed = processed + 1
            node = _object_node_next(node)
        if local_processed == 0:
            break
    return processed


def _begin_mark_cycle() -> None:
    _seed_roots()
    store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 0)
    if _gray_exists() == 0:
        store_i32(global_addr("pcc_gc_mark_active"), 0, 0)


def _finish_tracing_cycle() -> int:
    stw: int = pcc_stop_the_world()
    if stw != 0:
        return 0
    _gray_current_roots()
    _drain_all_gray_unlocked()
    node = _object_head()
    while ptr_is_null(node) == 0:
        if _object_node_is_active(node) == 0:
            pcc_thread_safepoint()
            node = _object_node_next(node)
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        if (flags & 8) != 0:
            store_i32(o, 12, flags | 1024)
        else:
            store_i32(o, 12, flags & ~1024)
        pcc_thread_safepoint()
        node = _object_node_next(node)
    if stw == 0:
        pcc_resume_world()
    return 1


def _trace_referents(o) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    tag: int = load_i32(o, 8)
    if tag == 5:  # PY_TYPE_LIST
        length: int = load_i64(o, 16)
        items = load_ptr(o, 32)
        i: int = 0
        while i < length:
            _mark_gray_if_known(load_ptr(items, i * 8))
            i = i + 1
    elif tag == 7:  # PY_TYPE_TUPLE
        length: int = load_i64(o, 16)
        i: int = 0
        while i < length:
            _mark_gray_if_known(load_ptr(o, 24 + i * 8))
            i = i + 1
    elif tag == 6:  # PY_TYPE_DICT
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                key = load_ptr(entries, off + 8)
                if ptr_is_null(key) == 0:
                    _mark_gray_if_known(key)
                    _mark_gray_if_known(load_ptr(entries, off + 16))
                i = i + 1
    elif tag == 8:  # PY_TYPE_SET
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            dummy = global_load_ptr("py_set_dummy")
            capacity: int = load_i64(o, 24)
            i: int = 0
            while i < capacity:
                key = load_ptr(entries, i * 16 + 8)
                if ptr_is_null(key) == 0:
                    if ptr_eq(key, dummy) == 0:
                        _mark_gray_if_known(key)
                i = i + 1
    elif tag == 9:  # PY_TYPE_FUNC
        _mark_gray_if_known(load_ptr(o, 24))
        _mark_gray_if_known(load_ptr(o, 40))
    elif tag == 10:  # PY_TYPE_CLASS
        n_bases: int = load_i32(o, 24)
        bases = load_ptr(o, 32)
        if ptr_is_null(bases) == 0:
            i: int = 0
            while i < n_bases:
                _mark_gray_if_known(load_ptr(bases, i * 8))
                i = i + 1
        n_mro: int = load_i32(o, 40)
        mro = load_ptr(o, 48)
        if ptr_is_null(mro) == 0:
            j: int = 0
            while j < n_mro:
                _mark_gray_if_known(load_ptr(mro, j * 8))
                j = j + 1
        n_methods: int = load_i32(o, 56)
        methods = load_ptr(o, 64)
        if ptr_is_null(methods) == 0:
            k: int = 0
            while k < n_methods:
                k = k + 1
        _mark_gray_if_known(load_ptr(o, 104))
        _mark_gray_if_known(load_ptr(o, 112))
    elif tag == 14:  # PY_TYPE_ITER
        _mark_gray_if_known(load_ptr(o, 16))
    elif tag == 15:  # PY_TYPE_GEN
        _mark_gray_if_known(load_ptr(o, 24))
        _mark_gray_if_known(load_ptr(o, 48))
    elif tag == 20:  # PY_TYPE_COROUTINE
        _mark_gray_if_known(load_ptr(o, 32))
        _mark_gray_if_known(load_ptr(o, 40))
        _mark_gray_if_known(load_ptr(o, 48))
    elif tag == 29:  # PY_TYPE_CONTINUATION
        chunk = load_ptr(o, 24)
        if ptr_is_null(chunk) == 0:
            slots = load_ptr(chunk, 16)
            count: int = load_i64(chunk, 8)
            i: int = 0
            while i < count:
                _mark_gray_if_known(load_ptr(slots, i * 8))
                i = i + 1
    elif tag == 28:  # PY_TYPE_TASK
        _mark_gray_if_known(load_ptr(o, 16))
        _mark_gray_if_known(load_ptr(o, 24))
        _mark_gray_if_known(load_ptr(o, 32))
    elif tag == 30:  # PY_TYPE_VIRTUAL_THREAD
        _mark_gray_if_known(load_ptr(o, 16))
        _mark_gray_if_known(load_ptr(o, 24))
    elif tag == 12:  # PY_TYPE_EXC
        _mark_gray_if_known(load_ptr(o, 16))
        _mark_gray_if_known(load_ptr(o, 24))
        _mark_gray_if_known(load_ptr(o, 32))
        _mark_gray_if_known(load_ptr(o, 40))
    elif tag == 101:  # PY_TYPE_PROPERTY
        _mark_gray_if_known(load_ptr(o, 16))
        _mark_gray_if_known(load_ptr(o, 24))
        _mark_gray_if_known(load_ptr(o, 32))
    elif tag == 102:  # PY_TYPE_CLASSMETHOD
        _mark_gray_if_known(load_ptr(o, 16))
    elif tag == 103:  # PY_TYPE_STATICMETHOD
        _mark_gray_if_known(load_ptr(o, 16))
    elif tag == 19:  # PY_TYPE_MEMORYVIEW
        _mark_gray_if_known(load_ptr(o, 16))
    elif tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags
        cls = load_ptr(o, 16)
        if ptr_is_null(cls) == 0:
            _mark_gray_if_known(cls)
            n_fields: int = load_i32(cls, 72)
            if n_fields < 0:
                n_fields = 0
            i: int = 0
            while i < n_fields:
                _mark_gray_if_known(load_ptr(o, 24 + i * 8))
                i = i + 1
            cls_flags: int = load_i32(cls, 12)
            if (cls_flags & 2) == 0:
                _mark_gray_if_known(load_ptr(o, 24 + n_fields * 8))
    elif tag == 21:  # PY_TYPE_WEAKREF
        _mark_gray_if_known(load_ptr(o, 24))
    elif tag == 27:  # PY_TYPE_THREAD
        _mark_gray_if_known(load_ptr(o, 24))
        _mark_gray_if_known(load_ptr(o, 32))
        _mark_gray_if_known(load_ptr(o, 40))


def _trace_referents_for_promotion(o) -> None:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return
    tag: int = load_i32(o, 8)
    if tag == 5:  # PY_TYPE_LIST
        length: int = load_i64(o, 16)
        items = load_ptr(o, 32)
        i: int = 0
        while i < length:
            _promote_young_slot(items, i * 8)
            i = i + 1
    elif tag == 7:  # PY_TYPE_TUPLE
        length: int = load_i64(o, 16)
        i: int = 0
        while i < length:
            _promote_young_slot(o, 24 + i * 8)
            i = i + 1
    elif tag == 6:  # PY_TYPE_DICT
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            used: int = load_i64(o, 48)
            i: int = 0
            while i < used:
                off: int = i * 24
                key = load_ptr(entries, off + 8)
                if ptr_is_null(key) == 0:
                    _promote_young_slot(entries, off + 8)
                    _promote_young_slot(entries, off + 16)
                i = i + 1
    elif tag == 8:  # PY_TYPE_SET
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            dummy = global_load_ptr("py_set_dummy")
            capacity: int = load_i64(o, 24)
            i: int = 0
            while i < capacity:
                key = load_ptr(entries, i * 16 + 8)
                if ptr_is_null(key) == 0:
                    if ptr_eq(key, dummy) == 0:
                        _promote_young_slot(entries, i * 16 + 8)
                i = i + 1
    elif tag == 9:  # PY_TYPE_FUNC
        _promote_young_slot(o, 24)
        _promote_young_slot(o, 40)
    elif tag == 10:  # PY_TYPE_CLASS
        n_bases: int = load_i32(o, 24)
        bases = load_ptr(o, 32)
        if ptr_is_null(bases) == 0:
            i: int = 0
            while i < n_bases:
                _promote_young_borrowed_slot(bases, i * 8)
                i = i + 1
        n_mro: int = load_i32(o, 40)
        mro = load_ptr(o, 48)
        if ptr_is_null(mro) == 0:
            j: int = 0
            while j < n_mro:
                _promote_young_borrowed_slot(mro, j * 8)
                j = j + 1
        n_methods: int = load_i32(o, 56)
        methods = load_ptr(o, 64)
        if ptr_is_null(methods) == 0:
            k: int = 0
            while k < n_methods:
                _promote_young_borrowed_slot(methods, k * 16 + 8)
                k = k + 1
        _promote_young_borrowed_slot(o, 96)
        _promote_young_slot(o, 104)
        _promote_young_borrowed_slot(o, 112)
    elif tag == 14:  # PY_TYPE_ITER
        _promote_young_slot(o, 16)
    elif tag == 15:  # PY_TYPE_GEN
        _promote_young_slot(o, 24)
        _promote_young_slot(o, 48)
    elif tag == 20:  # PY_TYPE_COROUTINE
        _promote_young_slot(o, 32)
        _promote_young_slot(o, 40)
        _promote_young_slot(o, 48)
    elif tag == 29:  # PY_TYPE_CONTINUATION
        chunk = load_ptr(o, 24)
        if ptr_is_null(chunk) == 0:
            slots = load_ptr(chunk, 16)
            count: int = load_i64(chunk, 8)
            i: int = 0
            while i < count:
                _promote_young_slot(slots, i * 8)
                i = i + 1
    elif tag == 28:  # PY_TYPE_TASK
        _promote_young_slot(o, 16)
        _promote_young_slot(o, 24)
        _promote_young_slot(o, 32)
    elif tag == 30:  # PY_TYPE_VIRTUAL_THREAD
        _promote_young_slot(o, 16)
        _promote_young_slot(o, 24)
    elif tag == 12:  # PY_TYPE_EXC
        _promote_young_slot(o, 16)
        _promote_young_slot(o, 24)
        _promote_young_slot(o, 32)
        _promote_young_slot(o, 40)
    elif tag == 101:  # PY_TYPE_PROPERTY
        _promote_young_slot(o, 16)
        _promote_young_slot(o, 24)
        _promote_young_slot(o, 32)
    elif tag == 102:  # PY_TYPE_CLASSMETHOD
        _promote_young_slot(o, 16)
    elif tag == 103:  # PY_TYPE_STATICMETHOD
        _promote_young_slot(o, 16)
    elif tag == 19:  # PY_TYPE_MEMORYVIEW
        _promote_young_slot(o, 16)
    elif tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags
        cls = load_ptr(o, 16)
        if ptr_is_null(cls) == 0:
            _promote_young_if_known(cls)
            n_fields: int = load_i32(cls, 72)
            if n_fields < 0:
                n_fields = 0
            i: int = 0
            while i < n_fields:
                _promote_young_slot(o, 24 + i * 8)
                i = i + 1
            cls_flags: int = load_i32(cls, 12)
            if (cls_flags & 2) == 0:
                _promote_young_slot(o, 24 + n_fields * 8)
    elif tag == 21:  # PY_TYPE_WEAKREF
        _promote_young_slot(o, 24)
    elif tag == 27:  # PY_TYPE_THREAD
        _promote_young_slot(o, 24)
        _promote_young_slot(o, 32)
        _promote_young_slot(o, 40)


def _promote_frame_roots(remaining_budget: int) -> None:
    if remaining_budget <= 0:
        return
    frame = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(frame) == 0:
        frame_map = load_ptr(frame, 0)
        root_slots = load_ptr(frame, 8)
        root_count: int = _mapped_root_count(frame_map)
        if root_count > 0 and ptr_is_null(root_slots) == 0:
            i: int = 0
            while i < root_count:
                _promote_young_slot(root_slots, i * 8)
                i = i + 1
        frame = load_ptr(frame, 16)

    cont = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(cont) == 0:
        frame_map = load_ptr(cont, 0)
        root_slots = load_ptr(cont, 8)
        root_count: int = _mapped_root_count(frame_map)
        if root_count > 0 and ptr_is_null(root_slots) == 0:
            i: int = 0
            while i < root_count:
                _promote_young_slot(root_slots, i * 8)
                i = i + 1
        cont = load_ptr(cont, 16)

    sched = global_load_ptr("pcc_gc_scheduler_root_head")
    while ptr_is_null(sched) == 0:
        slot = load_ptr(sched, 0)
        if ptr_is_null(slot) == 0:
            _promote_young_slot(slot, 0)
        sched = load_ptr(sched, 8)


@c_abi_export("pcc_gc_backend")
def pcc_gc_backend() -> int:
    _init_config()
    return load_i32(global_addr("pcc_gc_backend_selected"), 0)


@c_abi_export("pcc_gc_set_backend")
def pcc_gc_set_backend(backend: int) -> int:
    _init_config()
    if backend < 0 or backend > 4:
        return -1
    old_backend: int = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend == 0:
        store_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0, 1)
    store_i32(global_addr("pcc_gc_backend_selected"), 0, backend)
    store_i32(global_addr("pcc_gc_mark_active"), 0, 0)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
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
    i: int = 0
    while i <= 5:
        store_i32(_counter_global(i), 0, 0)
        i = i + 1
    store_i32(global_addr("pcc_gc_metric_max_pause_us"), 0, 0)
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


def _backend4_zpage_find_reusable_page_for_gen(size: int, generation: int):
    if size <= 0 or size > 65536:
        return null()
    wanted_class: int = _backend4_page_class_for_size(size)
    alloc_size: int = _backend4_align_alloc_size(size)
    page = _zpage_page_head()
    while ptr_is_null(page) == 0:
        page_class: int = load_i32(page, 24)
        page_generation: int = load_i32(page, 28)
        capacity: int = load_i64(page, 16)
        allocated: int = load_i64(page, 64)
        if (
            ptr_is_null(_backend4_evacuation_page_find(page)) != 0
            and page_class == wanted_class
            and page_generation == generation
            and capacity - allocated >= alloc_size
        ):
            return page
        page = load_ptr(page, 56)
    return null()


def _backend4_zpage_find_reusable_page(owner, size: int):
    if size <= 0 or size > 65536:
        return null()
    wanted_class: int = _backend4_page_class_for_size(size)
    wanted_generation: int = _backend4_zpage_generation_for_owner(owner)
    alloc_size: int = _backend4_align_alloc_size(size)
    page = _zpage_page_head()
    while ptr_is_null(page) == 0:
        page_class: int = load_i32(page, 24)
        page_generation: int = load_i32(page, 28)
        capacity: int = load_i64(page, 16)
        allocated: int = load_i64(page, 64)
        if (
            ptr_is_null(_backend4_evacuation_page_find(page)) != 0
            and page_class == wanted_class
            and page_generation == wanted_generation
            and capacity - allocated >= alloc_size
        ):
            return page
        page = load_ptr(page, 56)
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
    span = load_ptr(page, 72)
    span_capacity: int = load_i64(page, 80)
    if ptr_is_null(span) != 0 or span_capacity < capacity:
        if ptr_is_null(span) == 0:
            free(span)
        span = malloc(capacity)
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


def _backend4_zpage_recycle(page) -> None:
    if ptr_is_null(page) != 0:
        return
    page_class: int = load_i32(page, 24)
    if page_class > 1:
        _backend4_zpage_destroy(page)
        return
    limit: int = _backend4_free_page_limit_for_class(page_class)
    if limit <= 0 or _backend4_free_page_count_for_class(page_class) >= limit:
        _backend4_zpage_destroy(page)
        return
    store_ptr(page, 0, null())
    store_i64(page, 8, 0)
    store_i64(page, 64, 0)
    store_i64(page, 32, 0)
    store_i64(page, 40, 0)
    store_i64(page, 48, 0)
    store_i64(page, 88, 0)
    store_ptr(page, 56, _zpage_free_page_head())
    _set_zpage_free_page_head(page)


def _backend4_zpage_destroy(page) -> None:
    if ptr_is_null(page) != 0:
        return
    span = load_ptr(page, 72)
    if ptr_is_null(span) == 0:
        free(span)
    free(page)


def _backend4_zpage_note_owner_promoted(owner) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            page = load_ptr(node, 8)
            if ptr_is_null(page) == 0:
                store_i32(page, 28, 2)
            return
        node = load_ptr(node, 16)


def _backend4_zpage_find_page_for_addr(ptr, size: int):
    if ptr_is_null(ptr) != 0 or size <= 0:
        return null()
    alloc_size: int = _backend4_align_alloc_size(size)
    page = _zpage_page_head()
    while ptr_is_null(page) == 0:
        span = load_ptr(page, 72)
        span_capacity: int = load_i64(page, 80)
        if ptr_is_null(span) == 0 and span_capacity > 0:
            delta: int = ptr_diff(ptr, span)
            if delta >= 0 and delta + alloc_size <= span_capacity:
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
    return _backend4_zpage_list_owns_addr(_zpage_free_page_head(), ptr)


@c_abi_export("pcc_gc_backend4_try_zpage_alloc")
def pcc_gc_backend4_try_zpage_alloc(size: int, flags: int):
    _init_config()
    if pcc_gc_backend() != 4:
        return null()
    if size < 16:
        return null()
    alloc_size: int = _backend4_align_alloc_size(size)
    generation: int = _backend4_generation_for_flags(flags)
    _object_graph_lock()
    page_needs_reset: int = 0
    page = _backend4_zpage_find_reusable_page_for_gen(size, generation)
    if ptr_is_null(page) != 0:
        page = _backend4_zpage_pop_free_page(size)
        if ptr_is_null(page) == 0:
            page_needs_reset = 1
    if ptr_is_null(page) != 0:
        page = malloc(96)
        if ptr_is_null(page) != 0:
            _object_graph_unlock()
            return null()
        memset(page, 0, 96)
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
    _object_graph_unlock()
    return obj


def _backend4_zpage_track_alloc(owner, size: int) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    if pcc_gc_backend() != 4:
        return
    node = malloc(40)
    if ptr_is_null(node) != 0:
        return
    page = _backend4_zpage_find_reusable_page(owner, size)
    existing_offset: int = -1
    if (load_i32(owner, 12) & 65536) != 0:
        page = _backend4_zpage_find_page_for_addr(owner, size)
        if ptr_is_null(page) == 0:
            span = load_ptr(page, 72)
            existing_offset = ptr_diff(owner, span)
    if ptr_is_null(page) != 0:
        page = _backend4_zpage_pop_free_page(size)
    if ptr_is_null(page) != 0:
        page = malloc(96)
        if ptr_is_null(page) != 0:
            free(node)
            return
        memset(page, 0, 96)
    if existing_offset < 0 and load_i64(page, 32) <= 0:
        _backend4_zpage_reset(page, owner, size)
        store_ptr(page, 56, _zpage_page_head())
        _set_zpage_page_head(page)
    store_ptr(node, 0, owner)
    store_ptr(node, 8, page)
    store_ptr(node, 16, _zpage_head())
    allocated: int = load_i64(page, 64)
    if existing_offset >= 0:
        pending: int = load_i64(page, 88)
        if pending > 0:
            store_i64(page, 88, pending - 1)
        store_i64(node, 24, existing_offset)
    else:
        store_i64(node, 24, allocated)
    store_i64(node, 32, size)
    if existing_offset < 0:
        store_i64(page, 64, allocated + _backend4_align_alloc_size(size))
    store_i64(page, 8, load_i64(page, 8) + size)
    store_i64(page, 32, load_i64(page, 32) + 1)
    if ptr_is_null(load_ptr(page, 0)) != 0:
        store_ptr(page, 0, owner)
    _set_zpage_head(node)


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
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 8), page) != 0:
            return load_ptr(node, 0)
        node = load_ptr(node, 16)
    return null()


def _backend4_zpage_remove_payload_spans(owner) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    prev = null()
    node = _zpage_payload_span_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 40)
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            if ptr_is_null(prev) != 0:
                _set_zpage_payload_span_head(nxt)
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
            node = nxt
            continue
        prev = node
        node = nxt


def _backend4_zpage_remove(owner) -> None:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return
    if pcc_gc_backend() != 4:
        return
    prev = null()
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            if ptr_is_null(prev) != 0:
                _set_zpage_head(nxt)
            else:
                store_ptr(prev, 16, nxt)
            page = load_ptr(node, 8)
            if ptr_is_null(page) == 0:
                _backend4_zpage_remove_payload_spans(owner)
                size: int = _object_known_size(owner)
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
                    _backend4_zpage_unlink_page(page)
                    _backend4_zpage_recycle(page)
            free(node)
            node = nxt
            continue
        prev = node
        node = nxt


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
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            return load_i64(node, 24)
        node = load_ptr(node, 16)
    return -1


@c_abi_export("pcc_gc_backend4_zpage_owner_size_bytes")
def pcc_gc_backend4_zpage_owner_size_bytes(owner) -> int:
    _init_config()
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            return load_i64(node, 32)
        node = load_ptr(node, 16)
    return -1


@c_abi_export("pcc_gc_backend4_zpage_owner_span_card")
def pcc_gc_backend4_zpage_owner_span_card(owner) -> int:
    offset: int = pcc_gc_backend4_zpage_owner_offset_bytes(owner)
    if offset < 0:
        return -1
    return (offset // 512) % 64


def _backend4_zpage_payload_offset_for_slot(owner, slot) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    if ptr_is_null(slot) != 0:
        return -1
    span = _zpage_payload_span_head()
    while ptr_is_null(span) == 0:
        if ptr_eq(load_ptr(span, 0), owner) != 0:
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
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
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
                        owner,
                        slot,
                    )
                    if payload_offset >= 0:
                        span_offset = payload_offset
            return (span_offset // 512) % 64
        node = load_ptr(node, 16)
    return -1


@c_abi_export("pcc_gc_backend4_zpage_register_owner_payload_span")
def pcc_gc_backend4_zpage_register_owner_payload_span(
    owner, base, size_bytes: int
) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    if ptr_is_null(base) != 0 or size_bytes <= 0:
        return -1
    _init_config()
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            page = load_ptr(node, 8)
            if ptr_is_null(page) != 0:
                return -1
            _backend4_zpage_remove_payload_spans(owner)
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
            store_ptr(span, 40, _zpage_payload_span_head())
            _set_zpage_payload_span_head(span)
            store_i64(page, 64, allocated + size_bytes)
            store_i64(page, 8, load_i64(page, 8) + size_bytes)
            return allocated
        node = load_ptr(node, 16)
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

    node = _object_head()
    while ptr_is_null(node) == 0 and local_processed < remaining_budget:
        if _object_node_is_active(node) == 0:
            node = _object_node_next(node)
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        if (flags & 16) != 0:
            _trace_referents(o)
            store_i32(o, 12, (flags & ~56) | 32)
            local_processed = local_processed + 1
        node = _object_node_next(node)

    if _gray_exists() == 0:
        if _finish_tracing_cycle() != 0:
            store_i32(global_addr("pcc_gc_mark_active"), 0, 0)
            store_i32(global_addr("pcc_gc_cycle_requested"), 0, 0)

    _object_graph_unlock()
    return local_processed


def _step_generational_promotion(remaining_budget: int) -> int:
    if remaining_budget <= 0:
        return 0
    _object_graph_lock()
    _promote_frame_roots(remaining_budget)
    local_processed: int = 0
    node = _object_head()
    while ptr_is_null(node) == 0 and local_processed < remaining_budget:
        if _object_node_is_active(node) == 0:
            node = _object_node_next(node)
            continue
        o = load_ptr(node, 0)
        flags: int = load_i32(o, 12)
        if (flags & 512) != 0:
            _trace_referents_for_promotion(o)
            store_i32(o, 12, flags & ~512)
            local_processed = local_processed + 1
            if (local_processed % 16) == 0:
                pcc_thread_safepoint()
        node = _object_node_next(node)
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
            store_i32(o, 12, (flags & ~128) | 256)
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
    _init_config()
    if budget <= 0:
        return 0
    _counter_inc(5, 1)
    start_us: int = pcc_runtime_now_us()
    processed: int = 0
    backend: int = pcc_gc_backend()

    if backend == 1 or backend == 2:
        processed = processed + _step_tracing(budget)
    elif backend == 3:
        processed = processed + _step_generational_promotion(budget)
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
    if backend == 1 or backend == 2:
        _discharge_debt(processed)
        if load_i32(global_addr("pcc_gc_mark_active"), 0) == 0:
            if load_i32(global_addr("pcc_gc_cycle_requested"), 0) == 0:
                store_i32(global_addr("pcc_gc_debt_bytes"), 0, 0)
    _record_pause(start_us, pcc_runtime_now_us())
    return processed


@c_abi_export("pcc_gc_has_tracing_sweep")
def pcc_gc_has_tracing_sweep() -> int:
    _init_config()
    backend: int = pcc_gc_backend()
    if backend != 1 and backend != 2 and backend != 3 and backend != 4:
        return 0
    if _has_sweep_candidate() != 0:
        return 1
    return 0


@c_abi_export("pcc_gc_collect_tracing")
def pcc_gc_collect_tracing() -> int:
    _init_config()
    backend: int = pcc_gc_backend()
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
    _init_config()
    store_i32(global_addr("pcc_gc_explicit_collect_active"), 0, 1)
    backend: int = pcc_gc_backend()
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
    release_block = null()
    _object_graph_lock()
    node = pcc_gc_object_index_find(o)
    if ptr_is_null(node) == 0:
        block = _object_node_minor_block(node)
        if ptr_is_null(block) == 0:
            if _object_node_freeing(node) == 0:
                live: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
                size: int = _object_node_size(node)
                if size >= live:
                    store_i32(global_addr("pcc_gc_live_bytes"), 0, 0)
                else:
                    store_i32(global_addr("pcc_gc_live_bytes"), 0, live - size)
            _backend4_zpage_remove(o)
            pcc_gc_object_index_remove(o)
            _unlink_object_node(node)
            free(node)
            release_block = block
            _object_graph_unlock()
            _minor_release_block(release_block)
            return
    _object_graph_unlock()
    flags: int = load_i32(o, 12)
    if (flags & 65536) != 0:
        return
    if pcc_gc_backend() == 4:
        _object_graph_lock()
        zpage_owned: int = _backend4_zpage_owns_addr(o)
        _object_graph_unlock()
        if zpage_owned != 0:
            return
    if (flags & 4096) != 0:
        return
    free(o)


@c_abi_export("pcc_gc_note_alloc")
def pcc_gc_note_alloc(bytes: int) -> None:
    _init_config()
    if bytes < 0:
        bytes = 0
    _counter_inc(0, 1)
    if pcc_gc_backend() == 1:
        debt: int = load_i32(global_addr("pcc_gc_debt_bytes"), 0) + bytes
        store_i32(global_addr("pcc_gc_debt_bytes"), 0, debt)
        _maybe_auto_step()
    elif pcc_gc_backend() == 2:
        _maybe_start_cms_worker()
        _note_cms_alloc(bytes)
    elif pcc_gc_backend() == 3:
        return


@c_abi_export("pcc_gc_note_object_allocated_sized")
def pcc_gc_note_object_allocated_sized(o, size: int) -> None:
    _init_config()
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    if size < 16:
        size = 16
    backend: int = pcc_gc_backend()
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
    node = malloc(48)
    if ptr_is_null(node) == 0:
        _object_graph_lock()
        old_head = _object_head()
        store_ptr(node, 0, o)
        store_i64(node, 8, size)
        store_ptr(node, 16, old_head)
        store_ptr(node, 24, pending_block)
        store_i64(node, 32, 0)
        store_ptr(node, 40, null())
        if ptr_is_null(old_head) == 0:
            _set_object_node_prev(old_head, node)
        _set_object_head(node)
        pcc_gc_object_index_insert(o, node)
        live: int = load_i32(global_addr("pcc_gc_live_bytes"), 0)
        store_i32(global_addr("pcc_gc_live_bytes"), 0, live + size)
        _backend4_zpage_track_alloc(o, size)
        _object_graph_unlock()
    _set_pending_minor_block(null())
    global_store_ptr("pcc_gc_last_alloc", o)


@c_abi_export("pcc_gc_note_object_allocated")
def pcc_gc_note_object_allocated(o) -> None:
    pcc_gc_note_object_allocated_sized(o, 16)


@c_abi_export("pcc_gc_note_object_freeing")
def pcc_gc_note_object_freeing(o) -> None:
    _init_config()
    if ptr_is_null(o) != 0:
        return
    _object_graph_lock()
    _forwarding_remove(o)
    _identity_remove(o)
    _relocation_set_remove(o)
    _backend4_store_buffer_remove(o)
    _backend4_remembered_set_remove(o)
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
        free(node)
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
    if tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags
        return 1
    return _relocate_copy_supported_tag(tag)


def _relocate_copy_payload(from_obj, to_obj, tag: int, size: int) -> int:
    if tag == 101:  # PY_TYPE_PROPERTY
        fget = load_ptr(from_obj, 16)
        fset = load_ptr(from_obj, 24)
        fdel = load_ptr(from_obj, 32)
        store_ptr(to_obj, 16, null())
        store_ptr(to_obj, 24, null())
        store_ptr(to_obj, 32, null())
        py_incref(fget)
        py_incref(fset)
        py_incref(fdel)
        store_ptr(to_obj, 16, fget)
        store_ptr(to_obj, 24, fset)
        store_ptr(to_obj, 32, fdel)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 24)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 32)
        return 1

    if tag == 102:  # PY_TYPE_CLASSMETHOD
        func = load_ptr(from_obj, 16)
        store_ptr(to_obj, 16, null())
        py_incref(func)
        store_ptr(to_obj, 16, func)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        return 1

    if tag == 103:  # PY_TYPE_STATICMETHOD
        func = load_ptr(from_obj, 16)
        store_ptr(to_obj, 16, null())
        py_incref(func)
        store_ptr(to_obj, 16, func)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        return 1

    if tag == 19:  # PY_TYPE_MEMORYVIEW
        base = load_ptr(from_obj, 16)
        store_ptr(to_obj, 16, null())
        py_incref(base)
        store_ptr(to_obj, 16, base)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        return 1

    if tag == 9:  # PY_TYPE_FUNC
        entry = load_ptr(from_obj, 16)
        captures = pcc_gc_load_ptr_extern(from_obj, ptr_add(from_obj, 24))
        name = load_ptr(from_obj, 32)
        self_obj = pcc_gc_load_ptr_extern(from_obj, ptr_add(from_obj, 40))
        store_ptr(to_obj, 16, entry)
        store_ptr(to_obj, 24, null())
        store_ptr(to_obj, 32, name)
        store_ptr(to_obj, 40, null())
        py_incref(captures)
        store_ptr(to_obj, 24, captures)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 24)
        if ptr_is_null(self_obj) == 0:
            py_incref(self_obj)
            store_ptr(to_obj, 40, self_obj)
            _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 40)
        return 1

    if tag == 14:  # PY_TYPE_ITER
        seq = load_ptr(from_obj, 16)
        index: int = load_i64(from_obj, 24)
        store_ptr(to_obj, 16, null())
        store_i64(to_obj, 24, index)
        py_incref(seq)
        store_ptr(to_obj, 16, seq)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        return 1

    if tag == 15:  # PY_TYPE_GEN
        resume = load_ptr(from_obj, 16)
        frame = load_ptr(from_obj, 24)
        state: int = load_i64(from_obj, 32)
        done: int = load_i64(from_obj, 40)
        send_value = load_ptr(from_obj, 48)
        store_ptr(to_obj, 16, resume)
        store_ptr(to_obj, 24, null())
        store_i64(to_obj, 32, state)
        store_i64(to_obj, 40, done)
        store_ptr(to_obj, 48, null())
        py_incref(frame)
        py_incref(send_value)
        store_ptr(to_obj, 24, frame)
        store_ptr(to_obj, 48, send_value)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 24)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 48)
        return 1

    if tag == 20:  # PY_TYPE_COROUTINE
        name = load_ptr(from_obj, 16)
        entry = load_ptr(from_obj, 24)
        captures = load_ptr(from_obj, 32)
        args = load_ptr(from_obj, 40)
        result = load_ptr(from_obj, 48)
        closed: int = load_i32(from_obj, 56)
        done: int = load_i32(from_obj, 60)
        store_ptr(to_obj, 16, name)
        store_ptr(to_obj, 24, entry)
        store_ptr(to_obj, 32, null())
        store_ptr(to_obj, 40, null())
        store_ptr(to_obj, 48, null())
        store_i32(to_obj, 56, closed)
        store_i32(to_obj, 60, done)
        py_incref(captures)
        py_incref(args)
        py_incref(result)
        store_ptr(to_obj, 32, captures)
        store_ptr(to_obj, 40, args)
        store_ptr(to_obj, 48, result)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 32)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 40)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 48)
        return 1

    if tag == 29:  # PY_TYPE_CONTINUATION
        resume_pc = load_ptr(from_obj, 16)
        src_chunk = load_ptr(from_obj, 24)
        mounted: int = load_i64(from_obj, 32)
        resume_abi: int = load_i64(from_obj, 40)
        store_ptr(to_obj, 16, resume_pc)
        store_ptr(to_obj, 24, null())
        store_i64(to_obj, 32, mounted)
        store_i64(to_obj, 40, resume_abi)
        if ptr_is_null(src_chunk) != 0:
            return 1
        n_slots: int = load_i64(src_chunk, 8)
        if n_slots < 0 or n_slots > 1152921504606846975:
            return 0
        src_slots = load_ptr(src_chunk, 16)
        if n_slots > 0 and ptr_is_null(src_slots) != 0:
            return 0
        dst_chunk = malloc(24)
        if ptr_is_null(dst_chunk) != 0:
            return 0
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
                return 0
            memset(dst_slots, 0, n_slots * 8)
            store_ptr(dst_chunk, 16, dst_slots)
            i: int = 0
            while i < n_slots:
                value = load_ptr(src_slots, i * 8)
                py_incref(value)
                store_ptr(dst_slots, i * 8, value)
                _backend4_remembered_set_retarget_slot(
                    from_obj,
                    to_obj,
                    ptr_add(src_slots, i * 8),
                    ptr_add(dst_slots, i * 8),
                )
                i = i + 1
        store_ptr(to_obj, 24, dst_chunk)
        if mounted == 0:
            _retarget_continuation_root_slots(
                src_slots, src_chunk, dst_slots, dst_chunk
            )
        return 1

    if tag == 12:  # PY_TYPE_EXC
        exc_class = load_ptr(from_obj, 16)
        message = load_ptr(from_obj, 24)
        cause = load_ptr(from_obj, 32)
        context = load_ptr(from_obj, 40)
        traceback = load_ptr(from_obj, 48)
        n_frames: int = load_i32(from_obj, 56)
        cap_frames: int = load_i32(from_obj, 60)

        store_ptr(to_obj, 16, null())
        store_ptr(to_obj, 24, null())
        store_ptr(to_obj, 32, null())
        store_ptr(to_obj, 40, null())
        store_ptr(to_obj, 48, null())
        store_i32(to_obj, 56, 0)
        store_i32(to_obj, 60, 0)

        if n_frames < 0 or cap_frames < 0 or n_frames > cap_frames:
            return 0
        if cap_frames > 0 and ptr_is_null(traceback) != 0:
            return 0
        if cap_frames > 384307168202282325:
            return 0
        if cap_frames > 0:
            copied_traceback = malloc(cap_frames * 24)
            if ptr_is_null(copied_traceback) != 0:
                return 0
            memmove(copied_traceback, traceback, cap_frames * 24)
            store_ptr(to_obj, 48, copied_traceback)

        py_incref(exc_class)
        py_incref(message)
        py_incref(cause)
        py_incref(context)
        store_ptr(to_obj, 16, exc_class)
        store_ptr(to_obj, 24, message)
        store_ptr(to_obj, 32, cause)
        store_ptr(to_obj, 40, context)
        store_i32(to_obj, 56, n_frames)
        store_i32(to_obj, 60, cap_frames)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 24)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 32)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 40)
        return 1

    if tag == 10:  # PY_TYPE_CLASS
        name = load_ptr(from_obj, 16)
        n_bases: int = load_i32(from_obj, 24)
        bases = load_ptr(from_obj, 32)
        n_mro: int = load_i32(from_obj, 40)
        mro = load_ptr(from_obj, 48)
        n_methods: int = load_i32(from_obj, 56)
        methods = load_ptr(from_obj, 64)
        n_fields: int = load_i32(from_obj, 72)
        field_names = load_ptr(from_obj, 80)
        instance_size: int = load_i32(from_obj, 88)
        type_tag_alloc: int = load_i32(from_obj, 92)
        del_method = load_ptr(from_obj, 96)
        attrs = pcc_gc_load_ptr_extern(from_obj, ptr_add(from_obj, 104))
        metaclass = pcc_gc_load_ptr_extern(from_obj, ptr_add(from_obj, 112))

        store_ptr(to_obj, 16, name)
        store_i32(to_obj, 24, 0)
        store_ptr(to_obj, 32, null())
        store_i32(to_obj, 40, 0)
        store_ptr(to_obj, 48, null())
        store_i32(to_obj, 56, 0)
        store_ptr(to_obj, 64, null())
        store_i32(to_obj, 72, 0)
        store_ptr(to_obj, 80, null())
        store_i32(to_obj, 88, instance_size)
        store_i32(to_obj, 92, type_tag_alloc)
        store_ptr(to_obj, 96, del_method)
        store_ptr(to_obj, 104, null())
        store_ptr(to_obj, 112, metaclass)

        if n_bases < 0 or n_mro < 0 or n_methods < 0 or n_fields < 0:
            return 0
        if n_bases > 1152921504606846975 or n_mro > 1152921504606846975:
            return 0
        if n_methods > 576460752303423487 or n_fields > 1152921504606846975:
            return 0
        if n_bases > 0:
            if ptr_is_null(bases) != 0:
                return 0
            bases_copy = malloc(n_bases * 8)
            if ptr_is_null(bases_copy) != 0:
                return 0
            bi: int = 0
            while bi < n_bases:
                base = _note_relocation_read_unlocked(load_ptr(bases, bi * 8))
                store_ptr(bases_copy, bi * 8, base)
                bi = bi + 1
            store_ptr(to_obj, 32, bases_copy)
        if n_mro > 0:
            if ptr_is_null(mro) != 0:
                return 0
            mro_copy = malloc(n_mro * 8)
            if ptr_is_null(mro_copy) != 0:
                return 0
            mi: int = 0
            while mi < n_mro:
                entry = _note_relocation_read_unlocked(load_ptr(mro, mi * 8))
                if ptr_eq(entry, from_obj) != 0:
                    store_ptr(mro_copy, mi * 8, to_obj)
                else:
                    store_ptr(mro_copy, mi * 8, entry)
                mi = mi + 1
            store_ptr(to_obj, 48, mro_copy)
        if n_methods > 0:
            if ptr_is_null(methods) != 0:
                return 0
            methods_copy = malloc(n_methods * 16)
            if ptr_is_null(methods_copy) != 0:
                return 0
            mk: int = 0
            while mk < n_methods:
                method_off: int = mk * 16
                store_ptr(methods_copy, method_off, load_ptr(methods, method_off))
                method_func = load_ptr(methods, method_off + 8)
                store_ptr(methods_copy, method_off + 8, method_func)
                mk = mk + 1
            store_ptr(to_obj, 64, methods_copy)
        if n_fields > 0:
            if ptr_is_null(field_names) != 0:
                return 0
            field_names_copy = malloc(n_fields * 8)
            if ptr_is_null(field_names_copy) != 0:
                return 0
            memmove(field_names_copy, field_names, n_fields * 8)
            store_ptr(to_obj, 80, field_names_copy)
        py_incref(attrs)
        store_i32(to_obj, 24, n_bases)
        store_i32(to_obj, 40, n_mro)
        store_i32(to_obj, 56, n_methods)
        store_i32(to_obj, 72, n_fields)
        store_ptr(to_obj, 104, attrs)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 104)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 112)
        return 1

    if tag == 21:  # PY_TYPE_WEAKREF
        target = load_ptr(from_obj, 16)
        callback = load_ptr(from_obj, 24)
        prev = load_ptr(from_obj, 32)
        nxt = load_ptr(from_obj, 40)
        store_ptr(to_obj, 16, target)
        store_ptr(to_obj, 24, null())
        store_ptr(to_obj, 32, prev)
        store_ptr(to_obj, 40, nxt)
        if ptr_is_null(callback) == 0:
            py_incref(callback)
        store_ptr(to_obj, 24, callback)
        if ptr_is_null(prev) != 0:
            global_store_ptr("py_weakref_head", to_obj)
        else:
            store_ptr(prev, 40, to_obj)
        if ptr_is_null(nxt) == 0:
            store_ptr(nxt, 32, to_obj)
        store_ptr(from_obj, 32, from_obj)
        store_ptr(from_obj, 40, null())
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 24)
        return 1

    if tag == 27:  # PY_TYPE_THREAD
        handle = load_ptr(from_obj, 16)
        callable_obj = load_ptr(from_obj, 24)
        args = load_ptr(from_obj, 32)
        result = load_ptr(from_obj, 40)
        started: int = load_i64(from_obj, 48)
        joined: int = load_i64(from_obj, 56)
        finished: int = load_i64(from_obj, 64)
        if ptr_is_null(handle) == 0:
            return 0
        store_ptr(to_obj, 16, null())
        store_ptr(to_obj, 24, null())
        store_ptr(to_obj, 32, null())
        store_ptr(to_obj, 40, null())
        store_i64(to_obj, 48, started)
        store_i64(to_obj, 56, joined)
        store_i64(to_obj, 64, finished)
        if ptr_is_null(callable_obj) == 0:
            py_incref(callable_obj)
        if ptr_is_null(args) == 0:
            py_incref(args)
        if ptr_is_null(result) == 0:
            py_incref(result)
        store_ptr(to_obj, 24, callable_obj)
        store_ptr(to_obj, 32, args)
        store_ptr(to_obj, 40, result)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 24)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 32)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 40)
        return 1

    if tag == 28:  # PY_TYPE_TASK
        coro = load_ptr(from_obj, 16)
        result = load_ptr(from_obj, 24)
        waiter = load_ptr(from_obj, 32)
        done: int = load_i64(from_obj, 40)

        store_ptr(to_obj, 16, null())
        store_ptr(to_obj, 24, null())
        store_ptr(to_obj, 32, null())
        store_i64(to_obj, 40, done)

        py_incref(coro)
        py_incref(result)
        py_incref(waiter)
        store_ptr(to_obj, 16, coro)
        store_ptr(to_obj, 24, result)
        store_ptr(to_obj, 32, waiter)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 24)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 32)
        return 1

    if tag == 30:  # PY_TYPE_VIRTUAL_THREAD
        continuation = load_ptr(from_obj, 16)
        result = load_ptr(from_obj, 24)
        state: int = load_i64(from_obj, 32)
        queued: int = load_i64(from_obj, 40)
        pinned: int = load_i64(from_obj, 48)
        if queued != 0:
            return 0
        store_ptr(to_obj, 16, null())
        store_ptr(to_obj, 24, null())
        store_i64(to_obj, 32, state)
        store_i64(to_obj, 40, 0)
        store_i64(to_obj, 48, pinned)
        py_incref(continuation)
        py_incref(result)
        store_ptr(to_obj, 16, continuation)
        store_ptr(to_obj, 24, result)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 24)
        return 1

    if tag == 11 or tag >= 104:  # PY_TYPE_INSTANCE / user instance tags
        cls = load_ptr(from_obj, 16)
        store_ptr(to_obj, 16, null())

        if size < 24:
            return 0
        if ptr_is_null(cls) != 0:
            return 0
        if load_i32(cls, 8) != 10:  # PY_TYPE_CLASS
            return 0

        n_fields: int = load_i32(cls, 72)
        if n_fields < 0:
            n_fields = 0
        n_slots: int = n_fields
        class_flags: int = load_i32(cls, 12)
        if (class_flags & 2) == 0:
            n_slots = n_slots + 1
        if n_slots < 0:
            return 0
        if size < 24 + n_slots * 8:
            return 0

        i: int = 0
        while i < n_slots:
            offset: int = 24 + i * 8
            child = load_ptr(from_obj, offset)
            py_incref(child)
            store_ptr(to_obj, offset, child)
            _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, offset)
            i = i + 1
        store_ptr(to_obj, 16, cls)
        _backend4_remembered_set_retarget_inline_slot(from_obj, to_obj, 16)
        return 1

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
            return 0
        if entries_used > capacity or dict_size > entries_used:
            return 0
        if capacity == 0:
            return 1
        if ptr_is_null(src_indices) != 0:
            return 0
        if ptr_is_null(src_entries) != 0:
            return 0
        if capacity > 384307168202282325:
            return 0

        indices = malloc(capacity * 8)
        if ptr_is_null(indices) != 0:
            return 0
        entries = malloc(capacity * 24)
        if ptr_is_null(entries) != 0:
            free(indices)
            return 0
        memset(entries, 0, capacity * 24)

        i: int = 0
        while i < capacity:
            store_i64(indices, i * 8, load_i64(src_indices, i * 8))
            i = i + 1

        j: int = 0
        while j < entries_used:
            offset: int = j * 24
            key = load_ptr(src_entries, offset + 8)
            value = load_ptr(src_entries, offset + 16)
            store_i64(entries, offset, load_i64(src_entries, offset))
            store_ptr(entries, offset + 8, key)
            store_ptr(entries, offset + 16, value)
            if ptr_is_null(key) == 0:
                py_incref(key)
                py_incref(value)
            _backend4_remembered_set_retarget_slot(
                from_obj,
                to_obj,
                ptr_add(src_entries, offset + 8),
                ptr_add(entries, offset + 8),
            )
            _backend4_remembered_set_retarget_slot(
                from_obj,
                to_obj,
                ptr_add(src_entries, offset + 16),
                ptr_add(entries, offset + 16),
            )
            j = j + 1

        store_ptr(to_obj, 32, indices)
        store_ptr(to_obj, 40, entries)
        store_i64(to_obj, 24, capacity)
        store_i64(to_obj, 16, dict_size)
        store_i64(to_obj, 48, entries_used)
        return 1

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
            return 0
        if capacity == 0:
            return 1
        if ptr_is_null(src_entries) != 0:
            return 0
        if capacity > 576460752303423487:
            return 0

        entries = malloc(capacity * 16)
        if ptr_is_null(entries) != 0:
            return 0
        memset(entries, 0, capacity * 16)

        dummy = global_load_ptr("py_set_dummy")
        i: int = 0
        while i < capacity:
            offset: int = i * 16
            key = load_ptr(src_entries, offset + 8)
            store_i64(entries, offset, load_i64(src_entries, offset))
            store_ptr(entries, offset + 8, key)
            if ptr_is_null(key) == 0:
                if ptr_eq(key, dummy) == 0:
                    py_incref(key)
            _backend4_remembered_set_retarget_slot(
                from_obj,
                to_obj,
                ptr_add(src_entries, offset + 8),
                ptr_add(entries, offset + 8),
            )
            i = i + 1

        store_ptr(to_obj, 40, entries)
        store_i64(to_obj, 16, set_size)
        store_i64(to_obj, 24, capacity)
        store_i64(to_obj, 32, fill)
        return 1

    if tag == 7:  # PY_TYPE_TUPLE
        length: int = load_i64(from_obj, 16)
        store_i64(to_obj, 16, 0)
        if length < 0:
            return 0
        if size < 24 + length * 8:
            return 0
        i: int = 0
        while i < length:
            py_incref(load_ptr(to_obj, 24 + i * 8))
            _backend4_remembered_set_retarget_slot(
                from_obj,
                to_obj,
                ptr_add(from_obj, 24 + i * 8),
                ptr_add(to_obj, 24 + i * 8),
            )
            i = i + 1
        store_i64(to_obj, 16, length)
        return 1

    if tag == 5:  # PY_TYPE_LIST
        length: int = load_i64(from_obj, 16)
        capacity: int = load_i64(from_obj, 24)
        src_items = load_ptr(from_obj, 32)

        store_i64(to_obj, 16, 0)
        store_i64(to_obj, 24, 0)
        store_ptr(to_obj, 32, null())

        if length < 0 or capacity < length:
            return 0
        if capacity == 0:
            return 1
        if ptr_is_null(src_items) != 0:
            return 0

        items = malloc(capacity * 8)
        if ptr_is_null(items) != 0:
            return 0
        memset(items, 0, capacity * 8)

        i = 0
        while i < length:
            child = load_ptr(src_items, i * 8)
            store_ptr(items, i * 8, child)
            py_incref(child)
            i = i + 1

        store_ptr(to_obj, 32, items)
        store_i64(to_obj, 16, length)
        store_i64(to_obj, 24, capacity)
        j = 0
        while j < capacity:
            _backend4_remembered_set_retarget_slot(
                from_obj,
                to_obj,
                ptr_add(src_items, j * 8),
                ptr_add(items, j * 8),
            )
            j = j + 1
        return 1

    return 1


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
        node = _zpage_head()
        while ptr_is_null(node) == 0:
            if ptr_eq(load_ptr(node, 0), obj) != 0:
                if ptr_eq(load_ptr(node, 8), page) != 0:
                    return 1
            node = load_ptr(node, 16)
        rel = load_ptr(rel, 8)
    return 0


def _backend4_zpage_page_for_owner(owner):
    if ptr_is_null(owner) != 0:
        return null()
    node = _zpage_head()
    while ptr_is_null(node) == 0:
        if ptr_eq(load_ptr(node, 0), owner) != 0:
            return load_ptr(node, 8)
        node = load_ptr(node, 16)
    return null()


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
    _init_config()
    if pcc_gc_backend() != 4:
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
    memmove(to_obj, from_obj, size)
    store_i64(to_obj, 0, 1)
    new_flags: int = load_i32(to_obj, 12)
    store_i32(to_obj, 12, new_flags & ~10240)
    if _relocate_copy_payload(from_obj, to_obj, tag, size) == 0:
        py_decref(to_obj)
        return null()
    if _install_forwarding_unlocked(from_obj, to_obj) != 0:
        py_decref(to_obj)
        return null()
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
    _init_config()
    if pcc_gc_backend() != 4:
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
            py_incref(to_obj)
            store_ptr(node, 8, to_obj)
            py_decref(old_target)
    else:
        node = malloc(24)
        if ptr_is_null(node) != 0:
            return -1
        py_incref(to_obj)
        store_ptr(node, 0, from_obj)
        store_ptr(node, 8, to_obj)
        store_ptr(node, 16, _forwarding_head())
        _set_forwarding_head(node)
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
    _counter_inc(2, 1)


def _note_relocation_read_unlocked(o):
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return o
    flags: int = load_i32(o, 12)
    store_i32(o, 12, flags & ~2048)
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
    return o


@c_abi_export("pcc_gc_note_relocation_read")
def pcc_gc_note_relocation_read(o):
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return o
    _object_graph_lock()
    resolved = _note_relocation_read_unlocked(o)
    _object_graph_unlock()
    return resolved


@c_abi_export("pcc_gc_note_store")
def pcc_gc_note_store() -> None:
    _counter_inc(1, 1)


@c_abi_export("pcc_gc_note_slot_write_barrier")
def pcc_gc_note_slot_write_barrier(owner, slot, value) -> None:
    if ptr_is_null(value) != 0:
        return
    if is_tagged_int(value) != 0:
        return
    backend: int = pcc_gc_backend()
    barrier_backend: int = backend
    value_flags: int = load_i32(value, 12)
    if ptr_is_null(owner) != 0:
        if barrier_backend == 1 or barrier_backend == 2 or barrier_backend == 4:
            if _is_known_object(value) == 0:
                return
            if load_i32(global_addr("pcc_gc_mark_active"), 0) == 0:
                return
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
    owner_flags: int = load_i32(owner, 12)
    if barrier_backend == 1:
        if _is_known_object(owner) == 0 or _is_known_object(value) == 0:
            return
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
        should_gray_value: bool = (value_flags & 8) != 0
        if barrier_backend == 2:
            should_gray_value = (value_flags & 16) == 0
        if should_gray_value:
            store_i32(value, 12, (value_flags & ~56) | 16)
            store_i32(global_addr("pcc_gc_mark_active"), 0, 1)
            flushes: int = load_i32(global_addr("pcc_gc_cms_wb_flushes"), 0)
            store_i32(global_addr("pcc_gc_cms_wb_flushes"), 0, flushes + 1)
    elif backend == 3 or backend == 4:
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
                    store_i32(owner, 12, owner_flags | 512)
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


@c_abi_export("pcc_gc_scheduler_root_register")
def pcc_gc_scheduler_root_register(slot) -> None:
    _init_config()
    if ptr_is_null(slot) != 0:
        return
    node = malloc(16)
    if ptr_is_null(node) != 0:
        return
    store_ptr(node, 0, slot)
    _object_graph_lock()
    store_ptr(node, 8, global_load_ptr("pcc_gc_scheduler_root_head"))
    global_store_ptr("pcc_gc_scheduler_root_head", node)
    _object_graph_unlock()
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
            if ptr_is_null(prev) != 0:
                global_store_ptr("pcc_gc_scheduler_root_head", nxt)
            else:
                store_ptr(prev, 8, nxt)
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
        count += _mapped_root_count(load_ptr(node, 0))
        node = load_ptr(node, 16)
    return count


@c_abi_export("pcc_gc_continuation_root_slot_count")
def pcc_gc_continuation_root_slot_count() -> int:
    _init_config()
    node = global_load_ptr("pcc_gc_continuation_root_head")
    count: int = 0
    while ptr_is_null(node) == 0:
        count += _mapped_root_count(load_ptr(node, 0))
        node = load_ptr(node, 16)
    return count


@c_abi_export("pcc_gc_coroutine_root_score")
def pcc_gc_coroutine_root_score() -> int:
    return (
        pcc_gc_scheduler_root_count()
        + pcc_gc_frame_root_slot_count()
        + pcc_gc_continuation_root_slot_count()
    )


@c_abi_export("pcc_gc_register_continuation_root")
def pcc_gc_register_continuation_root(frame_map, slots) -> None:
    _init_config()
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return
    if _mapped_root_count(frame_map) <= 0:
        return
    node = malloc(24)
    if ptr_is_null(node) != 0:
        return
    store_ptr(node, 0, frame_map)
    store_ptr(node, 8, slots)
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
    pcc_gc_load_ptr_extern(null(), entry)
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
    pcc_gc_scheduler_root_unregister(entry)
    _object_graph_unlock()
    pcc_gc_store_root_extern(entry, null())
    free(entry)


@c_abi_export("pcc_gc_scheduler_queue_new")
def pcc_gc_scheduler_queue_new():
    queue = malloc(32)
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
    if ptr_is_null(mutex) == 0:
        pcc_mutex_free(mutex)
    free(queue)


@c_abi_export("pcc_gc_scheduler_queue_push")
def pcc_gc_scheduler_queue_push(queue, value) -> int:
    if ptr_is_null(queue):
        return -1
    entry = malloc(16)
    if ptr_is_null(entry):
        return -1
    store_ptr(entry, 0, null())
    store_ptr(entry, 8, null())
    _object_graph_lock()
    pcc_gc_scheduler_root_register(entry)
    pcc_gc_store_root_extern(entry, value)
    _object_graph_unlock()
    mutex = load_ptr(queue, 0)
    if pcc_mutex_lock(mutex) != 0:
        _scheduler_queue_entry_free(entry)
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
    value = pcc_gc_load_ptr_extern(null(), entry)
    if ptr_is_null(out_slot) == 0:
        pcc_gc_store_root_extern(out_slot, value)
    pcc_gc_scheduler_root_unregister(entry)
    _object_graph_unlock()
    pcc_gc_store_root_extern(entry, null())
    free(entry)
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
    _init_config()
    if _should_track_frame_roots() == 0:
        return
    if ptr_is_null(frame_map) != 0 or ptr_is_null(slots) != 0:
        return
    n_slots: int = load_i32(frame_map, 0)
    if n_slots < 0 or n_slots > 100000:
        return
    node = malloc(24)
    if ptr_is_null(node) != 0:
        return
    store_ptr(node, 0, frame_map)
    store_ptr(node, 8, slots)
    _object_graph_lock()
    store_ptr(node, 16, global_load_ptr("pcc_gc_frame_head"))
    global_store_ptr("pcc_gc_frame_head", node)
    store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
    _object_graph_unlock()


@c_abi_export("pcc_gc_note_frame_leave")
def pcc_gc_note_frame_leave(slots) -> None:
    _init_config()
    if _should_track_frame_roots() == 0:
        return
    if ptr_is_null(slots) != 0:
        return
    _object_graph_lock()
    if pcc_gc_backend() == 0:
        if ptr_is_null(global_load_ptr("pcc_gc_frame_head")) != 0:
            _object_graph_unlock()
            return
    prev = null()
    node = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if ptr_eq(load_ptr(node, 8), slots) != 0:
            if ptr_is_null(prev) != 0:
                global_store_ptr("pcc_gc_frame_head", nxt)
            else:
                store_ptr(prev, 16, nxt)
            store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
            _object_graph_unlock()
            return
        prev = node
        node = nxt
    root_slots = global_load_ptr("pcc_gc_root_slots")
    if ptr_eq(root_slots, slots) != 0:
        global_store_ptr("pcc_gc_root_slots", null())
        store_i32(global_addr("pcc_gc_root_count"), 0, 0)
        store_i32(global_addr("pcc_gc_cycle_requested"), 0, 1)
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
