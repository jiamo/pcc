"""Backend 3 root promotion and budgeted young-list scheduling."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import is_tagged_int, load_i32, load_ptr, ptr_eq, ptr_is_null


__pcc_freestanding__ = True


pcc_gc_backend3_drain_remembered_owners = extern(
    "pcc_gc_backend3_drain_remembered_owners", (c_int64,), c_int64
)
pcc_gc_backend3_young_link_head = extern(
    "pcc_gc_backend3_young_link_head", (c_ptr,), c_void
)
pcc_gc_backend3_young_list_head = extern(
    "pcc_gc_backend3_young_list_head", (), c_ptr
)
pcc_gc_backend3_young_unlink = extern(
    "pcc_gc_backend3_young_unlink", (c_ptr,), c_void
)
pcc_gc_forwarding_find = extern("pcc_gc_forwarding_find", (c_ptr,), c_ptr)
pcc_gc_generational_oldify_copy = extern(
    "pcc_gc_generational_oldify_copy", (c_ptr,), c_ptr
)
pcc_gc_generational_promote_young_if_known = extern(
    "pcc_gc_generational_promote_young_if_known", (c_ptr,), c_void
)
pcc_gc_object_node_is_active = extern(
    "pcc_gc_object_node_is_active", (c_ptr,), c_int64
)
pcc_gc_trace_referents_for_promotion = extern(
    "pcc_gc_trace_referents_for_promotion", (c_ptr,), c_void
)
pcc_gc_visit_registered_root_slots = extern(
    "pcc_gc_visit_registered_root_slots", (c_int64, c_int64), c_int64
)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_tls_exc_get = extern("py_tls_exc_get", (), c_ptr)
py_tls_exc_set = extern("py_tls_exc_set", (c_ptr,), c_void)


@c_abi_export("pcc_gc_generational_promote_frame_roots")
def pcc_gc_generational_promote_frame_roots(remaining_budget: i64) -> None:
    if remaining_budget <= 0:
        return
    pcc_gc_visit_registered_root_slots(2, 0)  # _PY_ROOT_VISIT_PROMOTE


@c_abi_export("pcc_gc_generational_promote_tls_exception_root")
def pcc_gc_generational_promote_tls_exception_root() -> None:
    current = py_tls_exc_get()
    if ptr_is_null(current) != 0 or is_tagged_int(current) != 0:
        return
    oldified = pcc_gc_generational_oldify_copy(current)
    if ptr_is_null(oldified) == 0:
        if ptr_eq(oldified, current) == 0:
            py_incref(oldified)
            py_tls_exc_set(oldified)
            pcc_gc_trace_referents_for_promotion(oldified)
            py_decref(current)
            return
    pcc_gc_generational_promote_young_if_known(current)


@c_abi_export("pcc_gc_generational_step")
def pcc_gc_generational_step(
    remaining_budget: i64, promote_all_young: i64
) -> i64:
    if remaining_budget <= 0:
        return 0
    pcc_py_gc_minor_graph_lock()
    pcc_gc_generational_promote_frame_roots(remaining_budget)
    pcc_gc_generational_promote_tls_exception_root()
    local_processed: i64 = 0
    local_processed = local_processed + pcc_gc_backend3_drain_remembered_owners(
        remaining_budget - local_processed
    )
    if promote_all_young != 0:
        while (
            ptr_is_null(pcc_gc_backend3_young_list_head()) == 0
            and local_processed < remaining_budget
        ):
            node = pcc_gc_backend3_young_list_head()
            pcc_gc_backend3_young_unlink(node)
            if pcc_gc_object_node_is_active(node) == 0:
                continue
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, 12)
            if (flags & 128) == 0:
                continue
            if ptr_is_null(pcc_gc_forwarding_find(obj)) == 0:
                continue
            pcc_gc_generational_promote_young_if_known(obj)
            after_flags: i64 = load_i32(obj, 12)
            if (
                (after_flags & 128) == 0
                or ptr_is_null(pcc_gc_forwarding_find(obj)) == 0
            ):
                local_processed = local_processed + 1
                if (local_processed & 15) == 0:
                    pcc_thread_safepoint()
            else:
                pcc_gc_backend3_young_link_head(node)
                break
    if local_processed > 0:
        pcc_thread_safepoint()
    pcc_py_gc_minor_graph_unlock()
    return local_processed
