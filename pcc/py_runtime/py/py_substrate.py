"""pcc-Python substrate replacement.

This module defines the stable runtime storage symbols that used to live
in py_substrate.c, plus the small C ABI helper functions retained for
older runtime call sites.  The top-level define_global_* calls are
compile-time pcc.unsafe intrinsics: they create data symbols in the
object file and do not depend on the stripped synthetic main().
"""
from pcc.extern import c_abi_export
from pcc.unsafe import (
    access,
    define_global_cstr,
    define_global_header,
    define_global_i8,
    define_global_i32,
    define_global_i32_array,
    define_global_null_ptr_array,
    define_global_ptr_array,
    define_global_ptr_null,
    define_global_ptr_to_global,
    free,
    getenv,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memcpy,
    memmove,
    memset,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    realloc,
    setenv,
    store_i32,
    store_i8,
    store_i64,
    store_ptr,
    strlen,
    unsetenv,
    write,
)


define_global_header("py_none_storage", 1, 0, 1)
define_global_header("py_notimplemented_storage", 1, 0, 1)
define_global_header("py_true_storage", 1, 1, 1)
define_global_header("py_false_storage", 1, 1, 1)
define_global_ptr_to_global("py_None", "py_none_storage")
define_global_ptr_to_global("py_NotImplemented", "py_notimplemented_storage")
define_global_ptr_to_global("py_True", "py_true_storage")
define_global_ptr_to_global("py_False", "py_false_storage")

define_global_cstr("PY_EXC_NAME_0", "BaseException")
define_global_cstr("PY_EXC_NAME_1", "Exception")
define_global_cstr("PY_EXC_NAME_2", "ValueError")
define_global_cstr("PY_EXC_NAME_3", "TypeError")
define_global_cstr("PY_EXC_NAME_4", "KeyError")
define_global_cstr("PY_EXC_NAME_5", "IndexError")
define_global_cstr("PY_EXC_NAME_6", "AttributeError")
define_global_cstr("PY_EXC_NAME_7", "RuntimeError")
define_global_cstr("PY_EXC_NAME_8", "StopIteration")
define_global_cstr("PY_EXC_NAME_9", "ZeroDivisionError")
define_global_cstr("PY_EXC_NAME_10", "NameError")
define_global_cstr("PY_EXC_NAME_11", "NotImplementedError")
define_global_cstr("PY_EXC_NAME_12", "ArithmeticError")
define_global_cstr("PY_EXC_NAME_13", "LookupError")
define_global_cstr("PY_EXC_NAME_14", "OSError")
define_global_cstr("PY_EXC_NAME_15", "OverflowError")
define_global_cstr("PY_EXC_NAME_16", "AssertionError")
define_global_cstr("PY_EXC_NAME_17", "StopAsyncIteration")
define_global_cstr("PY_EXC_NAME_18", "ReferenceError")
define_global_ptr_array(
    "PY_EXC_BUILTIN_NAMES",
    "PY_EXC_NAME_0",
    "PY_EXC_NAME_1",
    "PY_EXC_NAME_2",
    "PY_EXC_NAME_3",
    "PY_EXC_NAME_4",
    "PY_EXC_NAME_5",
    "PY_EXC_NAME_6",
    "PY_EXC_NAME_7",
    "PY_EXC_NAME_8",
    "PY_EXC_NAME_9",
    "PY_EXC_NAME_10",
    "PY_EXC_NAME_11",
    "PY_EXC_NAME_12",
    "PY_EXC_NAME_13",
    "PY_EXC_NAME_14",
    "PY_EXC_NAME_15",
    "PY_EXC_NAME_16",
    "PY_EXC_NAME_17",
    "PY_EXC_NAME_18",
)
define_global_i32_array(
    "PY_EXC_PARENT",
    -1,
    0,
    1,
    1,
    13,
    13,
    1,
    1,
    1,
    12,
    1,
    7,
    1,
    1,
    1,
    12,
    1,
    1,
    1,
)
define_global_null_ptr_array("py_exc_classes", 19)

define_global_i8("py_set_dummy_storage", 0)
define_global_ptr_to_global("py_set_dummy", "py_set_dummy_storage")
define_global_i32("py_next_user_tag", 104)
define_global_i32("py_gc_enabled", 1)
define_global_i32("py_gc_threshold0", 700)
define_global_i32("py_gc_threshold1", 10)
define_global_i32("py_gc_threshold2", 10)
define_global_i32("py_gc_freeze_count", 0)
define_global_ptr_null("py_gc_head")
define_global_i32("py_gc_tracked_count", 0)
define_global_i32("py_gc_collecting", 0)
define_global_ptr_null("py_gc_callbacks")
define_global_i32("py_gc_callbacks_firing", 0)
define_global_ptr_null("py_weakref_head")
define_global_ptr_null("py_object_root_cache")
define_global_ptr_null("py_tls_current_exc_storage")
define_global_i32("pcc_gc_backend_selected", 0)
define_global_i32("pcc_gc_metric_alloc", 0)
define_global_i32("pcc_gc_metric_store", 0)
define_global_i32("pcc_gc_metric_load", 0)
define_global_i32("pcc_gc_metric_safepoint", 0)
define_global_i32("pcc_gc_metric_pin", 0)
define_global_i32("pcc_gc_metric_step", 0)
define_global_i32("pcc_gc_metric_max_pause_us", 0)
define_global_i32("pcc_gc_debt_bytes", 0)
define_global_i32("pcc_gc_last_alloc_bytes", 0)
define_global_i32("pcc_gc_live_bytes", 0)
define_global_i32("pcc_gc_pause", 200)
define_global_i32("pcc_gc_stepmul", 200)
define_global_i32("pcc_gc_debt_threshold_override", 0)
define_global_i32("pcc_gc_config_initialized", 0)
define_global_i32("pcc_gc_backend0_frame_roots_enabled", 0)
define_global_i32("pcc_gc_in_auto_step", 0)
define_global_i32("pcc_gc_explicit_collect_active", 0)
define_global_i32("pcc_gc_minor_heap_size", 1048576)
define_global_i32("pcc_gc_minor_alloc_max", 256)
define_global_i32("pcc_gc_minor_allocations", 0)
define_global_i32("pcc_gc_minor_collections", 0)
define_global_i32("pcc_gc_minor_bytes", 0)
define_global_i32("pcc_gc_cms_worker_started", 0)
define_global_i32("pcc_gc_cms_worker_starts", 0)
define_global_i32("pcc_gc_cms_queue_pushes", 0)
define_global_i32("pcc_gc_cms_worker_drains", 0)
define_global_i32("pcc_gc_cms_mutator_assists", 0)
define_global_i32("pcc_gc_cms_worker_traces", 0)
define_global_i32("pcc_gc_minor_arena_refills", 0)
define_global_i32("pcc_gc_minor_arena_bumps", 0)
define_global_i32("pcc_gc_minor_arena_fallbacks", 0)
define_global_i32("pcc_gc_cms_worker_stops", 0)
define_global_i32("pcc_gc_cms_wb_flushes", 0)
define_global_ptr_null("pcc_gc_minor_blocks")
define_global_ptr_null("pcc_gc_minor_current")
define_global_ptr_null("pcc_gc_pending_minor_block")
define_global_i32("pcc_gc_relocation_forwards", 0)
define_global_i32("pcc_gc_relocation_barrier_forwards", 0)
define_global_i32("pcc_gc_relocation_pin_rejects", 0)
define_global_i32("pcc_gc_backend4_genzgc_store_barriers", 0)
define_global_i32("pcc_gc_backend4_store_buffer_entries_count", 0)
define_global_i32("pcc_gc_backend4_young_promotions", 0)
define_global_i32("pcc_gc_backend4_evacuation_candidates", 0)
define_global_i32("pcc_gc_backend4_evacuated_bytes_count", 0)
define_global_i32("pcc_gc_backend4_large_object_defers", 0)
define_global_i32("pcc_gc_backend4_large_object_deferred_bytes_count", 0)
define_global_i32("pcc_gc_backend4_large_object_reconsiderations_count", 0)
define_global_i32("pcc_gc_backend4_small_page_candidates", 0)
define_global_i32("pcc_gc_backend4_medium_page_candidates", 0)
define_global_i32("pcc_gc_backend4_evacuation_candidate_bytes_count", 0)
define_global_i32("pcc_gc_backend4_small_page_candidate_bytes_count", 0)
define_global_i32("pcc_gc_backend4_medium_page_candidate_bytes_count", 0)
define_global_i32("pcc_gc_backend4_evacuation_candidate_zpage_bytes_count", 0)
define_global_i32("pcc_gc_backend4_small_page_candidate_zpage_bytes_count", 0)
define_global_i32("pcc_gc_backend4_medium_page_candidate_zpage_bytes_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_drain_batches_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_drained_entries_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_duplicate_skips_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_high_water_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_owner_fanout_high_water_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_owner_count_high_water_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_incomplete_drains_count", 0)
define_global_i32("pcc_gc_backend4_evacuation_incomplete_batches_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_max_batch_size_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_full_batches_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_medium_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_medium_flushes_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_medium_flushed_entries_count", 0)
define_global_i32("pcc_gc_backend4_store_buffer_medium_full_flushes_count", 0)
define_global_i32("pcc_gc_backend4_remembered_set_entries_count", 0)
define_global_i32("pcc_gc_backend4_remembered_set_duplicate_skips_count", 0)
define_global_i32("pcc_gc_backend4_remembered_set_high_water_count", 0)
define_global_i32("pcc_gc_next_object_id", 1)
define_global_ptr_null("pcc_gc_last_alloc")
define_global_ptr_null("pcc_gc_forwarding_head")
define_global_ptr_null("pcc_gc_identity_head")
define_global_ptr_null("pcc_gc_relocation_set_head")
define_global_ptr_null("pcc_gc_backend4_store_buffer_head")
define_global_ptr_null("pcc_gc_backend4_store_buffer_medium_head")
define_global_ptr_null("pcc_gc_backend4_zpage_head")
define_global_ptr_null("pcc_gc_backend4_zpage_payload_span_head")
define_global_ptr_null("pcc_gc_backend4_page_head")
define_global_ptr_null("pcc_gc_backend4_free_page_head")
define_global_ptr_null("pcc_gc_backend4_evacuation_page_head")
define_global_ptr_null("pcc_gc_backend4_remembered_slots_head")
define_global_i32("pcc_gc_mark_active", 0)
define_global_i32("pcc_gc_cycle_requested", 0)
define_global_i32("pcc_gc_root_count", 0)
define_global_ptr_null("pcc_gc_root_slots")
define_global_ptr_null("pcc_gc_frame_head")
define_global_ptr_null("pcc_gc_continuation_root_head")
define_global_ptr_null("pcc_gc_scheduler_root_head")
define_global_ptr_null("pcc_gc_object_head")


@c_abi_export("py_mem_alloc")
def py_mem_alloc(bytes: int):
    return malloc(bytes)


@c_abi_export("py_mem_free")
def py_mem_free(p) -> None:
    free(p)


@c_abi_export("py_mem_zero")
def py_mem_zero(p, bytes: int):
    if ptr_is_null(p) == 0:
        memset(p, 0, bytes)
    return p


@c_abi_export("py_mem_copy")
def py_mem_copy(dst, src, bytes: int):
    if ptr_is_null(dst) == 0 and ptr_is_null(src) == 0:
        memmove(dst, src, bytes)
    return dst


@c_abi_export("py_mem_load_i64")
def py_mem_load_i64(p, offset: int) -> int:
    return load_i64(p, offset)


@c_abi_export("py_mem_load_i32")
def py_mem_load_i32(p, offset: int) -> int:
    return load_i32(p, offset)


@c_abi_export("py_mem_load_i8")
def py_mem_load_i8(p, offset: int) -> int:
    return load_i8(p, offset)


@c_abi_export("py_mem_load_ptr")
def py_mem_load_ptr(p, offset: int):
    return load_ptr(p, offset)


@c_abi_export("py_mem_store_i64")
def py_mem_store_i64(p, offset: int, v: int) -> None:
    store_i64(p, offset, v)


@c_abi_export("py_mem_store_i32")
def py_mem_store_i32(p, offset: int, v: int) -> None:
    store_i32(p, offset, v)


@c_abi_export("py_mem_store_i8")
def py_mem_store_i8(p, offset: int, v: int) -> None:
    store_i8(p, offset, v)


@c_abi_export("py_mem_store_ptr")
def py_mem_store_ptr(p, offset: int, v) -> None:
    store_ptr(p, offset, v)


@c_abi_export("py_mem_ptr_add")
def py_mem_ptr_add(p, offset: int):
    return ptr_add(p, offset)


@c_abi_export("py_mem_ptr_is_tagged_int")
def py_mem_ptr_is_tagged_int(p) -> int:
    if is_tagged_int(p):
        return 1
    return 0


@c_abi_export("py_mem_null_ptr")
def py_mem_null_ptr():
    return null()


@c_abi_export("py_tls_exc_get")
def py_tls_exc_get():
    return global_load_ptr("py_tls_current_exc_storage")


@c_abi_export("py_tls_exc_set")
def py_tls_exc_set(exc) -> None:
    global_store_ptr("py_tls_current_exc_storage", exc)


@c_abi_export("py_subs_none")
def py_subs_none():
    return global_load_ptr("py_None")


@c_abi_export("py_subs_true")
def py_subs_true():
    return global_load_ptr("py_True")


@c_abi_export("py_subs_false")
def py_subs_false():
    return global_load_ptr("py_False")


@c_abi_export("py_subs_exc_name")
def py_subs_exc_name(tag: int):
    if tag < 0 or tag >= 17:
        return null()
    return load_ptr(global_addr("PY_EXC_BUILTIN_NAMES"), tag * 8)


@c_abi_export("py_subs_exc_parent")
def py_subs_exc_parent(tag: int) -> int:
    if tag < 0 or tag >= 17:
        return -1
    return load_i32(global_addr("PY_EXC_PARENT"), tag * 4)


@c_abi_export("py_subs_exc_n_builtin")
def py_subs_exc_n_builtin() -> int:
    return 17


@c_abi_export("py_subs_exc_cache_get")
def py_subs_exc_cache_get(tag: int):
    if tag < 0 or tag >= 17:
        return null()
    return load_ptr(global_addr("py_exc_classes"), tag * 8)


@c_abi_export("py_subs_exc_cache_set")
def py_subs_exc_cache_set(tag: int, cls) -> None:
    if tag < 0 or tag >= 17:
        return
    store_ptr(global_addr("py_exc_classes"), tag * 8, cls)


@c_abi_export("py_subs_set_dummy")
def py_subs_set_dummy():
    return global_load_ptr("py_set_dummy")


@c_abi_export("py_mem_ptr_eq")
def py_mem_ptr_eq(a, b) -> int:
    if ptr_eq(a, b):
        return 1
    return 0


@c_abi_export("py_mem_ptr_is_null")
def py_mem_ptr_is_null(p) -> int:
    if ptr_is_null(p):
        return 1
    return 0


@c_abi_export("py_subs_getenv")
def py_subs_getenv(name):
    if ptr_is_null(name):
        return null()
    return getenv(name)


@c_abi_export("py_subs_setenv")
def py_subs_setenv(name, value) -> int:
    if ptr_is_null(name) or ptr_is_null(value):
        return -1
    return setenv(name, value, 1)


@c_abi_export("py_subs_unsetenv")
def py_subs_unsetenv(name) -> int:
    if ptr_is_null(name):
        return -1
    return unsetenv(name)


@c_abi_export("py_subs_path_exists")
def py_subs_path_exists(path) -> int:
    if ptr_is_null(path):
        return 0
    if access(path, 0) == 0:
        return 1
    return 0


@c_abi_export("py_subs_cstr_len")
def py_subs_cstr_len(s) -> int:
    if ptr_is_null(s):
        return 0
    return strlen(s)


@c_abi_export("py_subs_cstr_at")
def py_subs_cstr_at(s, i: int) -> int:
    if ptr_is_null(s):
        return 0
    return load_i8(s, i)


@c_abi_export("py_subs_realloc")
def py_subs_realloc(p, bytes: int):
    return realloc(p, bytes)


@c_abi_export("py_subs_write_fd")
def py_subs_write_fd(fd: int, buf, n: int) -> int:
    if ptr_is_null(buf) or n <= 0:
        return 0
    wrote: int = write(fd, buf, n)
    if wrote > 0:
        return wrote
    return 0


@c_abi_export("py_subs_strcmp")
def py_subs_strcmp(a, b) -> int:
    if ptr_is_null(a) or ptr_is_null(b):
        return -1
    i: int = 0
    while True:
        ca: int = load_i8(a, i) & 255
        cb: int = load_i8(b, i) & 255
        if ca != cb:
            return ca - cb
        if ca == 0:
            return 0
        i = i + 1


@c_abi_export("py_subs_alloc_user_tag")
def py_subs_alloc_user_tag() -> int:
    slot = global_addr("py_next_user_tag")
    tag: int = load_i32(slot, 0)
    store_i32(slot, 0, tag + 1)
    return tag


@c_abi_export("py_subs_object_root")
def py_subs_object_root():
    root = global_load_ptr("py_object_root_cache")
    if ptr_is_null(root) == 0:
        return root

    r = malloc(96)
    if ptr_is_null(r):
        return null()
    memset(r, 0, 96)

    store_i64(r, 0, 1)
    store_i32(r, 8, 10)
    store_i32(r, 12, 1)
    store_ptr(r, 16, global_addr("PY_OBJECT_NAME"))
    store_i32(r, 24, 0)
    store_ptr(r, 32, null())
    store_i32(r, 40, 1)

    mro = malloc(8)
    if ptr_is_null(mro):
        free(r)
        return null()
    store_ptr(mro, 0, r)
    store_ptr(r, 48, mro)

    store_i32(r, 56, 0)
    store_ptr(r, 64, null())
    store_i32(r, 72, 0)
    store_ptr(r, 80, null())
    store_i32(r, 88, 24)
    store_i32(r, 92, 11)

    global_store_ptr("py_object_root_cache", r)
    return r


define_global_cstr("PY_OBJECT_NAME", "object")
