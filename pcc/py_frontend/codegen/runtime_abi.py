"""Runtime-lib ABI declarations for pcc_py codegen.

Mirrors the C signatures in ``pcc/py_runtime/include/py_runtime.h``
(Section 3 of docs/plans/python-frontend-interfaces.md). Every function
declared here is a placeholder ``llvmlite.ir.Function`` with external
linkage — the definition lives in ``py_runtime.a`` and is linked in at
exe-build time.

Usage::

    from pcc.py_frontend.codegen.runtime_abi import declare_runtime
    module = ir.Module(name="my_module")
    rt = declare_runtime(module)
    builder.call(rt["py_print"], [obj])

Every entry in ``RUNTIME_SIGNATURES`` corresponds 1:1 with a prototype
in py_runtime.h. Changes to the C header MUST be reflected here (and
vice-versa) — the contract doc is the single source of truth.
"""

from __future__ import annotations

from pcc.llvm_capi.compat import ir

# -- Canonical LLVM IR types used in the runtime ABI -------------------------

_VOID = ir.VoidType()
_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_DOUBLE = ir.DoubleType()
# Opaque pointer. llvmlite emits typed pointers, but we use i8* as the
# generic ptr everywhere the ABI takes/returns a PyObject*. Subsequent
# opt passes ignore the pointee type for opaque-pointer LLVM versions.
_PTR = _I8.as_pointer()
_I32_PTR = _I32.as_pointer()
_CSTR = _I8.as_pointer()  # const char*
_CSTR_PTR = _CSTR.as_pointer()  # const char**

# ``PyObject*`` is spelled ``_PTR`` at the LLVM-IR level.
_PYOBJ = _PTR


# Table of (return_type, [param_types], var_arg). The names and
# signatures mirror py_runtime.h line-for-line. Whenever adding a new
# runtime function, add the C prototype there first, then mirror here.
RUNTIME_SIGNATURES: dict[str, tuple[ir.Type, list[ir.Type], bool]] = {
    # ---- GC interface ----------------------------------------------
    "pcc_gc_alloc": (_PYOBJ, [_I64, _I32, _I32], False),
    "pcc_gc_retain": (_PYOBJ, [_PYOBJ], False),
    "pcc_gc_release": (_VOID, [_PYOBJ], False),
    "pcc_debug_check_release": (_VOID, [_CSTR, _PYOBJ], False),
    "pcc_gc_load_ptr": (_PYOBJ, [_PYOBJ, _PTR], False),
    "pcc_gc_store_ptr": (_VOID, [_PYOBJ, _PTR, _PYOBJ], False),
    "pcc_gc_store_root": (_VOID, [_PTR, _PYOBJ], False),
    "pcc_gc_note_write_barrier": (_VOID, [_PYOBJ, _PYOBJ], False),
    "pcc_gc_note_slot_write_barrier": (_VOID, [_PYOBJ, _PTR, _PYOBJ], False),
    "pcc_gc_scheduler_root_register": (_VOID, [_PTR], False),
    "pcc_gc_scheduler_root_unregister": (_VOID, [_PTR], False),
    "pcc_gc_register_continuation_root": (_VOID, [_PTR, _PTR], False),
    "pcc_gc_unregister_continuation_root": (_VOID, [_PTR], False),
    "pcc_gc_trace_continuation_roots": (_I64, [], False),
    "pcc_gc_rewrite_continuation_roots": (_I64, [], False),
    "pcc_gc_scheduler_queue_new": (_PTR, [], False),
    "pcc_gc_scheduler_queue_free": (_VOID, [_PTR], False),
    "pcc_gc_scheduler_queue_push": (_I64, [_PTR, _PYOBJ], False),
    "pcc_gc_scheduler_queue_pop_into": (_I64, [_PTR, _PTR], False),
    "pcc_gc_scheduler_queue_len": (_I64, [_PTR], False),
    "pcc_gc_frame_enter": (_VOID, [_PTR, _PTR], False),
    "pcc_gc_frame_leave": (_VOID, [_PTR], False),
    "pcc_gc_safepoint": (_VOID, [], False),
    "pcc_gc_collect": (_I64, [_I32], False),
    "pcc_gc_pin": (_VOID, [_PYOBJ], False),
    "pcc_gc_unpin": (_VOID, [_PYOBJ], False),
    "pcc_gc_object_id": (_I64, [_PYOBJ], False),
    "pcc_gc_reset_relocation_set": (_VOID, [], False),
    "pcc_gc_select_relocation_set": (_I64, [_I64], False),
    "pcc_gc_backend4_evacuation_drain": (_I64, [_I64], False),
    "pcc_gc_backend4_evacuation_page_drain": (_I64, [_I64], False),
    "pcc_gc_relocation_set_contains": (_I64, [_PYOBJ], False),
    "pcc_gc_relocation_set_size": (_I64, [], False),
    "pcc_gc_install_forwarding": (_I64, [_PYOBJ, _PYOBJ], False),
    "pcc_gc_relocate_copy": (_PYOBJ, [_PYOBJ, _I64], False),
    "pcc_gc_backend": (_I64, [], False),
    "pcc_gc_set_backend": (_I64, [_I64], False),
    "pcc_gc_backend_name": (_CSTR, [_I64], False),
    "pcc_gc_telemetry": (_I64, [_I64], False),
    "pcc_gc_telemetry_reset": (_VOID, [], False),
    "pcc_gc_step": (_I64, [_I64], False),
    "pcc_gc_backend4_verify_no_old_addresses": (_I64, [], False),
    "pcc_gc_backend4_fragmentation_score": (_I64, [], False),
    "pcc_gc_backend4_forwarding_entries": (_I64, [], False),
    "pcc_gc_backend4_stable_id_entries": (_I64, [], False),
    "pcc_gc_backend4_generation_barrier_score": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_entries": (_I64, [], False),
    "pcc_gc_backend4_generation_promotion_score": (_I64, [], False),
    "pcc_gc_backend4_evacuation_candidate_score": (_I64, [], False),
    "pcc_gc_backend4_evacuated_bytes": (_I64, [], False),
    "pcc_gc_backend4_page_policy_score": (_I64, [], False),
    "pcc_gc_backend4_large_object_defer_score": (_I64, [], False),
    "pcc_gc_backend4_large_object_deferred_bytes": (_I64, [], False),
    "pcc_gc_backend4_small_page_candidate_score": (_I64, [], False),
    "pcc_gc_backend4_medium_page_candidate_score": (_I64, [], False),
    "pcc_gc_backend4_evacuation_candidate_bytes": (_I64, [], False),
    "pcc_gc_backend4_small_page_candidate_bytes": (_I64, [], False),
    "pcc_gc_backend4_medium_page_candidate_bytes": (_I64, [], False),
    "pcc_gc_backend4_evacuation_candidate_zpage_bytes": (_I64, [], False),
    "pcc_gc_backend4_small_page_candidate_zpage_bytes": (_I64, [], False),
    "pcc_gc_backend4_medium_page_candidate_zpage_bytes": (_I64, [], False),
    "pcc_gc_backend4_evacuation_page_candidate_score": (_I64, [], False),
    "pcc_gc_backend4_evacuation_page_candidate_bytes": (_I64, [], False),
    "pcc_gc_backend4_evacuation_page_dirty_cards": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_drain_batches": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_drained_entries": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_duplicate_skips": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_high_water": (_I64, [], False),
    "pcc_gc_backend4_page_pressure_score": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_owner_fanout_high_water": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_owner_count_high_water": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_incomplete_drains": (_I64, [], False),
    "pcc_gc_backend4_evacuation_incomplete_batches": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_batch_capacity": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_max_batch_size": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_full_batches": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_medium_capacity": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_medium_pending": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_medium_flushes": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_medium_flushed_entries": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_medium_full_flushes": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_cross_thread_medium_flushes": (_I64, [], False),
    "pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries": (
        _I64,
        [],
        False,
    ),
    "pcc_gc_backend4_remembered_set_entries": (_I64, [], False),
    "pcc_gc_backend4_remembered_set_duplicate_skips": (_I64, [], False),
    "pcc_gc_backend4_remembered_set_high_water": (_I64, [], False),
    "pcc_gc_backend4_remembered_page_entries": (_I64, [], False),
    "pcc_gc_backend4_remembered_page_slot_entries": (_I64, [], False),
    "pcc_gc_backend4_remembered_page_high_water": (_I64, [], False),
    "pcc_gc_backend4_remembered_page_contains_slot": (_I64, [_PTR], False),
    "pcc_gc_backend4_remembered_page_clear_slot": (_I64, [_PTR], False),
    "pcc_gc_backend4_zpage_contains_remembered_card": (_I64, [_PTR, _PTR], False),
    "pcc_gc_backend4_zpage_clear_remembered_card": (_I64, [_PTR, _PTR], False),
    "pcc_gc_backend4_zpage_count": (_I64, [], False),
    "pcc_gc_backend4_zpage_capacity_bytes": (_I64, [], False),
    "pcc_gc_backend4_zpage_fragmentation_bytes": (_I64, [], False),
    "pcc_gc_backend4_zpage_large_pages": (_I64, [], False),
    "pcc_gc_backend4_zpage_used_bytes": (_I64, [], False),
    "pcc_gc_backend4_zpage_allocated_bytes": (_I64, [], False),
    "pcc_gc_backend4_zpage_reclaimable_gap_bytes": (_I64, [], False),
    "pcc_gc_backend4_zpage_span_bytes": (_I64, [], False),
    "pcc_gc_backend4_zpage_owner_offset_bytes": (_I64, [_PYOBJ], False),
    "pcc_gc_backend4_zpage_owner_size_bytes": (_I64, [_PYOBJ], False),
    "pcc_gc_backend4_zpage_owner_span_card": (_I64, [_PYOBJ], False),
    "pcc_gc_backend4_zpage_owner_slot_span_card": (_I64, [_PYOBJ, _PTR], False),
    "pcc_gc_backend4_zpage_register_owner_payload_span": (
        _I64,
        [_PYOBJ, _PTR, _I64],
        False,
    ),
    "pcc_gc_backend4_zpage_fragmentation_per_mille": (_I64, [], False),
    "pcc_gc_backend4_zpage_policy_score": (_I64, [], False),
    "pcc_gc_backend4_zpage_remembered_slots": (_I64, [], False),
    "pcc_gc_backend4_zpage_remembered_cards": (_I64, [], False),
    "pcc_gc_backend4_zpage_remembered_card_ratio_per_mille": (_I64, [], False),
    "pcc_gc_backend4_zpage_dirty_pages": (_I64, [], False),
    "pcc_gc_backend4_zpage_fragmented_pages": (_I64, [], False),
    "pcc_gc_backend4_zpage_young_pages": (_I64, [], False),
    "pcc_gc_backend4_zpage_old_pages": (_I64, [], False),
    "pcc_gc_backend4_zpage_free_pages": (_I64, [], False),
    "pcc_gc_backend4_zpage_free_capacity_bytes": (_I64, [], False),
    "pcc_gc_backend4_zpage_free_span_bytes": (_I64, [], False),
    "pcc_gc_backend4_evacuation_efficiency_per_mille": (_I64, [], False),
    "pcc_gc_backend4_fragmentation_backlog_bytes": (_I64, [], False),
    "pcc_gc_backend4_fragmentation_policy_score": (_I64, [], False),
    "pcc_gc_backend4_small_page_limit_bytes": (_I64, [], False),
    "pcc_gc_backend4_medium_page_limit_bytes": (_I64, [], False),
    "pcc_gc_backend4_large_defer_limit_bytes": (_I64, [], False),
    "pcc_gc_backend4_large_object_reconsiderations": (_I64, [], False),
    "pcc_gc_backend4_young_object_count": (_I64, [], False),
    "pcc_gc_backend4_old_object_count": (_I64, [], False),
    "pcc_gc_backend4_young_bytes": (_I64, [], False),
    "pcc_gc_backend4_old_bytes": (_I64, [], False),
    "pcc_gc_backend4_small_page_object_count": (_I64, [], False),
    "pcc_gc_backend4_medium_page_object_count": (_I64, [], False),
    "pcc_gc_backend4_large_page_object_count": (_I64, [], False),
    "pcc_gc_backend4_small_page_live_bytes": (_I64, [], False),
    "pcc_gc_backend4_medium_page_live_bytes": (_I64, [], False),
    "pcc_gc_backend4_large_page_live_bytes": (_I64, [], False),
    "pcc_gc_backend2_production_score": (_I64, [], False),
    "pcc_gc_backend2_worker_buffer_score": (_I64, [], False),
    "pcc_gc_backend3_minor_productivity_score": (_I64, [], False),
    "pcc_gc_backend3_remembered_update_score": (_I64, [], False),
    "pcc_gc_scheduler_root_count": (_I64, [], False),
    "pcc_gc_frame_root_slot_count": (_I64, [], False),
    "pcc_gc_continuation_root_slot_count": (_I64, [], False),
    "pcc_gc_coroutine_root_score": (_I64, [], False),
    "py_gc_callbacks_list": (_PYOBJ, [], False),
    "py_gc_callbacks_append": (_VOID, [_PYOBJ], False),
    "py_gc_callbacks_remove": (_VOID, [_PYOBJ], False),
    # ---- Runtime threading substrate -------------------------------
    "pcc_threads_enabled": (_I64, [], False),
    "pcc_current_thread_id": (_I64, [], False),
    "pcc_refcount_strategy": (_I64, [], False),
    "pcc_thread_safepoint": (_VOID, [], False),
    "pcc_stop_the_world": (_I64, [], False),
    "pcc_resume_world": (_I64, [], False),
    # ---- Native threading module ---------------------------------
    "py_threading_get_ident": (_I64, [], False),
    "py_threading_current_thread": (_PYOBJ, [], False),
    "py_threading_lock_new": (_PYOBJ, [], False),
    "py_threading_lock_acquire": (_I64, [_PYOBJ], False),
    "py_threading_lock_acquire_vthread": (_I64, [_PYOBJ], False),
    "py_threading_lock_release": (_I64, [_PYOBJ], False),
    "py_threading_rlock_new": (_PYOBJ, [], False),
    "py_threading_rlock_acquire": (_I64, [_PYOBJ], False),
    "py_threading_rlock_release": (_I64, [_PYOBJ], False),
    "py_threading_event_new": (_PYOBJ, [], False),
    "py_threading_event_set": (_I64, [_PYOBJ], False),
    "py_threading_event_clear": (_I64, [_PYOBJ], False),
    "py_threading_event_is_set": (_I64, [_PYOBJ], False),
    "py_threading_event_wait": (_I64, [_PYOBJ], False),
    "py_threading_event_wait_vthread": (_I64, [_PYOBJ], False),
    "py_threading_condition_new": (_PYOBJ, [_PYOBJ], False),
    "py_threading_condition_acquire": (_I64, [_PYOBJ], False),
    "py_threading_condition_release": (_I64, [_PYOBJ], False),
    "py_threading_condition_wait": (_I64, [_PYOBJ], False),
    "py_threading_condition_wait_vthread": (_I64, [_PYOBJ], False),
    "py_threading_condition_notify": (_I64, [_PYOBJ], False),
    "py_threading_semaphore_new": (_PYOBJ, [_I64], False),
    "py_threading_semaphore_acquire": (_I64, [_PYOBJ], False),
    "py_threading_semaphore_acquire_vthread": (_I64, [_PYOBJ], False),
    "py_threading_semaphore_release": (_I64, [_PYOBJ], False),
    "py_threading_thread_new": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_threading_thread_start": (_I64, [_PYOBJ], False),
    "py_threading_thread_join": (_I64, [_PYOBJ], False),
    "py_threading_thread_is_alive": (_I64, [_PYOBJ], False),
    # ---- refcount --------------------------------------------------
    "py_incref": (_VOID, [_PYOBJ], False),
    "py_decref": (_VOID, [_PYOBJ], False),
    # ---- Bool ------------------------------------------------------
    "py_bool_from_bit": (_PYOBJ, [_I32], False),
    # ---- Int (tagged + bignum) ------------------------------------
    "py_int_from_i64": (_PYOBJ, [_I64], False),
    "py_int_from_cstr": (_PYOBJ, [_CSTR, _I32], False),
    "py_int_from_cstr_or_raise": (_PYOBJ, [_CSTR, _I32], False),
    "py_int_to_i64": (_I64, [_PYOBJ, _I32_PTR], False),
    "py_int_bit_length": (_I64, [_PYOBJ], False),
    "py_int_add": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_sub": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_mul": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_floordiv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_truediv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_mod": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_pow": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_pow_mod": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_min_max": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_tuple_count": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_tuple_index": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_int_neg": (_PYOBJ, [_PYOBJ], False),
    "py_int_and": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_or": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_xor": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_shl": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_shr": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_int_cmp": (_I32, [_PYOBJ, _PYOBJ], False),
    "py_int_format_hex": (_PYOBJ, [_PYOBJ, _I64, _I64], False),
    "py_int_format_decimal": (_PYOBJ, [_PYOBJ, _I64, _I64, _I64], False),
    "py_builtin_bin": (_PYOBJ, [_PYOBJ], False),
    "py_builtin_hex": (_PYOBJ, [_PYOBJ], False),
    "py_builtin_oct": (_PYOBJ, [_PYOBJ], False),
    # ---- Float -----------------------------------------------------
    "py_float_from_f64": (_PYOBJ, [_DOUBLE], False),
    "py_float_to_f64": (_DOUBLE, [_PYOBJ], False),
    "py_float_is_integer": (_I64, [_PYOBJ], False),
    "py_float_add": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_float_format_fixed": (_PYOBJ, [_PYOBJ, _I64], False),
    # ---- Complex ---------------------------------------------------
    "py_complex_new": (_PYOBJ, [_DOUBLE, _DOUBLE], False),
    "py_complex_real": (_PYOBJ, [_PYOBJ], False),
    "py_complex_imag": (_PYOBJ, [_PYOBJ], False),
    "py_complex_add": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    # ---- Bytes / bytearray / memoryview ----------------------------
    "py_bytes_new": (_PYOBJ, [_CSTR, _I64], False),
    "py_bytearray_from_obj": (_PYOBJ, [_PYOBJ], False),
    "py_bytes_from_obj": (_PYOBJ, [_PYOBJ], False),
    "py_memoryview_new": (_PYOBJ, [_PYOBJ], False),
    "py_bytes_decode": (_PYOBJ, [_PYOBJ], False),
    "py_bytes_hex": (_PYOBJ, [_PYOBJ], False),
    "py_bytes_getitem": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_bytes_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_bytes_concat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_bytes_repeat": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_bytes_len": (_I64, [_PYOBJ], False),
    "py_bytearray_setitem": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    # ---- Str -------------------------------------------------------
    "py_str_new": (_PYOBJ, [_CSTR, _I64], False),
    "py_str_len": (_I64, [_PYOBJ], False),
    "py_str_byte_len": (_I64, [_PYOBJ], False),
    "py_str_utf8": (_CSTR, [_PYOBJ], False),
    "py_str_ord": (_I64, [_PYOBJ], False),
    "py_str_ord_at_i64": (_I64, [_PYOBJ, _I64], False),
    "py_str_byte_at_i64": (_I64, [_PYOBJ, _I64], False),
    "py_str_latin1_encode": (_PYOBJ, [_PYOBJ], False),
    "py_str_utf8_encode": (_PYOBJ, [_PYOBJ], False),
    "py_str_byte_slice_i64": (_PYOBJ, [_PYOBJ, _I64, _I64], False),
    "py_str_concat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_repeat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_str_index": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_eq": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_find": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_rfind": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_upper": (_PYOBJ, [_PYOBJ], False),
    "py_str_lower": (_PYOBJ, [_PYOBJ], False),
    "py_str_capitalize": (_PYOBJ, [_PYOBJ], False),
    "py_str_swapcase": (_PYOBJ, [_PYOBJ], False),
    "py_str_title": (_PYOBJ, [_PYOBJ], False),
    "py_str_casefold": (_PYOBJ, [_PYOBJ], False),
    "py_str_strip": (_PYOBJ, [_PYOBJ], False),
    "py_str_split": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_partition": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_rpartition": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_translate": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_maketrans": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_removeprefix": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_removesuffix": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_rjust": (_PYOBJ, [_PYOBJ, _I64, _PYOBJ], False),
    "py_str_ljust": (_PYOBJ, [_PYOBJ, _I64, _PYOBJ], False),
    "py_str_center": (_PYOBJ, [_PYOBJ, _I64, _PYOBJ], False),
    "py_str_zfill": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_str_expandtabs": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_str_rsplit_maxsplit": (_PYOBJ, [_PYOBJ, _PYOBJ, _I64], False),
    "py_str_split_maxsplit": (_PYOBJ, [_PYOBJ, _PYOBJ, _I64], False),
    "py_str_splitlines": (_PYOBJ, [_PYOBJ], False),
    "py_str_splitlines_keepends": (_PYOBJ, [_PYOBJ, _I32], False),
    "py_str_lstrip": (_PYOBJ, [_PYOBJ], False),
    "py_str_rstrip": (_PYOBJ, [_PYOBJ], False),
    "py_str_strip_chars": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_lstrip_chars": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_rstrip_chars": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_count": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_isdigit": (_I64, [_PYOBJ], False),
    "py_str_isalpha": (_I64, [_PYOBJ], False),
    "py_str_isspace": (_I64, [_PYOBJ], False),
    "py_str_isalnum": (_I64, [_PYOBJ], False),
    "py_str_isupper": (_I64, [_PYOBJ], False),
    "py_str_islower": (_I64, [_PYOBJ], False),
    "py_str_index_of": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_rindex_of": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_join": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_replace": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_str_replace_count": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _I64], False),
    "py_str_startswith": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_str_endswith": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_chr_from_i64": (_PYOBJ, [_I64], False),
    "py_json_loads": (_PYOBJ, [_PYOBJ], False),
    "py_json_dumps": (_PYOBJ, [_PYOBJ], False),
    "py_copy_copy": (_PYOBJ, [_PYOBJ], False),
    "py_copy_deepcopy": (_PYOBJ, [_PYOBJ], False),
    "py_pickle_dumps": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_pickle_loads": (_PYOBJ, [_PYOBJ], False),
    # ---- List ------------------------------------------------------
    "py_list_new": (_PYOBJ, [_I64], False),
    "py_list_append": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_list_get": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_list_getitem": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_list_get_i64": (_I64, [_PYOBJ, _I64], False),
    "py_list_get_i64_nonnegative": (_I64, [_PYOBJ, _I64], False),
    "py_list_set": (_VOID, [_PYOBJ, _I64, _PYOBJ], False),
    "py_list_len": (_I64, [_PYOBJ], False),
    "py_list_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_list_set_slice": (
        _I64,
        [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ],
        False,
    ),
    "py_list_del_slice": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_list_concat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_list_repeat": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_list_extend": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_list_insert": (_VOID, [_PYOBJ, _I64, _PYOBJ], False),
    "py_list_pop": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_list_remove": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_list_clear": (_VOID, [_PYOBJ], False),
    "py_obj_clear": (_VOID, [_PYOBJ], False),
    "py_list_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_list_index": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_list_count": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_list_reverse": (_VOID, [_PYOBJ], False),
    # ---- Dict ------------------------------------------------------
    "py_dict_new": (_PYOBJ, [], False),
    "py_dict_set": (_VOID, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_dict_get": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_dict_getitem": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_dict_fromkeys": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_dict_get_default": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_dict_pop": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_dict_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_dict_del": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_dict_clear": (_VOID, [_PYOBJ], False),
    "py_dict_len": (_I64, [_PYOBJ], False),
    "py_dict_keys": (_PYOBJ, [_PYOBJ], False),
    "py_dict_values": (_PYOBJ, [_PYOBJ], False),
    "py_dict_items": (_PYOBJ, [_PYOBJ], False),
    "py_dict_update": (_VOID, [_PYOBJ, _PYOBJ], False),
    # ---- Tuple -----------------------------------------------------
    "py_tuple_new": (_PYOBJ, [_I64], False),
    "py_tuple_set_item": (_VOID, [_PYOBJ, _I64, _PYOBJ], False),
    "py_tuple_get": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_tuple_len": (_I64, [_PYOBJ], False),
    "py_tuple_concat": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_tuple_repeat": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_tuple_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    # ---- Descriptor wrappers --------------------------------------
    "py_property_new": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_classmethod_new": (_PYOBJ, [_PYOBJ], False),
    # ---- Set -------------------------------------------------------
    "py_set_new": (_PYOBJ, [], False),
    "py_set_add": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_set_update": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_set_intersection": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_set_difference": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_set_symmetric_difference": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_set_issubset": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_set_issuperset": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_set_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_set_remove": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_set_len": (_I64, [_PYOBJ], False),
    # ---- Generic object ops ---------------------------------------
    "py_obj_call": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_call_method1": (_PYOBJ, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_obj_add": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_obj_sub": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_obj_mul": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_obj_truediv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_obj_mod": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_str_mod": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_weakref_new": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_weakref_call": (_PYOBJ, [_PYOBJ], False),
    "py_weakref_invalidate": (_VOID, [_PYOBJ], False),
    "py_weak_value_dict_new": (_PYOBJ, [], False),
    "py_weak_value_dict_set": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_weak_value_dict_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_weak_value_dict_len": (_I64, [_PYOBJ], False),
    "py_weak_key_dict_new": (_PYOBJ, [], False),
    "py_weak_key_dict_set": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_weak_key_dict_len": (_I64, [_PYOBJ], False),
    "py_obj_getattr": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_obj_getattr_default": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_obj_setattr": (_I64, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_obj_delattr": (_I64, [_PYOBJ, _CSTR], False),
    "py_obj_type_name": (_PYOBJ, [_PYOBJ], False),
    "py_type_builtin": (_PYOBJ, [_PYOBJ], False),
    "py_obj_getitem": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_obj_slice": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_set_slice": (
        _I64,
        [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ],
        False,
    ),
    "py_obj_del_slice": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_setitem": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_delitem": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_len": (_I64, [_PYOBJ], False),
    "py_obj_contains": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_sorted": (_PYOBJ, [_PYOBJ], False),
    "py_obj_truthy": (_I64, [_PYOBJ], False),
    "py_obj_type_tag": (_I64, [_PYOBJ], False),
    "py_obj_eq": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_lt": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_le": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_gt": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_ge": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_hash": (_I64, [_PYOBJ], False),
    "py_obj_index_i64": (_I64, [_PYOBJ], False),
    "py_obj_repr": (_PYOBJ, [_PYOBJ], False),
    "py_obj_ascii": (_PYOBJ, [_PYOBJ], False),
    "py_obj_str": (_PYOBJ, [_PYOBJ], False),
    "py_obj_isinstance": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_obj_iter": (_PYOBJ, [_PYOBJ], False),
    "py_obj_next": (_PYOBJ, [_PYOBJ], False),
    "py_user_str_dispatch": (_PYOBJ, [_PYOBJ], False),
    "py_user_repr_dispatch": (_PYOBJ, [_PYOBJ], False),
    "py_user_hash_dispatch": (_I64, [_PYOBJ, _PTR], False),
    "py_user_iter_dispatch": (_PYOBJ, [_PYOBJ], False),
    "py_user_next_dispatch": (_PYOBJ, [_PYOBJ], False),
    "py_user_matmul_dispatch": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_call_merge_posargs": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_call_merge_kwargs": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_zip_star": (_PYOBJ, [_PYOBJ], False),
    "py_obj_call_splat": (
        _PYOBJ,
        [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ],
        False,
    ),
    "py_module_attrs_dict": (_PYOBJ, [_CSTR, _I64], False),
    "py_module_attr_set": (_I64, [_CSTR, _CSTR, _PYOBJ], False),
    "py_module_attr_get": (_PYOBJ, [_CSTR, _CSTR], False),
    "py_module_attr_value_or_default": (_PYOBJ, [_CSTR_PTR, _PYOBJ], False),
    "py_module_attr_del": (_I64, [_CSTR, _CSTR], False),
    "py_module_attr_len": (_I64, [_CSTR], False),
    "py_native_extension_import": (_PYOBJ, [_CSTR, _CSTR], False),
    "py_native_extension_import_by_name": (_PYOBJ, [_CSTR], False),
    # ---- Native function objects -----------------------------------
    "py_func_new": (_PYOBJ, [_PTR, _PYOBJ], False),
    "py_func_new_named": (_PYOBJ, [_PTR, _PYOBJ, _CSTR], False),
    "py_func_new_bound": (_PYOBJ, [_PTR, _PYOBJ, _CSTR, _PYOBJ], False),
    "py_func_call": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_functools_partial": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    # ---- Native generator objects ----------------------------------
    "py_gen_new": (_PYOBJ, [_PTR, _PYOBJ], False),
    "py_gen_next": (_PYOBJ, [_PYOBJ], False),
    "py_gen_send": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_gen_throw": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_gen_close": (_PYOBJ, [_PYOBJ], False),
    "py_gen_take_send": (_PYOBJ, [_PYOBJ], False),
    "py_gen_state": (_I64, [_PYOBJ], False),
    "py_gen_set_state": (_VOID, [_PYOBJ, _I64], False),
    "py_gen_set_done": (_VOID, [_PYOBJ], False),
    "py_gen_is_done": (_I64, [_PYOBJ], False),
    "py_gen_finish": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    # ---- Native coroutine shell objects ----------------------------
    "py_coroutine_new": (_PYOBJ, [_CSTR], False),
    "py_coroutine_new_native": (_PYOBJ, [_CSTR, _PTR, _PYOBJ, _PYOBJ], False),
    "py_coroutine_run": (_PYOBJ, [_PYOBJ], False),
    "py_coroutine_close": (_PYOBJ, [_PYOBJ], False),
    "py_coroutine_class": (_PYOBJ, [], False),
    "py_coroutine_is_done": (_I64, [_PYOBJ], False),
    "py_coroutine_get_result": (_PYOBJ, [_PYOBJ], False),
    "py_await": (_PYOBJ, [_PYOBJ], False),
    "py_asyncio_sleep": (_PYOBJ, [_PYOBJ], False),
    "py_continuation_class": (_PYOBJ, [], False),
    "py_continuation_new": (_PYOBJ, [_PTR, _PTR, _PTR], False),
    "py_continuation_new_typed": (_PYOBJ, [_PTR, _PTR, _PTR], False),
    "py_continuation_mount": (_I64, [_PYOBJ, _PTR], False),
    "py_continuation_unmount": (_I64, [_PYOBJ, _PTR, _PTR], False),
    "py_continuation_is_mounted": (_I64, [_PYOBJ], False),
    "py_continuation_resume_pc": (_PTR, [_PYOBJ], False),
    "py_continuation_resume_abi": (_I64, [_PYOBJ], False),
    "py_continuation_slot_count": (_I64, [_PYOBJ], False),
    "py_continuation_get_slot": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_continuation_set_slot": (_I64, [_PYOBJ, _I64, _PYOBJ], False),
    "py_virtual_thread_current": (_PYOBJ, [], False),
    "py_virtual_thread_resume_generator": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_virtual_thread_new": (_PYOBJ, [_PYOBJ], False),
    "py_virtual_thread_start": (_I64, [_PYOBJ], False),
    "py_virtual_thread_park": (_I64, [_PYOBJ], False),
    "py_virtual_thread_unpark": (_I64, [_PYOBJ], False),
    "py_virtual_thread_sleep": (_I64, [_PYOBJ, _I64], False),
    "py_virtual_thread_poll_timers": (_I64, [], False),
    "py_virtual_thread_timer_count": (_I64, [], False),
    "py_virtual_thread_block_on_fd": (_I64, [_PYOBJ, _I64, _I64, _I64], False),
    "py_virtual_thread_poll_io": (_I64, [_I64], False),
    "py_virtual_thread_io_wait_count": (_I64, [], False),
    "py_virtual_thread_pin_enter": (_I64, [_PYOBJ, _CSTR], False),
    "py_virtual_thread_pin_leave": (_I64, [_PYOBJ], False),
    "py_virtual_thread_pin_count": (_I64, [_PYOBJ], False),
    "py_virtual_thread_pinned_count": (_I64, [], False),
    "py_virtual_thread_pin_event_count": (_I64, [], False),
    "py_virtual_thread_poll_ready": (_PYOBJ, [], False),
    "py_virtual_thread_ready_count": (_I64, [], False),
    "py_virtual_thread_carrier_count": (_I64, [], False),
    "py_virtual_thread_carrier_steal_count": (_I64, [], False),
    "py_virtual_thread_run_once": (_I64, [], False),
    "py_virtual_thread_run_until_idle": (_I64, [_I64], False),
    "py_virtual_thread_run_carrier_pool": (_I64, [_I64, _I64], False),
    "py_virtual_thread_carrier_pool_start": (_I64, [_I64], False),
    "py_virtual_thread_carrier_pool_stop": (_I64, [], False),
    "py_virtual_thread_state": (_I64, [_PYOBJ], False),
    "py_virtual_thread_complete": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_virtual_thread_result": (_PYOBJ, [_PYOBJ], False),
    "py_task_new": (_PYOBJ, [_PYOBJ], False),
    "py_task_step": (_PYOBJ, [_PYOBJ], False),
    "py_task_is_done": (_I64, [_PYOBJ], False),
    "py_task_set_result": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_task_set_waiter": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_task_get_coro": (_PYOBJ, [_PYOBJ], False),
    "py_task_get_result": (_PYOBJ, [_PYOBJ], False),
    "py_task_get_waiter": (_PYOBJ, [_PYOBJ], False),
    "py_context_enter": (_PYOBJ, [_PYOBJ], False),
    "py_context_exit": (_I64, [_PYOBJ, _PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_obj_format": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    # ---- File I/O --------------------------------------------------
    "py_file_open": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_file_read_all": (_PYOBJ, [_PYOBJ], False),
    "py_file_read": (_PYOBJ, [_PYOBJ, _I64], False),
    "py_file_write": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_file_close": (_VOID, [_PYOBJ], False),
    "py_fileinput_new": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_fileinput_readline": (_PYOBJ, [_PYOBJ], False),
    "py_fileinput_filename": (_PYOBJ, [_PYOBJ], False),
    "py_fileinput_lineno": (_PYOBJ, [_PYOBJ], False),
    "py_fileinput_filelineno": (_PYOBJ, [_PYOBJ], False),
    "py_fileinput_isfirstline": (_PYOBJ, [_PYOBJ], False),
    "py_fileinput_close": (_PYOBJ, [_PYOBJ], False),
    # ---- Printing --------------------------------------------------
    "py_print": (_VOID, [_PYOBJ], False),
    "py_print_many": (_VOID, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_sys_stdout_write": (_PYOBJ, [_PYOBJ], False),
    "py_sys_stderr_write": (_PYOBJ, [_PYOBJ], False),
    # ---- Process startup -------------------------------------------
    "py_set_program_args": (_VOID, [_I32, _CSTR_PTR], False),
    "py_program_argc": (_I64, [], False),
    "py_program_argv": (_CSTR, [_I64], False),
    "py_process_exit": (_VOID, [_I64], False),
    "py_sys_executable_str": (_PYOBJ, [], False),
    "py_sys_prefix_str": (_PYOBJ, [_I64], False),
    "py_os_getpid": (_PYOBJ, [], False),
    "py_subprocess_check_output": (_PYOBJ, [_PYOBJ], False),
    "py_subprocess_run": (_I64, [_PYOBJ, _I32], False),
    "py_sysconfig_get_config_var": (_PYOBJ, [_PYOBJ], False),
    "py_os_listdir": (_PYOBJ, [_PYOBJ], False),
    "py_shlex_split": (_PYOBJ, [_PYOBJ], False),
    "py_shutil_which": (_PYOBJ, [_PYOBJ], False),
    "py_tempdir_new": (_PYOBJ, [_PYOBJ], False),
    "py_tempdir_cleanup": (_VOID, [_PYOBJ], False),
    "py_re_escape": (_PYOBJ, [_PYOBJ], False),
    "py_re_match": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_re_match_flags": (_PYOBJ, [_PYOBJ, _PYOBJ, _I64], False),
    "py_re_search": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_re_search_flags": (_PYOBJ, [_PYOBJ, _PYOBJ, _I64], False),
    "py_re_findall_flags": (_PYOBJ, [_PYOBJ, _PYOBJ, _I64], False),
    "py_re_compile_method": (_PYOBJ, [_PYOBJ, _I64, _I64], False),
    "py_time_monotonic": (_PYOBJ, [], False),
    # ---- Narrow os.path subset ------------------------------------
    "py_os_getenv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_os_putenv": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_os_unsetenv": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_join": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_basename": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_dirname": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_split": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_splitext": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_normcase": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_normpath": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_splitdrive": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_exists": (_I32, [_PYOBJ], False),
    "py_os_path_isabs": (_I32, [_PYOBJ], False),
    "py_os_path_isfile": (_I32, [_PYOBJ], False),
    "py_os_path_isdir": (_I32, [_PYOBJ], False),
    "py_os_path_getmtime": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_abspath": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_expanduser": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_realpath": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_commonpath": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_expandvars": (_PYOBJ, [_PYOBJ], False),
    "py_os_path_relpath": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_os_path_commonprefix": (_PYOBJ, [_PYOBJ], False),
    "py_os_getcwd_str": (_PYOBJ, [], False),
    "py_os_access": (_I32, [_PYOBJ, _I32], False),
    "py_os_write": (_I32, [_I32, _PYOBJ], False),

    "py_http_download_to_file": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_os_cpu_count": (_PYOBJ, [], False),
    "py_sys_platform_str": (_PYOBJ, [], False),
    "py_sys_path_list": (_PYOBJ, [], False),
    "py_platform_machine_str": (_PYOBJ, [], False),
    "py_platform_release_str": (_PYOBJ, [], False),
    # ---- Exceptions (Phase 3) -------------------------------------
    "py_raise": (_VOID, [_PYOBJ], False),
    "py_current_exception": (_PYOBJ, [], False),
    "py_clear_exception": (_VOID, [], False),
    "py_exc_new": (_PYOBJ, [_I64, _CSTR], False),
    "py_exc_new_with_value": (_PYOBJ, [_I64, _PYOBJ], False),
    "py_exc_new_with_class": (_PYOBJ, [_PYOBJ, _CSTR], False),
    # py_exc_builtin_class(tag) -> PyClassObject* for a builtin class tag.
    # i64 to match the pcc-Python port's default int lowering.
    "py_exc_builtin_class": (_PYOBJ, [_I64], False),
    # py_exc_matches(exc, class) -> 0/1 (walks MRO). i64 to match the
    # pcc-Python port's default `int` lowering.
    "py_exc_matches": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_exc_set_cause": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_exc_set_context": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_exc_append_frame": (_VOID, [_PYOBJ, _CSTR, _CSTR, _I32], False),
    "py_exc_print_unhandled": (_VOID, [_PYOBJ], False),
    "py_exc_get_message": (_PYOBJ, [_PYOBJ], False),
    "py_exc_get_cause": (_PYOBJ, [_PYOBJ], False),
    "py_exc_get_context": (_PYOBJ, [_PYOBJ], False),
    "py_exc_traceback_len": (_I64, [_PYOBJ], False),
    # Return-code exception check (post-call branch target). i64 to
    # match the pcc-Python port's default `int` lowering.
    "py_err_occurred": (_I64, [], False),
    # ---- Classes / Instances (Phase 3) ----------------------------
    # py_class_new(name: const char*,
    #              bases: PyClassObject**, n_bases: i32,
    #              field_names: const char**, n_fields: i32)
    #   -> PyClassObject*
    "py_class_new": (_PYOBJ, [_CSTR, _PTR, _I32, _PTR, _I32], False),
    "py_class_new_from_objects": (_PYOBJ, [_PYOBJ, _PYOBJ, _PYOBJ], False),
    "py_class_mark_slots_only": (_VOID, [_PYOBJ], False),
    "py_class_add_method": (_VOID, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_class_set_metaclass": (_VOID, [_PYOBJ, _PYOBJ], False),
    "py_class_lookup": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_class_getattr": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_class_setattr": (_I64, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_class_setattr_raw": (_I64, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_class_apply_namespace_dict": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_class_delattr": (_I64, [_PYOBJ, _CSTR], False),
    "py_valuebox_new": (_PYOBJ, [_PYOBJ], False),
    "py_valuebox_get_field": (_PYOBJ, [_PYOBJ, _I32], False),
    "py_valuebox_set_field": (_VOID, [_PYOBJ, _I32, _PYOBJ], False),
    "py_instance_new": (_PYOBJ, [_PYOBJ], False),
    "py_instance_get_field": (_PYOBJ, [_PYOBJ, _I32], False),
    "py_instance_set_field": (_VOID, [_PYOBJ, _I32, _PYOBJ], False),
    "py_instance_getattr": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_instance_getattr_default": (_PYOBJ, [_PYOBJ, _CSTR], False),
    "py_instance_setattr": (_I64, [_PYOBJ, _CSTR, _PYOBJ], False),
    "py_dataclass_replace": (_PYOBJ, [_PYOBJ, _I64, _PTR, _PTR], False),
    "py_dataclass_replace_from_dict": (_PYOBJ, [_PYOBJ, _PYOBJ], False),
    "py_isinstance": (_I64, [_PYOBJ, _PYOBJ], False),
    "py_super_lookup": (_PYOBJ, [_PYOBJ, _PYOBJ, _CSTR], False),
    # ---- GC --------------------------------------------------------
    "py_gc_init": (_VOID, [], False),
    "py_gc_collect": (_I64, [], False),
    "py_gc_track": (_VOID, [_PYOBJ], False),
    "py_gc_untrack": (_VOID, [_PYOBJ], False),
    "py_gc_enable": (_VOID, [], False),
    "py_gc_disable": (_VOID, [], False),
    "py_gc_is_enabled": (_I64, [], False),
    "py_gc_is_tracked": (_I64, [_PYOBJ], False),
    "py_gc_get_count": (_I64, [_I32], False),
    "py_gc_get_threshold": (_I64, [_I32], False),
    "py_gc_set_threshold": (_VOID, [_I32, _I32, _I32], False),
    "py_gc_freeze": (_VOID, [], False),
    "py_gc_unfreeze": (_VOID, [], False),
    "py_gc_get_freeze_count": (_I64, [], False),
    "py_gc_get_objects": (_PYOBJ, [], False),
    "py_gc_get_referents": (_PYOBJ, [_PYOBJ], False),
    "py_gc_get_referrers": (_PYOBJ, [_PYOBJ], False),
    # ---- Phase 4: CPython C-API fallback ---------------------------
    # All CPython pointers show as ``i8*`` at the IR boundary; the
    # distinction from pcc ``PyObject*`` is tracked type-side only.
    "py_cpy_ensure_init": (_VOID, [], False),
    "py_cpy_import": (_PTR, [_CSTR], False),
    "py_cpy_getattr": (_PTR, [_PTR, _CSTR], False),
    "py_cpy_binop": (_PTR, [_I64, _PTR, _PTR], False),
    "py_cpy_setattr": (_I32, [_PTR, _CSTR, _PTR], False),
    "py_cpy_main_exitcode": (_I32, [], False),
    "py_cpy_call_noargs": (_PTR, [_PTR], False),
    "py_cpy_call1": (_PTR, [_PTR, _PTR], False),
    "py_cpy_call2": (_PTR, [_PTR, _PTR, _PTR], False),
    "py_cpy_call3": (_PTR, [_PTR, _PTR, _PTR, _PTR], False),
    # (callable, n, argv[]) — PyTuple_SetItem steals each ref in argv.
    "py_cpy_call_argv": (_PTR, [_PTR, _I64, _PTR], False),
    # (callable, n_pos, argv[], n_kw, kw_names[], kw_vals[]) — pos
    # refs stolen; kw refs borrowed. kw_names are C strings.
    "py_cpy_call_kw": (
        _PTR,
        [_PTR, _I64, _PTR, _I64, _PTR, _PTR],
        False,
    ),
    # (callable, n_pos, argv[], kwargs_dict) — pos refs stolen into the
    # tuple; kwargs_dict is borrowed and passed through to PyObject_Call.
    "py_cpy_call_kwdict": (_PTR, [_PTR, _I64, _PTR, _PTR], False),
    # Like py_cpy_call_kwdict, but merges explicit kw pairs into the
    # borrowed kwargs_dict before the call.
    "py_cpy_call_kwdict_plus": (
        _PTR,
        [_PTR, _I64, _PTR, _I64, _PTR, _PTR, _PTR],
        False,
    ),
    # (callable, args_pcc_list) — convert pcc list/tuple to CPython tuple
    # then PyObject_Call. Used for ``fn(*args)`` unpack at call sites.
    "py_cpy_call_list": (_PTR, [_PTR, _PYOBJ], False),
    # Like py_cpy_call_list, but forwards a borrowed CPython kwargs
    # mapping to PyObject_Call as well.
    "py_cpy_call_list_kwdict": (_PTR, [_PTR, _PYOBJ, _PTR], False),
    # (fn_ptr) — wrap a pcc user FuncDef (DynType-in / DynType-out) as
    # a CPython callable via PyCapsule + PyCFunction. Used when a
    # lambda or nested def is passed as a value to a CPython API
    # (``sorted(xs, key=<fn>)``, ``re.sub(pat, <repl>, text)``, etc.).
    "py_cpy_wrap_pcc_0arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_1arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_2arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_3arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_4arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_5arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_6arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_7arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_8arg": (_PTR, [_PTR], False),
    "py_cpy_wrap_pcc_9arg": (_PTR, [_PTR], False),
    "py_cpy_len": (_I64, [_PTR], False),
    "py_cpy_getitem": (_PTR, [_PTR, _PTR], False),
    "py_cpy_setitem": (_I32, [_PTR, _PTR, _PTR], False),
    "py_cpy_truthy": (_I32, [_PTR], False),
    "py_cpy_iter": (_PTR, [_PTR], False),
    "py_cpy_iter_next": (_PTR, [_PTR], False),
    "py_cpy_to_pcc_str": (_PYOBJ, [_PTR], False),
    "py_cpy_to_pcc_obj": (_PYOBJ, [_PTR], False),
    "py_cpy_decref": (_VOID, [_PTR], False),
    "py_cpy_incref": (_VOID, [_PTR], False),
    "py_cpy_from_i64": (_PTR, [_I64], False),
    "py_cpy_to_i64": (_I64, [_PTR], False),
    "py_cpy_from_f64": (_PTR, [_DOUBLE], False),
    "py_cpy_to_f64": (_DOUBLE, [_PTR], False),
    "py_cpy_from_pccstr": (_PTR, [_PYOBJ], False),
    "py_cpy_from_pcc_obj": (_PTR, [_PYOBJ], False),
}

_PURE_RUNTIME_ATTRS = frozenset({"readnone", "willreturn", "nounwind"})
_READONLY_RUNTIME_ATTRS = frozenset({"readonly", "willreturn", "nounwind"})
RUNTIME_FUNCTION_ATTRS: dict[str, frozenset[str]] = {
    "py_bool_from_bit": _PURE_RUNTIME_ATTRS,
    "py_str_byte_len": _READONLY_RUNTIME_ATTRS,
    "py_str_utf8": _READONLY_RUNTIME_ATTRS,
    "py_list_len": _READONLY_RUNTIME_ATTRS,
    "py_list_get_i64": _READONLY_RUNTIME_ATTRS,
    "py_list_get_i64_nonnegative": _READONLY_RUNTIME_ATTRS,
    "py_tuple_len": _READONLY_RUNTIME_ATTRS,
    "py_dict_len": _READONLY_RUNTIME_ATTRS,
    "py_set_len": _READONLY_RUNTIME_ATTRS,
    "py_current_exception": _READONLY_RUNTIME_ATTRS,
    "py_err_occurred": _READONLY_RUNTIME_ATTRS,
    "py_gc_is_enabled": _READONLY_RUNTIME_ATTRS,
    "py_gc_is_tracked": _READONLY_RUNTIME_ATTRS,
    "py_gc_get_count": _READONLY_RUNTIME_ATTRS,
    "py_gc_get_threshold": _READONLY_RUNTIME_ATTRS,
    "pcc_gc_backend": _READONLY_RUNTIME_ATTRS,
    "pcc_gc_backend_name": _READONLY_RUNTIME_ATTRS,
    "pcc_gc_telemetry": _READONLY_RUNTIME_ATTRS,
    "pcc_threads_enabled": _READONLY_RUNTIME_ATTRS,
    "pcc_refcount_strategy": _READONLY_RUNTIME_ATTRS,
}


# Global constants (extern) from py_runtime.h:
#   extern PyObject *const py_None;
#   extern PyObject *const py_True;
#   extern PyObject *const py_False;
RUNTIME_GLOBALS: dict[str, ir.Type] = {
    "py_None": _PYOBJ,
    "py_NotImplemented": _PYOBJ,
    "py_True": _PYOBJ,
    "py_False": _PYOBJ,
    "pcc_thread_stop_requested": _I32,
}

RUNTIME_MUTABLE_GLOBALS = frozenset(
    {
        "pcc_thread_stop_requested",
    }
)


def declare_runtime(module: ir.Module) -> dict[str, ir.Function]:
    """Declare all runtime library functions in ``module``.

    Returns a mapping ``name -> ir.Function`` for every entry in
    :data:`RUNTIME_SIGNATURES`. Declarations are idempotent: calling
    twice on the same module returns the same ``ir.Function`` objects.

    The returned dict intentionally only contains functions. To fetch a
    runtime global (``py_None`` etc.), call :func:`declare_runtime_global`.
    """
    funcs: dict[str, ir.Function] = {}
    for item in RUNTIME_SIGNATURES.items():
        name = item[0]
        sig = item[1]
        ret_ty = sig[0]
        param_tys = sig[1]
        var_arg = sig[2]
        existing = module.globals.get(name)
        if existing is not None and isinstance(existing, ir.Function):
            funcs[name] = existing
            continue
        fnty = ir.FunctionType(ret_ty, param_tys, var_arg=var_arg)
        fn = ir.Function(module, fnty, name=name)
        fn.linkage = "external"
        _apply_runtime_function_attrs(fn, name)
        funcs[name] = fn
    return funcs


def _apply_runtime_function_attrs(fn: ir.Function, name: str) -> None:
    attr_set = RUNTIME_FUNCTION_ATTRS.get(name)
    if not attr_set:
        return
    fa = getattr(fn, "attributes", None)
    if fa is None:
        return
    # NOTE: avoid ``fa.add(attr)`` — pcc-py codegen mis-dispatches the
    # ``.add`` method as ``py_set_add`` regardless of receiver type,
    # which crashes against ``FunctionAttributes._attrs`` (a list).
    # See docs/investigations/pcc1-stage2-runtime-abi-set-segfault.md.
    bag = fa._attrs
    for attr in sorted(attr_set):
        try:
            if attr not in bag:
                bag.append(attr)
        except ValueError:
            # Two run modes for this code path:
            #
            # * Host CPython + llvmlite 0.46+: AttributeSet.add has a
            #   whitelist that lags LLVM upstream and rejects e.g.
            #   `willreturn`. The previous workaround was
            #   ``set.add(fn_attrs, attr)`` — an unbound-method-call
            #   that bypassed the wrapper via MRO. This pattern can't
            #   be lowered natively by pcc's codegen, so when pcc
            #   self-compiles runtime_abi.py the call site routes
            #   through libpython (py_cpy_*), which in turn fails the
            #   no-libpython detector and blocks the self-compile
            #   tests in tests/test_py_multi_file_bootstrap_shim.py.
            #
            # * pcc-compiled binary using pcc.llvm_capi.ir: the local
            #   FunctionAttributes.add (ir.py:972) has no whitelist
            #   and never raises, so this except branch is dead code
            #   — there is no whitelist to bypass.
            #
            # Trade-off: silently dropping the attribute under host
            # CPython loses a perf hint (e.g. willreturn → ADCE may
            # leave a few `py_err_occurred` checks in place that P-7
            # would have eliminated). Other attributes in the same
            # set (readnone, readonly, nounwind) are accepted by
            # llvmlite directly, so they still take effect. Net cost
            # is a small perf miss; net benefit is that pcc can
            # self-compile this module without a libpython link.
            pass


def declare_runtime_global(module: ir.Module, name: str) -> ir.GlobalVariable:
    """Declare (or fetch) one of the runtime's extern constant globals.

    Raises :class:`KeyError` if ``name`` is not a known runtime global.
    """
    if name not in RUNTIME_GLOBALS:
        raise KeyError(f"unknown runtime global: {name!r}")
    existing = module.globals.get(name)
    if existing is not None and isinstance(existing, ir.GlobalVariable):
        return existing
    gv = ir.GlobalVariable(module, RUNTIME_GLOBALS[name], name=name)
    gv.linkage = "external"
    gv.global_constant = name not in RUNTIME_MUTABLE_GLOBALS
    return gv


__all__ = [
    "RUNTIME_SIGNATURES",
    "RUNTIME_FUNCTION_ATTRS",
    "RUNTIME_GLOBALS",
    "declare_runtime",
    "declare_runtime_global",
]
