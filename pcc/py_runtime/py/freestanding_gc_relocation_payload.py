"""Backend 4 raw-payload copying through the shared object-slot contract."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    abi_constant,
    free,
    global_load_ptr,
    global_store_ptr,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memmove,
    memset,
    null,
    ptr_add,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_capi_is_cext_type_tag = extern(
    "pcc_capi_is_cext_type_tag", (c_int64,), c_int64
)
pcc_gc_backend4_remap_heal_slot = extern(
    "pcc_gc_backend4_remap_heal_slot", (c_ptr, c_int64), c_void
)
pcc_gc_backend4_remembered_set_retarget_slot = extern(
    "pcc_gc_backend4_remembered_set_retarget_slot", (c_ptr, c_ptr, c_ptr, c_ptr), c_void
)
pcc_gc_backend4_zpage_register_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_register_owner_payload_span", (c_ptr, c_ptr, c_int64), c_int64
)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
py_incref = extern("py_incref", (c_ptr,), c_void)


@c_abi_export("pcc_gc_relocation_payload_slot_pairs_dispose")
def _relocate_slot_pairs_dispose(ctx) -> None:
    if ptr_is_null(ctx) != 0:
        return
    entries = load_ptr(ctx, 16)
    if ptr_is_null(entries) == 0:
        free(entries)
    if ptr_eq(global_load_ptr("pcc_gc_relocate_slot_pairs_ctx"), ctx) != 0:
        global_store_ptr("pcc_gc_relocate_slot_pairs_ctx", null())
    free(ctx)


@c_abi_export("pcc_gc_relocation_payload_count_slot")
def _relocate_count_slot(slot, role: i64, context) -> None:
    ctx = global_load_ptr("pcc_gc_relocate_slot_pairs_ctx")
    if ptr_is_null(ctx) == 0:
        store_i64(ctx, 24, load_i64(ctx, 24) + 1)


@c_abi_export("pcc_gc_relocation_payload_from_slot")
def _relocate_from_slot(slot, role: i64, context) -> None:
    ctx = global_load_ptr("pcc_gc_relocate_slot_pairs_ctx")
    if ptr_is_null(ctx) != 0:
        return
    index: i64 = load_i64(ctx, 32)
    count: i64 = load_i64(ctx, 24)
    if index >= count:
        store_i32(ctx, 40, 0)
        return
    entries = load_ptr(ctx, 16)
    entry = ptr_add(entries, index * 24)
    store_ptr(entry, 0, slot)
    store_i32(entry, 8, role)
    from_obj = load_ptr(ctx, 0)
    to_obj = load_ptr(ctx, 8)
    inline_offset: i64 = ptr_diff(slot, from_obj)
    object_size: i64 = load_i64(ctx, 48)
    if role == 1 and inline_offset >= 0 and inline_offset + 8 <= object_size:
        store_ptr(to_obj, inline_offset, null())
    store_i64(ctx, 32, index + 1)


@c_abi_export("pcc_gc_relocation_payload_to_slot")
def _relocate_to_slot(slot, role: i64, context) -> None:
    ctx = global_load_ptr("pcc_gc_relocate_slot_pairs_ctx")
    if ptr_is_null(ctx) != 0:
        return
    index: i64 = load_i64(ctx, 32)
    count: i64 = load_i64(ctx, 24)
    if index >= count:
        store_i32(ctx, 40, 0)
        return
    entries = load_ptr(ctx, 16)
    entry = ptr_add(entries, index * 24)
    if load_i32(entry, 8) != role:
        store_i32(ctx, 40, 0)
    store_ptr(entry, 16, slot)
    store_i64(ctx, 32, index + 1)


@c_abi_export("pcc_gc_relocation_payload_slot_pairs_prepare")
def _relocate_slot_pairs_prepare(from_obj, to_obj, size: i64):
    ctx = malloc(56)
    if ptr_is_null(ctx) != 0:
        return null()
    memset(ctx, 0, 56)
    store_ptr(ctx, 0, from_obj)
    store_ptr(ctx, 8, to_obj)
    store_i64(ctx, 48, size)
    store_i32(ctx, 40, 1)
    global_store_ptr("pcc_gc_relocate_slot_pairs_ctx", ctx)
    if pcc_gc_visit_object_slots(from_obj, _relocate_count_slot, null()) == 0:
        _relocate_slot_pairs_dispose(ctx)
        return null()
    count: i64 = load_i64(ctx, 24)
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
    if pcc_gc_visit_object_slots(from_obj, _relocate_from_slot, null()) == 0:
        _relocate_slot_pairs_dispose(ctx)
        return null()
    if load_i64(ctx, 32) != count or load_i32(ctx, 40) == 0:
        _relocate_slot_pairs_dispose(ctx)
        return null()
    return ctx


@c_abi_export("pcc_gc_relocation_payload_copy_slots")
def _relocate_copy_slots(from_obj, to_obj, ctx) -> i64:
    if ptr_is_null(ctx) != 0:
        return 0
    count: i64 = load_i64(ctx, 24)
    store_i64(ctx, 32, 0)
    store_i32(ctx, 40, 1)
    if pcc_gc_visit_object_slots(to_obj, _relocate_to_slot, null()) == 0:
        return 0
    if load_i64(ctx, 32) != count or load_i32(ctx, 40) == 0:
        return 0
    entries = load_ptr(ctx, 16)
    index: i64 = 0
    while index < count:
        entry = ptr_add(entries, index * 24)
        from_slot = load_ptr(entry, 0)
        role: i64 = load_i32(entry, 8)
        to_slot = load_ptr(entry, 16)
        pcc_gc_backend4_remap_heal_slot(from_slot, 0)
        value = load_ptr(from_slot, 0)
        if ptr_eq(value, from_obj) != 0:
            value = to_obj
        if role == 1:  # _PY_OBJ_SLOT_OWNED
            py_incref(value)
        store_ptr(to_slot, 0, value)
        pcc_gc_backend4_remembered_set_retarget_slot(
            from_obj, to_obj, from_slot, to_slot
        )
        index = index + 1
    return 1


@c_abi_export("pcc_gc_relocation_payload_retarget_continuation_root_slots")
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


@c_abi_export("pcc_gc_relocation_payload_fail")
def _relocate_copy_payload_fail(ctx) -> i64:
    _relocate_slot_pairs_dispose(ctx)
    return 0


@c_abi_export("pcc_gc_relocation_payload_finish")
def _relocate_copy_payload_finish(
    from_obj,
    to_obj,
    tag: i64,
    ctx,
    continuation_src_chunk,
    continuation_dst_chunk,
    continuation_mounted: i64,
) -> i64:
    if _relocate_copy_slots(from_obj, to_obj, ctx) == 0:
        return _relocate_copy_payload_fail(ctx)
    if tag == abi_constant("object.type.weakref"):
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
        tag == abi_constant("object.type.continuation")
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


@c_abi_export("pcc_gc_relocate_copy_payload")
def pcc_gc_relocate_copy_payload(from_obj, to_obj, tag: i64, size: i64) -> i64:
    ctx = _relocate_slot_pairs_prepare(from_obj, to_obj, size)
    if ptr_is_null(ctx) != 0:
        return 0
    continuation_src_chunk = null()
    continuation_dst_chunk = null()

    if tag == abi_constant("object.type.continuation"):
        src_chunk = load_ptr(from_obj, 24)
        mounted: i64 = load_i64(from_obj, 32)
        continuation_src_chunk = src_chunk
        store_ptr(to_obj, 24, null())
        if ptr_is_null(src_chunk) != 0:
            return _relocate_copy_payload_finish(
                from_obj, to_obj, tag, ctx, null(), null(), mounted
            )
        n_slots: i64 = load_i64(src_chunk, 8)
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
                to_obj, dst_slots, n_slots * 8
            )
        store_ptr(to_obj, 24, dst_chunk)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, src_chunk, dst_chunk, mounted
        )

    if tag == abi_constant("object.type.exc"):
        traceback = load_ptr(from_obj, 48)
        n_frames: i64 = load_i32(from_obj, 56)
        cap_frames: i64 = load_i32(from_obj, 60)
        store_ptr(to_obj, 48, null())
        store_i32(to_obj, 56, 0)
        store_i32(to_obj, 60, 0)
        if n_frames < 0 or cap_frames < 0 or n_frames > cap_frames:
            return _relocate_copy_payload_fail(ctx)
        if cap_frames > 0 and ptr_is_null(traceback) != 0:
            return _relocate_copy_payload_fail(ctx)
        if cap_frames > 288230376151711743:
            return _relocate_copy_payload_fail(ctx)
        if cap_frames > 0:
            copied_traceback = malloc(cap_frames * 32)
            if ptr_is_null(copied_traceback) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(copied_traceback, traceback, cap_frames * 32)
            store_ptr(to_obj, 48, copied_traceback)
        store_i32(to_obj, 56, n_frames)
        store_i32(to_obj, 60, cap_frames)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.class"):
        n_bases: i64 = load_i32(
            from_obj, abi_constant("object.class.n_bases_offset")
        )
        bases = load_ptr(from_obj, abi_constant("object.class.bases_offset"))
        n_mro: i64 = load_i32(
            from_obj, abi_constant("object.class.n_mro_offset")
        )
        mro = load_ptr(from_obj, abi_constant("object.class.mro_offset"))
        n_methods: i64 = load_i32(
            from_obj, abi_constant("object.class.n_methods_offset")
        )
        methods = load_ptr(from_obj, abi_constant("object.class.methods_offset"))
        n_fields: i64 = load_i32(
            from_obj, abi_constant("object.class.n_fields_offset")
        )
        field_names = load_ptr(
            from_obj, abi_constant("object.class.field_names_offset")
        )
        store_i32(to_obj, abi_constant("object.class.n_bases_offset"), 0)
        store_ptr(to_obj, abi_constant("object.class.bases_offset"), null())
        store_i32(to_obj, abi_constant("object.class.n_mro_offset"), 0)
        store_ptr(to_obj, abi_constant("object.class.mro_offset"), null())
        store_i32(to_obj, abi_constant("object.class.n_methods_offset"), 0)
        store_ptr(to_obj, abi_constant("object.class.methods_offset"), null())
        store_i32(to_obj, abi_constant("object.class.n_fields_offset"), 0)
        store_ptr(to_obj, abi_constant("object.class.field_names_offset"), null())
        store_ptr(to_obj, abi_constant("object.class.attrs_offset"), null())
        if n_bases < 0 or n_mro < 0 or n_methods < 0 or n_fields < 0:
            return _relocate_copy_payload_fail(ctx)
        if n_bases > 1152921504606846975 or n_mro > 1152921504606846975:
            return _relocate_copy_payload_fail(ctx)
        if n_methods > 576460752303423487 or n_fields > 1152921504606846975:
            return _relocate_copy_payload_fail(ctx)
        if n_bases > 0:
            if ptr_is_null(bases) != 0:
                return _relocate_copy_payload_fail(ctx)
            bases_copy = malloc(n_bases * abi_constant("object.pointer.size"))
            if ptr_is_null(bases_copy) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(
                bases_copy,
                bases,
                n_bases * abi_constant("object.pointer.size"),
            )
            store_ptr(to_obj, abi_constant("object.class.bases_offset"), bases_copy)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj, bases_copy, n_bases * abi_constant("object.pointer.size")
            )
        if n_mro > 0:
            if ptr_is_null(mro) != 0:
                return _relocate_copy_payload_fail(ctx)
            mro_copy = malloc(n_mro * abi_constant("object.pointer.size"))
            if ptr_is_null(mro_copy) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(mro_copy, mro, n_mro * abi_constant("object.pointer.size"))
            store_ptr(to_obj, abi_constant("object.class.mro_offset"), mro_copy)
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj, mro_copy, n_mro * abi_constant("object.pointer.size")
            )
        if n_methods > 0:
            if ptr_is_null(methods) != 0:
                return _relocate_copy_payload_fail(ctx)
            methods_copy = malloc(
                n_methods * abi_constant("object.class_method.size")
            )
            if ptr_is_null(methods_copy) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(
                methods_copy,
                methods,
                n_methods * abi_constant("object.class_method.size"),
            )
            store_ptr(
                to_obj, abi_constant("object.class.methods_offset"), methods_copy
            )
            pcc_gc_backend4_zpage_register_owner_payload_span(
                to_obj,
                methods_copy,
                n_methods * abi_constant("object.class_method.size"),
            )
        if n_fields > 0:
            if ptr_is_null(field_names) != 0:
                return _relocate_copy_payload_fail(ctx)
            field_names_copy = malloc(
                n_fields * abi_constant("object.pointer.size")
            )
            if ptr_is_null(field_names_copy) != 0:
                return _relocate_copy_payload_fail(ctx)
            memmove(
                field_names_copy,
                field_names,
                n_fields * abi_constant("object.pointer.size"),
            )
            store_ptr(
                to_obj,
                abi_constant("object.class.field_names_offset"),
                field_names_copy,
            )
        store_i32(to_obj, abi_constant("object.class.n_bases_offset"), n_bases)
        store_i32(to_obj, abi_constant("object.class.n_mro_offset"), n_mro)
        store_i32(to_obj, abi_constant("object.class.n_methods_offset"), n_methods)
        store_i32(to_obj, abi_constant("object.class.n_fields_offset"), n_fields)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.weakref"):
        store_ptr(to_obj, 32, null())
        store_ptr(to_obj, 40, null())
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.thread"):
        handle = load_ptr(from_obj, 16)
        if ptr_is_null(handle) == 0:
            return _relocate_copy_payload_fail(ctx)
        store_ptr(to_obj, 16, null())
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.task"):
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.virtual_thread"):
        queued: i64 = load_i64(
            from_obj, abi_constant("object.virtual_thread.queued_offset")
        )
        # Rollback must never observe raw scheduler entries copied from the
        # source, even when the active-wait validation below rejects moving it.
        store_i64(
            to_obj, abi_constant("object.virtual_thread.queued_offset"), 0
        )
        store_ptr(
            to_obj,
            abi_constant("object.virtual_thread.timer_entry_offset"),
            null(),
        )
        store_ptr(
            to_obj,
            abi_constant("object.virtual_thread.io_entry_offset"),
            null(),
        )
        store_ptr(
            to_obj,
            abi_constant("object.virtual_thread.join_waiters_offset"),
            null(),
        )
        store_ptr(
            to_obj,
            abi_constant("object.virtual_thread.join_wait_tail_offset"),
            null(),
        )
        store_ptr(
            to_obj,
            abi_constant("object.virtual_thread.join_entry_offset"),
            null(),
        )
        store_i64(
            to_obj, abi_constant("object.virtual_thread.wait_kind_offset"), 0
        )
        store_ptr(
            to_obj,
            abi_constant("object.virtual_thread.channel_arm_a_offset"),
            null(),
        )
        store_ptr(
            to_obj,
            abi_constant("object.virtual_thread.channel_arm_b_offset"),
            null(),
        )
        if queued != 0:
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(load_ptr(
            from_obj, abi_constant("object.virtual_thread.timer_entry_offset")
        )) == 0:
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(load_ptr(
            from_obj, abi_constant("object.virtual_thread.io_entry_offset")
        )) == 0:
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(load_ptr(
            from_obj, abi_constant("object.virtual_thread.join_waiters_offset")
        )) == 0:
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(load_ptr(
            from_obj, abi_constant("object.virtual_thread.join_wait_tail_offset")
        )) == 0:
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(load_ptr(
            from_obj, abi_constant("object.virtual_thread.join_entry_offset")
        )) == 0:
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(load_ptr(
            from_obj,
            abi_constant("object.virtual_thread.channel_arm_a_offset"),
        )) == 0:
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(load_ptr(
            from_obj,
            abi_constant("object.virtual_thread.channel_arm_b_offset"),
        )) == 0:
            return _relocate_copy_payload_fail(ctx)
        if load_i64(
            from_obj, abi_constant("object.virtual_thread.wait_kind_offset")
        ) != 0:
            return _relocate_copy_payload_fail(ctx)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.vthread_channel"):
        kind: i64 = load_i64(
            from_obj, abi_constant("object.vthread_channel.core.kind_offset")
        )
        if kind == 0:
            store_ptr(
                to_obj,
                abi_constant("object.vthread_channel.core.send_head_offset"),
                null(),
            )
            store_ptr(
                to_obj,
                abi_constant("object.vthread_channel.core.send_tail_offset"),
                null(),
            )
            store_ptr(
                to_obj,
                abi_constant("object.vthread_channel.core.recv_head_offset"),
                null(),
            )
            store_ptr(
                to_obj,
                abi_constant("object.vthread_channel.core.recv_tail_offset"),
                null(),
            )
            capacity: i64 = load_i64(
                from_obj,
                abi_constant("object.vthread_channel.core.capacity_offset"),
            )
            core_size: i64 = abi_constant("object.vthread_channel.core.size")
            if capacity < 0 or capacity > 1048576:
                return _relocate_copy_payload_fail(ctx)
            if size < core_size + capacity * abi_constant("object.pointer.size"):
                return _relocate_copy_payload_fail(ctx)
            if ptr_is_null(load_ptr(
                from_obj,
                abi_constant("object.vthread_channel.core.send_head_offset"),
            )) == 0:
                return _relocate_copy_payload_fail(ctx)
            if ptr_is_null(load_ptr(
                from_obj,
                abi_constant("object.vthread_channel.core.send_tail_offset"),
            )) == 0:
                return _relocate_copy_payload_fail(ctx)
            if ptr_is_null(load_ptr(
                from_obj,
                abi_constant("object.vthread_channel.core.recv_head_offset"),
            )) == 0:
                return _relocate_copy_payload_fail(ctx)
            if ptr_is_null(load_ptr(
                from_obj,
                abi_constant("object.vthread_channel.core.recv_tail_offset"),
            )) == 0:
                return _relocate_copy_payload_fail(ctx)
            if load_i64(
                from_obj,
                abi_constant("object.vthread_channel.core.flags_offset"),
            ) != 0:
                return _relocate_copy_payload_fail(ctx)
        elif kind == 1 or kind == 2:
            if size < abi_constant("object.vthread_channel.endpoint.size"):
                return _relocate_copy_payload_fail(ctx)
        else:
            return _relocate_copy_payload_fail(ctx)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.memoryview"):
        # The raw Py_buffer allocation is single-owner state, not a GC slot.
        # Keep it on the source until the forwarding transaction commits;
        # otherwise a later install-forwarding failure would either double
        # free it through the destination or leave the source dangling.
        store_ptr(to_obj, 24, null())
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if pcc_capi_is_cext_type_tag(tag) != 0:
        return _relocate_copy_payload_fail(ctx)

    if (
        tag == abi_constant("object.type.instance")
        or tag >= abi_constant("object.type.user_class_start")
    ):
        cls = pcc_gc_load_ptr(
            from_obj,
            ptr_add(from_obj, abi_constant("object.instance.cls_offset")),
        )
        if size < abi_constant("object.instance.size"):
            return _relocate_copy_payload_fail(ctx)
        if ptr_is_null(cls) != 0:
            return _relocate_copy_payload_fail(ctx)
        if load_i32(
            cls, abi_constant("object.header.type_tag_offset")
        ) != abi_constant("object.type.class"):
            return _relocate_copy_payload_fail(ctx)
        n_fields: i64 = load_i32(
            cls, abi_constant("object.class.n_fields_offset")
        )
        if n_fields < 0:
            n_fields: i64 = 0
        n_slots: i64 = n_fields
        class_flags: i64 = load_i32(
            cls, abi_constant("object.header.flags_offset")
        )
        if (class_flags & abi_constant("object.flag.gc_tracked")) == 0:
            n_slots = n_slots + 1
        if n_slots < 0:
            return _relocate_copy_payload_fail(ctx)
        if size < (
            abi_constant("object.instance.fields_offset")
            + n_slots * abi_constant("object.pointer.size")
        ):
            return _relocate_copy_payload_fail(ctx)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.dict"):
        dict_size: i64 = load_i64(from_obj, 16)
        capacity: i64 = load_i64(from_obj, 24)
        src_indices = load_ptr(from_obj, 32)
        src_entries = load_ptr(from_obj, 40)
        entries_used: i64 = load_i64(from_obj, 48)
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

    if tag == abi_constant("object.type.set"):
        set_size: i64 = load_i64(from_obj, 16)
        capacity: i64 = load_i64(from_obj, 24)
        fill: i64 = load_i64(from_obj, 32)
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

    if tag == abi_constant("object.type.tuple"):
        length: i64 = load_i64(from_obj, 16)
        if length < 0:
            return _relocate_copy_payload_fail(ctx)
        if size < 24 + length * 8:
            return _relocate_copy_payload_fail(ctx)
        return _relocate_copy_payload_finish(
            from_obj, to_obj, tag, ctx, null(), null(), 0
        )

    if tag == abi_constant("object.type.list"):
        length: i64 = load_i64(from_obj, 16)
        capacity: i64 = load_i64(from_obj, 24)
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
