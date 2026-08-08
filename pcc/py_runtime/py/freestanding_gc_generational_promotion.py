"""Shared generational slot promotion and stable-root rewriting."""
from pcc import i64
from pcc.py_runtime.py.py_abi_constants import (
    PY_TYPE_MEMORYVIEW,
)

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    is_tagged_int,
    load_i32,
    load_ptr,
    null,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_gc_backend3_young_unlink = extern(
    "pcc_gc_backend3_young_unlink", (c_ptr,), c_void
)
pcc_gc_backend4_zpage_note_owner_promoted = extern(
    "pcc_gc_backend4_zpage_note_owner_promoted", (c_ptr,), c_void
)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)
pcc_gc_forwarding_find = extern("pcc_gc_forwarding_find", (c_ptr,), c_ptr)
pcc_gc_generational_oldify_copy = extern(
    "pcc_gc_generational_oldify_copy", (c_ptr,), c_ptr
)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock", (c_ptr,), c_int64
)
pcc_gc_pointer_is_managed = extern(
    "pcc_gc_pointer_is_managed", (c_ptr,), c_int64
)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_memoryview_refresh_owned_buffer = extern(
    "pcc_gc_memoryview_refresh_owned_buffer", (c_ptr,), c_int64
)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)


@c_abi_export("pcc_gc_generational_pointer_can_have_header")
def pcc_gc_generational_pointer_can_have_header(obj) -> i64:
    return pcc_gc_pointer_is_managed(obj)


@c_abi_export("pcc_gc_generational_promote_young_if_known")
def pcc_gc_generational_promote_young_if_known(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    if pcc_gc_object_is_known_no_lock(obj) == 0:
        return
    flags: i64 = load_i32(obj, 12)
    if (flags & 128) == 0:
        return
    oldified = pcc_gc_generational_oldify_copy(obj)
    if ptr_is_null(oldified) == 0:
        pcc_gc_trace_referents_for_promotion(oldified)
        return
    backend: i64 = pcc_gc_config_ensure()
    if backend == 3 and (load_i32(obj, 12) & 4096) != 0:
        pcc_gc_backend3_young_unlink(pcc_gc_object_index_find(obj))
        promoted_flags: i64 = load_i32(obj, 12)
        store_i32(obj, 12, (promoted_flags & ~(128 | 512)) | 256)
        pcc_gc_trace_referents_for_promotion(obj)
        return
    pcc_gc_backend3_young_unlink(pcc_gc_object_index_find(obj))
    store_i32(obj, 12, (flags & ~128) | 256)
    if backend == 4:
        pcc_gc_backend4_zpage_note_owner_promoted(obj)
    if backend == 3:
        pcc_gc_trace_referents_for_promotion(obj)


@c_abi_export("pcc_gc_generational_promote_owned_slot_mode")
def pcc_gc_generational_promote_owned_slot_mode(
    slot_base, slot_offset: i64, recurse: i64
) -> None:
    child = load_ptr(slot_base, slot_offset)
    if pcc_gc_generational_pointer_can_have_header(child) == 0:
        return
    if (
        pcc_gc_object_is_known_no_lock(child) == 0
        and ptr_is_null(pcc_gc_forwarding_find(child)) != 0
    ):
        return
    child_flags: i64 = load_i32(child, 12)
    if (child_flags & (128 | 2048)) == 0:
        return
    oldified = pcc_gc_generational_oldify_copy(child)
    if ptr_is_null(oldified) == 0 and ptr_eq(oldified, child) == 0:
        py_incref(oldified)
        store_ptr(slot_base, slot_offset, oldified)
        pcc_gc_trace_referents_for_promotion(oldified)
        py_decref(child)
        return
    if recurse == 0:
        return
    pcc_gc_generational_promote_young_if_known(child)


@c_abi_export("pcc_gc_generational_promote_borrowed_slot_mode")
def pcc_gc_generational_promote_borrowed_slot_mode(
    slot_base, slot_offset: i64, recurse: i64
) -> None:
    child = load_ptr(slot_base, slot_offset)
    if pcc_gc_generational_pointer_can_have_header(child) == 0:
        return
    if (
        pcc_gc_object_is_known_no_lock(child) == 0
        and ptr_is_null(pcc_gc_forwarding_find(child)) != 0
    ):
        return
    child_flags: i64 = load_i32(child, 12)
    if (child_flags & (128 | 2048)) == 0:
        return
    oldified = pcc_gc_generational_oldify_copy(child)
    if ptr_is_null(oldified) == 0 and ptr_eq(oldified, child) == 0:
        store_ptr(slot_base, slot_offset, oldified)
        pcc_gc_trace_referents_for_promotion(oldified)
        return
    if recurse == 0:
        return
    pcc_gc_generational_promote_young_if_known(child)


@c_abi_export("pcc_gc_generational_promote_slot")
def pcc_gc_generational_promote_slot(slot, role: i64, context) -> None:
    if role == 1:
        pcc_gc_generational_promote_owned_slot_mode(slot, 0, 1)
    else:
        pcc_gc_generational_promote_borrowed_slot_mode(slot, 0, 1)


@c_abi_export("pcc_gc_generational_promote_shallow_slot")
def pcc_gc_generational_promote_shallow_slot(slot, role: i64, context) -> None:
    if role == 1:
        pcc_gc_generational_promote_owned_slot_mode(slot, 0, 0)
    else:
        pcc_gc_generational_promote_borrowed_slot_mode(slot, 0, 0)


@c_abi_export("pcc_gc_trace_referents_for_promotion_mode")
def pcc_gc_trace_referents_for_promotion_mode(obj, recurse: i64) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    if recurse != 0:
        pcc_gc_visit_object_slots(obj, pcc_gc_generational_promote_slot, null())
    else:
        pcc_gc_visit_object_slots(
            obj, pcc_gc_generational_promote_shallow_slot, null()
        )
    if load_i32(obj, 8) == PY_TYPE_MEMORYVIEW:  # PY_TYPE_MEMORYVIEW
        # Promotion may replace base@16; refresh the non-owning Py_buffer
        # aliases after the shared slot visitor has completed.
        pcc_gc_memoryview_refresh_owned_buffer(obj)


@c_abi_export("pcc_gc_trace_referents_for_promotion")
def pcc_gc_trace_referents_for_promotion(obj) -> None:
    pcc_gc_trace_referents_for_promotion_mode(obj, 1)


@c_abi_export("pcc_gc_generational_root_slot_value_is_stable")
def pcc_gc_generational_root_slot_value_is_stable(value) -> i64:
    if pcc_gc_generational_pointer_can_have_header(value) == 0:
        return 1
    if pcc_gc_object_is_known_no_lock(value) == 0:
        if ptr_is_null(pcc_gc_forwarding_find(value)) != 0:
            return 1
        return 0
    flags: i64 = load_i32(value, 12)
    if (flags & (128 | 2048)) == 0:
        return 1
    return 0


@c_abi_export("pcc_gc_promote_cached_frame_slot")
def pcc_gc_promote_cached_frame_slot(
    slot_base, slot_offset: i64, stable_base, borrowed: i64
) -> None:
    before = load_ptr(slot_base, slot_offset)
    if ptr_is_null(stable_base) == 0:
        if ptr_eq(load_ptr(stable_base, slot_offset), before) != 0:
            return
    if borrowed != 0:
        pcc_gc_generational_promote_borrowed_slot_mode(slot_base, slot_offset, 1)
    else:
        pcc_gc_generational_promote_owned_slot_mode(slot_base, slot_offset, 1)
    if ptr_is_null(stable_base) != 0:
        return
    after = load_ptr(slot_base, slot_offset)
    if pcc_gc_generational_root_slot_value_is_stable(after) != 0:
        store_ptr(stable_base, slot_offset, after)
    else:
        store_ptr(stable_base, slot_offset, null())
