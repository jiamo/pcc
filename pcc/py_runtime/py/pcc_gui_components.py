"""Bounded declarative component registry and keyed atomic render commit.

This module is the canonical pcc-Python owner for the first declarative GUI
component slice.  A registered ``i32(PccGuiRenderContextV1 *)`` callback writes
descriptors into caller-owned storage.  The complete result is validated and
reconciled before the committed kernel tree is touched.  New/replacement nodes
are staged while detached; the kernel then performs one validation-first
sibling-list rewrite.  Any earlier failure discards work, recycles staged
nodes, and leaves the prior tree, node-owner routes, focus, and hover intact.

The v1 slice admits only raw scalar/opaque slots copied into component-owned
records.  Managed Python references remain outside this ABI until the
documented GC0..4 trace/update admission gate is satisfied.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    call_i64_i64,
    call_i32_ptr1,
    calloc,
    cstr,
    define_global_i64,
    free,
    function_addr,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    load_ptr,
    memcpy,
    ptr_add,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


ABI_VERSION = 1
DESCRIPTOR_SIZE = 72
CONTEXT_SIZE = 80
SLOT_SIZE = 24
COMPONENT_SIZE = 96
CALLBACK_SIZE = 16
BINDING_SIZE = 112
EFFECT_SIZE = 48

MAX_DESCRIPTORS = 1024
MAX_EFFECTS = 2048
MAX_STATE_SLOTS = 64

OK = 0
ERR_ABI_VERSION = -100
ERR_CAPACITY = -101
ERR_DUPLICATE_KEY = -102
ERR_INVALID_TRANSITION = -103
ERR_OWNERSHIP = -105
ERR_STALE_NODE = -106
ERR_CALLBACK_FAILED = -116

EFFECT_INSERT = 1
EFFECT_MOVE = 2
EFFECT_UPDATE = 3
EFFECT_REPLACE = 4
EFFECT_REMOVE = 5
EFFECT_PHASE_STRUCTURAL = 2

BINDING_FREE = 0
BINDING_ACTIVE = 1
BINDING_RESERVED = 2


_kit_valid = extern("pcc_kit_is_valid", (c_int64,), c_int32)
_kit_available = extern("pcc_kit_available_nodes", (), c_int64)
_kit_first_child = extern("pcc_kit_first_child", (c_int64,), c_int64)
_kit_create = extern("pcc_kit_create", (c_int64,), c_int64)
_kit_replace_children = extern(
    "pcc_kit_replace_children", (c_int64, c_ptr, c_int64), c_int32
)
_kit_destroy = extern("pcc_kit_destroy_subtree", (c_int64,), c_int64)
_kit_kind = extern("pcc_kit_node_kind", (c_int64, c_int32), c_int32)
_kit_rect = extern(
    "pcc_kit_rect",
    (c_int64, c_int64, c_int64, c_int64, c_int64, c_int32),
    c_void,
)
_kit_text = extern(
    "pcc_kit_text",
    (c_int64, c_int64, c_int64, c_ptr, c_int64, c_int64, c_int32),
    c_void,
)
_kit_scroll_container = extern(
    "pcc_kit_scroll_container", (c_int64, c_int32), c_void
)
_kit_set_removal_hook = extern("pcc_kit_set_removal_hook", (c_ptr,), c_void)
_events_before_commit = extern(
    "pcc_gui_events_before_component_commit",
    (c_int64, c_ptr, c_int32, c_ptr),
    c_int32,
)
_events_abort_commit = extern(
    "pcc_gui_events_abort_component_commit", (c_int64, c_ptr), c_int32
)
_events_after_commit = extern(
    "pcc_gui_events_after_component_commit",
    (c_int64, c_ptr, c_int32, c_ptr),
    c_int32,
)
_events_before_unmount = extern(
    "pcc_gui_events_before_component_unmount", (c_int64, c_ptr), c_int32
)
_events_node_removed = extern(
    "pcc_gui_events_node_removed", (c_int64, c_int64, c_int64), c_int32
)
_scheduler_can_unmount = extern(
    "pcc_gui_scheduler_can_unmount", (c_int64,), c_int32
)
_style_component_unmounted = extern(
    "pcc_gui_style_component_unmounted", (c_int64,), c_void
)
_commands_target_teardown = extern(
    "pcc_gui_commands_target_teardown", (c_int64,), c_int32
)


define_global_i64("pcc_gui_component_records", 0)
define_global_i64("pcc_gui_component_capacity", 0)
define_global_i64("pcc_gui_callback_records", 0)
define_global_i64("pcc_gui_callback_capacity", 0)
define_global_i64("pcc_gui_binding_records", 0)
define_global_i64("pcc_gui_binding_capacity", 0)
define_global_i64("pcc_gui_handle_retain_callback", 0)
define_global_i64("pcc_gui_handle_release_callback", 0)


def _base(name: str) -> int:
    if name == "pcc_gui_component_records":
        return load_i64(global_addr("pcc_gui_component_records"), 0)
    if name == "pcc_gui_component_capacity":
        return load_i64(global_addr("pcc_gui_component_capacity"), 0)
    if name == "pcc_gui_callback_records":
        return load_i64(global_addr("pcc_gui_callback_records"), 0)
    if name == "pcc_gui_callback_capacity":
        return load_i64(global_addr("pcc_gui_callback_capacity"), 0)
    if name == "pcc_gui_binding_records":
        return load_i64(global_addr("pcc_gui_binding_records"), 0)
    if name == "pcc_gui_binding_capacity":
        return load_i64(global_addr("pcc_gui_binding_capacity"), 0)
    if name == "pcc_gui_handle_retain_callback":
        return load_i64(global_addr("pcc_gui_handle_retain_callback"), 0)
    if name == "pcc_gui_handle_release_callback":
        return load_i64(global_addr("pcc_gui_handle_release_callback"), 0)
    return 0


def _component_at(index: int):
    return int_to_ptr(_base("pcc_gui_component_records") + index * COMPONENT_SIZE)


def _callback_at(index: int):
    return int_to_ptr(_base("pcc_gui_callback_records") + index * CALLBACK_SIZE)


def _binding_at(index: int):
    return int_to_ptr(_base("pcc_gui_binding_records") + index * BINDING_SIZE)


def _component_generation(component_id: int) -> int:
    return (component_id >> 32) & 0x7FFFFFFF


def _component_slot(component_id: int) -> int:
    return component_id & 0xFFFFFFFF


def _component_index(component_id: int) -> int:
    if component_id < 0:
        return -1
    index = _component_slot(component_id)
    cap = _base("pcc_gui_component_capacity")
    if index < 0 or index >= cap:
        return -1
    record = _component_at(index)
    if load_i32(record, 88) == 0:
        return -1
    if load_i64(record, 72) != _component_generation(component_id):
        return -1
    if load_i64(record, 0) != component_id:
        return -1
    return index


def _callback_index(callback_id: int) -> int:
    cap = _base("pcc_gui_callback_capacity")
    i = 0
    while i < cap:
        record = _callback_at(i)
        if load_i32(record, 0) != 0 and load_i32(record, 4) == callback_id:
            return i
        i = i + 1
    return -1


def _binding_index(component_id: int, key: int) -> int:
    cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < cap:
        record = _binding_at(i)
        if (
            load_i32(record, 0) == BINDING_ACTIVE
            and load_i64(record, 8) == component_id
            and load_i64(record, 16) == key
        ):
            return i
        i = i + 1
    return -1


def _binding_owner_for_node_exact(node_id: int) -> int:
    cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < cap:
        record = _binding_at(i)
        if (
            load_i32(record, 0) == BINDING_ACTIVE
            and load_i64(record, 24) == node_id
        ):
            return load_i64(record, 8)
        i = i + 1
    return -1


def _free_binding_index() -> int:
    cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < cap:
        if load_i32(_binding_at(i), 0) == BINDING_FREE:
            return i
        i = i + 1
    return -1


def _active_binding_count(component_id: int) -> int:
    cap = _base("pcc_gui_binding_capacity")
    total = 0
    i = 0
    while i < cap:
        record = _binding_at(i)
        if (
            load_i32(record, 0) == BINDING_ACTIVE
            and load_i64(record, 8) == component_id
        ):
            total = total + 1
        i = i + 1
    return total


def _free_binding_count() -> int:
    cap = _base("pcc_gui_binding_capacity")
    total = 0
    i = 0
    while i < cap:
        if load_i32(_binding_at(i), 0) == BINDING_FREE:
            total = total + 1
        i = i + 1
    return total


def _clear_binding(index: int) -> None:
    record = _binding_at(index)
    store_i32(record, 0, BINDING_FREE)
    store_i32(record, 4, 0)
    store_i64(record, 8, 0)
    store_i64(record, 16, 0)
    store_i64(record, 24, -1)
    store_i64(record, 32, -1)


def _descriptor(arena, index: int):
    return ptr_add(arena, index * DESCRIPTOR_SIZE)


def _descriptor_key_present(arena, count: int, component_id: int, key: int) -> int:
    i = 0
    while i < count:
        descriptor = _descriptor(arena, i)
        if load_i64(descriptor, 0) == component_id and load_i64(descriptor, 8) == key:
            return 1
        i = i + 1
    return 0


def _descriptor_equal(binding, descriptor) -> int:
    if load_i32(binding, 4) != load_i32(descriptor, 16):
        return 0
    if load_i32(binding, 60) != load_i32(descriptor, 20):
        return 0
    if load_i64(binding, 64) != load_i64(descriptor, 24):
        return 0
    if load_i64(binding, 72) != load_i64(descriptor, 32):
        return 0
    if load_i64(binding, 80) != load_i64(descriptor, 40):
        return 0
    if load_i64(binding, 88) != load_i64(descriptor, 48):
        return 0
    if load_i64(binding, 96) != load_i64(descriptor, 56):
        return 0
    if load_i64(binding, 104) != load_i64(descriptor, 64):
        return 0
    return 1


def _publish_binding(
    index: int, component_id: int, node_id: int, order: int, descriptor
) -> None:
    record = _binding_at(index)
    store_i32(record, 4, load_i32(descriptor, 16))
    store_i64(record, 8, component_id)
    store_i64(record, 16, load_i64(descriptor, 8))
    store_i64(record, 24, node_id)
    store_i64(record, 32, order)
    memcpy(ptr_add(record, 40), descriptor, DESCRIPTOR_SIZE)
    store_i32(record, 0, BINDING_ACTIVE)


def _set_error(error_out, code: int, phase: int, subject_id: int) -> int:
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, code)
        store_i32(error_out, 4, phase)
        store_i64(error_out, 8, subject_id)
        if code == ERR_ABI_VERSION:
            store_ptr(error_out, 16, cstr("gui component ABI version mismatch"))
        elif code == ERR_CAPACITY:
            store_ptr(error_out, 16, cstr("gui component arena capacity exceeded"))
        elif code == ERR_DUPLICATE_KEY:
            store_ptr(error_out, 16, cstr("duplicate gui child or callback key"))
        elif code == ERR_INVALID_TRANSITION:
            store_ptr(error_out, 16, cstr("invalid gui component transition"))
        elif code == ERR_OWNERSHIP:
            store_ptr(error_out, 16, cstr("invalid gui component ownership"))
        elif code == ERR_STALE_NODE:
            store_ptr(error_out, 16, cstr("stale gui component or node id"))
        elif code == ERR_CALLBACK_FAILED:
            store_ptr(error_out, 16, cstr("gui component callback failed"))
        else:
            store_i64(error_out, 16, 0)
    return code


def _clear_error(error_out) -> None:
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, OK)
        store_i32(error_out, 4, 0)
        store_i64(error_out, 8, 0)
        store_i64(error_out, 16, 0)


def _release_slot_buffer(slots, count: int) -> None:
    release_callback = _base("pcc_gui_handle_release_callback")
    i = 0
    while i < count:
        slot = ptr_add(slots, i * SLOT_SIZE)
        if load_i32(slot, 0) == 2:
            value = load_i64(slot, 8)
            if value != 0 and release_callback != 0:
                call_i64_i64(int_to_ptr(release_callback), value)
            store_i64(slot, 8, 0)
        i = i + 1


def _retain_slot_buffer(slots, count: int) -> int:
    retain_callback = _base("pcc_gui_handle_retain_callback")
    release_callback = _base("pcc_gui_handle_release_callback")
    i = 0
    while i < count:
        slot = ptr_add(slots, i * SLOT_SIZE)
        kind = load_i32(slot, 0)
        if kind != 1 and kind != 2:
            _release_slot_buffer(slots, i)
            return ERR_OWNERSHIP
        if kind == 2:
            value = load_i64(slot, 8)
            if value != 0:
                if retain_callback == 0 or release_callback == 0:
                    _release_slot_buffer(slots, i)
                    return ERR_OWNERSHIP
                retained = call_i64_i64(int_to_ptr(retain_callback), value)
                if retained == 0:
                    _release_slot_buffer(slots, i)
                    return ERR_OWNERSHIP
                store_i64(slot, 8, retained)
        i = i + 1
    return OK


def _copy_slots(source, count: int):
    if count == 0:
        return int_to_ptr(0)
    result = calloc(count, SLOT_SIZE)
    if ptr_is_null(result):
        return result
    memcpy(result, source, count * SLOT_SIZE)
    if _retain_slot_buffer(result, count) != OK:
        free(result)
        return int_to_ptr(0)
    return result


def _validate_slot_source(source, count: int) -> int:
    i = 0
    while i < count:
        slot = ptr_add(source, i * SLOT_SIZE)
        kind = load_i32(slot, 0)
        if kind != 1 and kind != 2:
            return ERR_OWNERSHIP
        if kind == 2 and load_i64(slot, 8) != 0:
            if (
                _base("pcc_gui_handle_retain_callback") == 0
                or _base("pcc_gui_handle_release_callback") == 0
            ):
                return ERR_OWNERSHIP
        i = i + 1
    return OK


def _release_component_storage(record) -> None:
    props = load_ptr(record, 32)
    state = load_ptr(record, 48)
    if not ptr_is_null(props):
        _release_slot_buffer(props, load_i32(record, 40))
        free(props)
    if not ptr_is_null(state):
        _release_slot_buffer(state, load_i32(record, 44))
        free(state)
    store_i64(record, 32, 0)
    store_i64(record, 48, 0)


@c_abi_typed_export("pcc_gui_component_register_handle_ops", "i32", ("ptr", "ptr"))
def pcc_gui_component_register_handle_ops(retain_callback, release_callback) -> int:
    if ptr_is_null(retain_callback) or ptr_is_null(release_callback):
        return ERR_OWNERSHIP
    cap = _base("pcc_gui_component_capacity")
    i = 0
    while i < cap:
        if load_i32(_component_at(i), 88) != 0:
            return ERR_INVALID_TRANSITION
        i = i + 1
    store_i64(
        global_addr("pcc_gui_handle_retain_callback"),
        0,
        ptr_to_int(retain_callback),
    )
    store_i64(
        global_addr("pcc_gui_handle_release_callback"),
        0,
        ptr_to_int(release_callback),
    )
    return OK


@c_abi_typed_export("pcc_gui_component_handle_retain", "i64", ("i64",))
def pcc_gui_component_handle_retain(handle: int) -> int:
    if handle == 0:
        return 0
    callback = _base("pcc_gui_handle_retain_callback")
    if callback == 0:
        return -1
    return call_i64_i64(int_to_ptr(callback), handle)


@c_abi_typed_export("pcc_gui_component_handle_release", "i64", ("i64",))
def pcc_gui_component_handle_release(handle: int) -> int:
    if handle == 0:
        return 0
    callback = _base("pcc_gui_handle_release_callback")
    if callback == 0:
        return -1
    return call_i64_i64(int_to_ptr(callback), handle)


def _retire_component(index: int) -> None:
    record = _component_at(index)
    component_id = load_i64(record, 0)
    # Retirement is the final authority for component-owned resources.  Event
    # lifecycle validation can fail after the kernel has already destroyed a
    # subtree, so style dependency cleanup cannot live only in that callback.
    # The style owner is idempotent and the normal event path may have already
    # cleared the same records.
    _style_component_unmounted(component_id)
    # Pending resolvers and target-scoped registry/state records must not
    # outlive the component identity they reference.  This call is idempotent
    # even when the command service has not been initialized.
    _commands_target_teardown(component_id)
    _release_component_storage(record)
    cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < cap:
        binding = _binding_at(i)
        if load_i32(binding, 0) != BINDING_FREE and load_i64(binding, 8) == component_id:
            _clear_binding(i)
        i = i + 1
    generation = load_i64(record, 72) + 1
    if generation > 0x7FFFFFFF:
        generation = 1
    store_i64(record, 72, generation)
    store_i32(record, 88, 0)
    store_i64(record, 0, 0)
    store_i64(record, 16, -1)
    store_i64(record, 80, 0)


def _component_is_descendant(component_id: int, ancestor_id: int) -> int:
    current = component_id
    limit = _base("pcc_gui_component_capacity")
    steps = 0
    while current >= 0 and steps <= limit:
        if current == ancestor_id:
            return 1
        index = _component_index(current)
        if index < 0:
            return 0
        current = load_i64(_component_at(index), 8)
        steps = steps + 1
    return 0


def _component_subtree_can_unmount(component_id: int) -> int:
    cap = _base("pcc_gui_component_capacity")
    i = 0
    while i < cap:
        record = _component_at(i)
        if load_i32(record, 88) != 0:
            candidate = load_i64(record, 0)
            if (
                _component_is_descendant(candidate, component_id) != 0
                and _scheduler_can_unmount(candidate) == 0
            ):
                return 0
        i = i + 1
    return 1


@c_abi_typed_export("pcc_gui_components_init", "i32", ("i64", "i64", "i64"))
def pcc_gui_components_init(
    max_components: int, max_callbacks: int, max_bindings: int
) -> int:
    if (
        max_components <= 0
        or max_components > 1024
        or max_callbacks <= 0
        or max_callbacks > 4096
        or max_bindings <= 0
        or max_bindings > 8192
    ):
        return ERR_CAPACITY

    old_components = _base("pcc_gui_component_records")
    if old_components != 0:
        old_cap = _base("pcc_gui_component_capacity")
        i = 0
        while i < old_cap:
            record = _component_at(i)
            if load_i32(record, 88) != 0:
                root = load_i64(record, 16)
                _release_component_storage(record)
                if _kit_valid(root) != 0:
                    _kit_destroy(root)
            i = i + 1
        free(int_to_ptr(old_components))
    old_callbacks = _base("pcc_gui_callback_records")
    if old_callbacks != 0:
        free(int_to_ptr(old_callbacks))
    old_bindings = _base("pcc_gui_binding_records")
    if old_bindings != 0:
        free(int_to_ptr(old_bindings))

    store_i64(global_addr("pcc_gui_component_records"), 0, 0)
    store_i64(global_addr("pcc_gui_callback_records"), 0, 0)
    store_i64(global_addr("pcc_gui_binding_records"), 0, 0)
    store_i64(global_addr("pcc_gui_component_capacity"), 0, 0)
    store_i64(global_addr("pcc_gui_callback_capacity"), 0, 0)
    store_i64(global_addr("pcc_gui_binding_capacity"), 0, 0)

    components = calloc(max_components, COMPONENT_SIZE)
    callbacks = calloc(max_callbacks, CALLBACK_SIZE)
    bindings = calloc(max_bindings, BINDING_SIZE)
    if ptr_is_null(components) or ptr_is_null(callbacks) or ptr_is_null(bindings):
        if not ptr_is_null(components):
            free(components)
        if not ptr_is_null(callbacks):
            free(callbacks)
        if not ptr_is_null(bindings):
            free(bindings)
        return ERR_CAPACITY

    store_i64(
        global_addr("pcc_gui_component_records"), 0, ptr_to_int(components)
    )
    store_i64(global_addr("pcc_gui_callback_records"), 0, ptr_to_int(callbacks))
    store_i64(global_addr("pcc_gui_binding_records"), 0, ptr_to_int(bindings))
    store_i64(global_addr("pcc_gui_component_capacity"), 0, max_components)
    store_i64(global_addr("pcc_gui_callback_capacity"), 0, max_callbacks)
    store_i64(global_addr("pcc_gui_binding_capacity"), 0, max_bindings)

    i = 0
    while i < max_components:
        record = _component_at(i)
        store_i64(record, 16, -1)
        store_i64(record, 56, -1)
        store_i64(record, 64, -1)
        i = i + 1
    i = 0
    while i < max_bindings:
        _clear_binding(i)
        i = i + 1
    _kit_set_removal_hook(function_addr("pcc_gui_component_node_removed"))
    return OK


@c_abi_typed_export("pcc_gui_component_register_render", "i32", ("i32", "ptr"))
def pcc_gui_component_register_render(callback_id: int, callback) -> int:
    if callback_id <= 0 or ptr_is_null(callback):
        return ERR_CALLBACK_FAILED
    if _callback_index(callback_id) >= 0:
        return ERR_DUPLICATE_KEY
    cap = _base("pcc_gui_callback_capacity")
    i = 0
    while i < cap:
        record = _callback_at(i)
        if load_i32(record, 0) == 0:
            store_i32(record, 4, callback_id)
            store_i64(record, 8, ptr_to_int(callback))
            store_i32(record, 0, 1)
            return OK
        i = i + 1
    return ERR_CAPACITY


@c_abi_typed_export(
    "pcc_gui_component_mount",
    "i64",
    ("i64", "i64", "i32", "ptr", "i32", "ptr", "i32"),
)
def pcc_gui_component_mount(
    parent_component_id: int,
    root_node_id: int,
    callback_id: int,
    props,
    props_count: int,
    state,
    state_count: int,
) -> int:
    if parent_component_id >= 0 and _component_index(parent_component_id) < 0:
        return ERR_STALE_NODE
    if _kit_valid(root_node_id) == 0 or _kit_first_child(root_node_id) >= 0:
        return ERR_STALE_NODE
    existing_owner = pcc_gui_component_owner_for_node(root_node_id)
    if existing_owner >= 0 and (
        parent_component_id < 0
        or _binding_owner_for_node_exact(root_node_id) != parent_component_id
    ):
        return ERR_OWNERSHIP
    if _callback_index(callback_id) < 0:
        return ERR_CALLBACK_FAILED
    if (
        props_count < 0
        or props_count > MAX_STATE_SLOTS
        or state_count < 0
        or state_count > MAX_STATE_SLOTS
    ):
        return ERR_CAPACITY
    if (props_count > 0 and ptr_is_null(props)) or (
        state_count > 0 and ptr_is_null(state)
    ):
        return ERR_OWNERSHIP
    if _validate_slot_source(props, props_count) != OK or _validate_slot_source(
        state, state_count
    ) != OK:
        return ERR_OWNERSHIP

    cap = _base("pcc_gui_component_capacity")
    index = -1
    i = 0
    while i < cap:
        if load_i32(_component_at(i), 88) == 0:
            index = i
            break
        i = i + 1
    if index < 0:
        return ERR_CAPACITY

    props_copy = _copy_slots(props, props_count)
    if props_count > 0 and ptr_is_null(props_copy):
        return ERR_CAPACITY
    state_copy = _copy_slots(state, state_count)
    if state_count > 0 and ptr_is_null(state_copy):
        if not ptr_is_null(props_copy):
            free(props_copy)
        return ERR_CAPACITY

    record = _component_at(index)
    generation = load_i64(record, 72)
    component_id = (generation << 32) | index
    store_i64(record, 0, component_id)
    store_i64(record, 8, parent_component_id)
    store_i64(record, 16, root_node_id)
    store_i32(record, 24, callback_id)
    store_i32(record, 28, 1)
    store_ptr(record, 32, props_copy)
    store_i32(record, 40, props_count)
    store_i32(record, 44, state_count)
    store_ptr(record, 48, state_copy)
    store_i64(record, 56, -1)
    store_i64(record, 64, -1)
    store_i64(record, 80, 0)
    store_i32(record, 88, 1)
    return component_id


@c_abi_typed_export("pcc_gui_component_is_valid", "i32", ("i64",))
def pcc_gui_component_is_valid(component_id: int) -> int:
    return 1 if _component_index(component_id) >= 0 else 0


@c_abi_typed_export("pcc_gui_component_parent", "i64", ("i64",))
def pcc_gui_component_parent(component_id: int) -> int:
    index = _component_index(component_id)
    if index < 0:
        return ERR_STALE_NODE
    return load_i64(_component_at(index), 8)


@c_abi_typed_export("pcc_gui_component_listener_for_node", "i64", ("i64",))
def pcc_gui_component_listener_for_node(node_id: int) -> int:
    cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < cap:
        binding = _binding_at(i)
        if (
            load_i32(binding, 0) == BINDING_ACTIVE
            and load_i64(binding, 24) == node_id
        ):
            return load_i64(binding, 104)
        i = i + 1
    return 0


@c_abi_typed_export(
    "pcc_gui_component_binding_owner_for_node", "i64", ("i64",)
)
def pcc_gui_component_binding_owner_for_node(node_id: int) -> int:
    owner = _binding_owner_for_node_exact(node_id)
    if owner >= 0:
        return owner
    return pcc_gui_component_owner_for_node(node_id)


@c_abi_typed_export(
    "pcc_gui_component_listener_is_bound", "i32", ("i64", "i64")
)
def pcc_gui_component_listener_is_bound(
    component_id: int, listener_id: int
) -> int:
    if _component_index(component_id) < 0:
        return ERR_STALE_NODE
    cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < cap:
        binding = _binding_at(i)
        if (
            load_i32(binding, 0) == BINDING_ACTIVE
            and load_i64(binding, 8) == component_id
            and load_i64(binding, 104) == listener_id
        ):
            return 1
        i = i + 1
    return 0


@c_abi_typed_export(
    "pcc_gui_component_clear_listener", "i32", ("i64", "i64")
)
def pcc_gui_component_clear_listener(
    component_id: int, listener_id: int
) -> int:
    if _component_index(component_id) < 0:
        return ERR_STALE_NODE
    cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < cap:
        binding = _binding_at(i)
        if (
            load_i32(binding, 0) == BINDING_ACTIVE
            and load_i64(binding, 8) == component_id
            and load_i64(binding, 104) == listener_id
        ):
            store_i64(binding, 104, 0)
        i = i + 1
    return OK


@c_abi_typed_export("pcc_gui_component_owner_for_node", "i64", ("i64",))
def pcc_gui_component_owner_for_node(node_id: int) -> int:
    if _kit_valid(node_id) == 0:
        return -1
    component_cap = _base("pcc_gui_component_capacity")
    i = 0
    while i < component_cap:
        record = _component_at(i)
        if load_i32(record, 88) != 0 and load_i64(record, 16) == node_id:
            return load_i64(record, 0)
        i = i + 1
    binding_cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < binding_cap:
        binding = _binding_at(i)
        if (
            load_i32(binding, 0) == BINDING_ACTIVE
            and load_i64(binding, 24) == node_id
        ):
            return load_i64(binding, 8)
        i = i + 1
    return -1


@c_abi_typed_export("pcc_gui_component_binding_count", "i64", ("i64",))
def pcc_gui_component_binding_count(component_id: int) -> int:
    if _component_index(component_id) < 0:
        return -1
    return _active_binding_count(component_id)


@c_abi_typed_export("pcc_gui_component_state_count", "i32", ("i64",))
def pcc_gui_component_state_count(component_id: int) -> int:
    index = _component_index(component_id)
    if index < 0:
        return ERR_STALE_NODE
    return load_i32(_component_at(index), 44)


@c_abi_typed_export("pcc_gui_component_state_slot_kind", "i32", ("i64", "i32"))
def pcc_gui_component_state_slot_kind(component_id: int, slot: int) -> int:
    index = _component_index(component_id)
    if index < 0:
        return ERR_STALE_NODE
    record = _component_at(index)
    count = load_i32(record, 44)
    if slot < 0 or slot >= count:
        return ERR_INVALID_TRANSITION
    return load_i32(load_ptr(record, 48), slot * SLOT_SIZE)


@c_abi_typed_export("pcc_gui_component_state_slot_value", "i64", ("i64", "i32"))
def pcc_gui_component_state_slot_value(component_id: int, slot: int) -> int:
    index = _component_index(component_id)
    if index < 0:
        return ERR_STALE_NODE
    record = _component_at(index)
    count = load_i32(record, 44)
    if slot < 0 or slot >= count:
        return ERR_INVALID_TRANSITION
    return load_i64(load_ptr(record, 48), slot * SLOT_SIZE + 8)


@c_abi_typed_export(
    "pcc_gui_component_state_snapshot", "i32", ("i64", "ptr", "i32")
)
def pcc_gui_component_state_snapshot(
    component_id: int, snapshot_out, capacity: int
) -> int:
    index = _component_index(component_id)
    if index < 0:
        return ERR_STALE_NODE
    record = _component_at(index)
    count = load_i32(record, 44)
    if capacity < count or (count > 0 and ptr_is_null(snapshot_out)):
        return ERR_CAPACITY
    if count == 0:
        return 0
    memcpy(snapshot_out, load_ptr(record, 48), count * SLOT_SIZE)
    status = _retain_slot_buffer(snapshot_out, count)
    if status != OK:
        return status
    return count


@c_abi_typed_export("pcc_gui_component_state_discard", "i32", ("ptr", "i32"))
def pcc_gui_component_state_discard(snapshot, count: int) -> int:
    if count < 0 or count > MAX_STATE_SLOTS:
        return ERR_CAPACITY
    if count > 0 and ptr_is_null(snapshot):
        return ERR_OWNERSHIP
    _release_slot_buffer(snapshot, count)
    return OK


@c_abi_typed_export(
    "pcc_gui_component_state_replace_owned", "i32", ("i64", "ptr", "i32")
)
def pcc_gui_component_state_replace_owned(
    component_id: int, owned_snapshot, count: int
) -> int:
    index = _component_index(component_id)
    if index < 0:
        return ERR_STALE_NODE
    record = _component_at(index)
    expected = load_i32(record, 44)
    if count != expected or (count > 0 and ptr_is_null(owned_snapshot)):
        return ERR_INVALID_TRANSITION
    i = 0
    while i < count:
        kind = load_i32(owned_snapshot, i * SLOT_SIZE)
        if kind != 1 and kind != 2:
            return ERR_OWNERSHIP
        if kind == 2 and load_i64(owned_snapshot, i * SLOT_SIZE + 8) != 0:
            if _base("pcc_gui_handle_release_callback") == 0:
                return ERR_OWNERSHIP
        i = i + 1
    state = load_ptr(record, 48)
    _release_slot_buffer(state, count)
    if count > 0:
        memcpy(state, owned_snapshot, count * SLOT_SIZE)
    i = 0
    while i < count:
        if load_i32(owned_snapshot, i * SLOT_SIZE) == 2:
            store_i64(owned_snapshot, i * SLOT_SIZE + 8, 0)
        i = i + 1
    store_i32(record, 28, load_i32(record, 28) | 2)
    return OK


@c_abi_typed_export("pcc_gui_component_node_for_key", "i64", ("i64", "i64"))
def pcc_gui_component_node_for_key(component_id: int, key: int) -> int:
    if _component_index(component_id) < 0:
        return -1
    index = _binding_index(component_id, key)
    if index < 0:
        return -1
    node_id = load_i64(_binding_at(index), 24)
    if _kit_valid(node_id) == 0:
        return -1
    return node_id


@c_abi_typed_export("pcc_gui_component_unmount", "i32", ("i64",))
def pcc_gui_component_unmount(component_id: int) -> int:
    index = _component_index(component_id)
    if index < 0:
        return ERR_STALE_NODE
    if _component_subtree_can_unmount(component_id) == 0:
        return ERR_INVALID_TRANSITION
    root = load_i64(_component_at(index), 16)
    if _kit_valid(root) != 0:
        if _kit_destroy(root) < 0:
            return ERR_STALE_NODE
    else:
        error = stack_alloc(24)
        status = _events_before_unmount(component_id, error)
        _retire_component(index)
        if status != OK:
            return status
    return OK


@c_abi_typed_export("pcc_gui_components_shutdown", "i32", ())
def pcc_gui_components_shutdown() -> int:
    """Unmount every live component and release all component-owned state."""
    if _base("pcc_gui_component_records") == 0:
        return OK
    cap = _base("pcc_gui_component_capacity")
    first_error = OK
    i = 0
    while i < cap:
        record = _component_at(i)
        if load_i32(record, 88) != 0:
            status = pcc_gui_component_unmount(load_i64(record, 0))
            if status != OK and status != ERR_STALE_NODE and first_error == OK:
                first_error = status
        i = i + 1
    # A parent subtree normally retires its children through the kernel hook.
    # Sweep any detached survivors once more so callback errors cannot leak
    # component-owned opaque handles at process shutdown.
    i = 0
    while i < cap:
        if load_i32(_component_at(i), 88) != 0:
            _retire_component(i)
            if first_error == OK:
                first_error = ERR_CALLBACK_FAILED
        i = i + 1
    return first_error


@c_abi_typed_export("pcc_gui_component_node_removed", "i64", ("i64",))
def pcc_gui_component_node_removed(node_id: int) -> int:
    binding_cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < binding_cap:
        binding = _binding_at(i)
        if (
            load_i32(binding, 0) == BINDING_ACTIVE
            and load_i64(binding, 24) == node_id
        ):
            component_id = load_i64(binding, 8)
            listener_id = load_i64(binding, 104)
            _clear_binding(i)
            _events_node_removed(component_id, node_id, listener_id)
            component_index = _component_index(component_id)
            if component_index >= 0:
                record = _component_at(component_index)
                count = load_i64(record, 80)
                if count > 0:
                    store_i64(record, 80, count - 1)
        i = i + 1

    component_cap = _base("pcc_gui_component_capacity")
    i = 0
    while i < component_cap:
        record = _component_at(i)
        if load_i32(record, 88) != 0 and load_i64(record, 16) == node_id:
            component_id = load_i64(record, 0)
            error = stack_alloc(24)
            _events_before_unmount(component_id, error)
            # Subtree destruction is already committed when the kernel calls
            # this hook.  Preserve the lifecycle error in the event owner, but
            # always retire the component so no stale root or owned state
            # survives a callback failure.
            _retire_component(i)
        i = i + 1
    return 0


def _apply_descriptor(node_id: int, descriptor) -> None:
    kind = load_i32(descriptor, 16)
    flags = load_i32(descriptor, 20)
    p0 = load_i64(descriptor, 32)
    p1 = load_i64(descriptor, 40)
    p2 = load_i64(descriptor, 48)
    p3 = load_i64(descriptor, 56)
    _kit_kind(node_id, kind)
    if kind == 2:
        _kit_text(node_id, 0, 0, int_to_ptr(p0), p1, p2, p3)
    else:
        _kit_rect(node_id, p0, p1, p2, p3, flags)
        if kind == 3:
            _kit_scroll_container(node_id, 1)


def _validate_descriptors(component_id: int, arena, count: int) -> int:
    i = 0
    while i < count:
        descriptor = _descriptor(arena, i)
        if load_i64(descriptor, 0) != component_id:
            return ERR_OWNERSHIP
        kind = load_i32(descriptor, 16)
        if kind < 0 or kind > 3:
            return ERR_INVALID_TRANSITION
        key = load_i64(descriptor, 8)
        j = 0
        while j < i:
            if load_i64(_descriptor(arena, j), 8) == key:
                return ERR_DUPLICATE_KEY
            j = j + 1
        i = i + 1
    return OK


def _effect_count(component_id: int, arena, count: int) -> int:
    total = 0
    i = 0
    while i < count:
        descriptor = _descriptor(arena, i)
        binding_index = _binding_index(component_id, load_i64(descriptor, 8))
        if binding_index < 0:
            total = total + 1
        else:
            binding = _binding_at(binding_index)
            if load_i32(binding, 4) != load_i32(descriptor, 16):
                total = total + 1
            else:
                if load_i64(binding, 32) != i:
                    total = total + 1
                if _descriptor_equal(binding, descriptor) == 0:
                    total = total + 1
        i = i + 1

    binding_cap = _base("pcc_gui_binding_capacity")
    i = 0
    while i < binding_cap:
        binding = _binding_at(i)
        if (
            load_i32(binding, 0) == BINDING_ACTIVE
            and load_i64(binding, 8) == component_id
            and _descriptor_key_present(
                arena, count, component_id, load_i64(binding, 16)
            )
            == 0
        ):
            total = total + 1
        i = i + 1
    return total


def _needed_insert_bindings(component_id: int, arena, count: int) -> int:
    total = 0
    i = 0
    while i < count:
        descriptor = _descriptor(arena, i)
        if _binding_index(component_id, load_i64(descriptor, 8)) < 0:
            total = total + 1
        i = i + 1
    return total


def _needed_new_nodes(component_id: int, arena, count: int) -> int:
    total = 0
    i = 0
    while i < count:
        descriptor = _descriptor(arena, i)
        binding_index = _binding_index(component_id, load_i64(descriptor, 8))
        if binding_index < 0:
            total = total + 1
        elif load_i32(_binding_at(binding_index), 4) != load_i32(descriptor, 16):
            total = total + 1
        i = i + 1
    return total


def _write_effect(
    arena,
    index: int,
    component_id: int,
    node_id: int,
    kind: int,
    payload: int,
) -> None:
    effect = ptr_add(arena, index * EFFECT_SIZE)
    store_i64(effect, 0, index)
    store_i64(effect, 8, component_id)
    store_i64(effect, 16, node_id)
    store_i32(effect, 24, EFFECT_PHASE_STRUCTURAL)
    store_i32(effect, 28, kind)
    store_i32(effect, 32, 0)
    store_i32(effect, 36, 0)
    store_i64(effect, 40, payload)


def _discard_staged(nodes, slots, modes, count: int) -> None:
    i = 0
    while i < count:
        mode = load_i32(modes, i * 4)
        node_id = load_i64(nodes, i * 8)
        if mode != 0 and node_id >= 0 and _kit_valid(node_id) != 0:
            _kit_destroy(node_id)
        if mode == 1:
            binding_index = load_i64(slots, i * 8)
            if binding_index >= 0:
                _clear_binding(binding_index)
        i = i + 1


def _free_work(nodes, slots, modes, retired) -> None:
    if not ptr_is_null(nodes):
        free(nodes)
    if not ptr_is_null(slots):
        free(slots)
    if not ptr_is_null(modes):
        free(modes)
    if not ptr_is_null(retired):
        free(retired)


@c_abi_typed_export(
    "pcc_gui_component_render_commit",
    "i32",
    ("i64", "ptr", "i32", "ptr", "i32", "ptr", "ptr"),
)
def pcc_gui_component_render_commit(
    component_id: int,
    descriptor_arena,
    descriptor_capacity: int,
    effect_arena,
    effect_capacity: int,
    effect_count_out,
    error_out,
) -> int:
    _clear_error(error_out)
    if not ptr_is_null(effect_count_out):
        store_i32(effect_count_out, 0, 0)
    component_index = _component_index(component_id)
    if component_index < 0:
        return _set_error(error_out, ERR_STALE_NODE, 0, component_id)
    if (
        descriptor_capacity < 0
        or descriptor_capacity > MAX_DESCRIPTORS
        or effect_capacity < 0
        or effect_capacity > MAX_EFFECTS
    ):
        return _set_error(error_out, ERR_CAPACITY, 0, component_id)
    if (descriptor_capacity > 0 and ptr_is_null(descriptor_arena)) or (
        effect_capacity > 0 and ptr_is_null(effect_arena)
    ):
        return _set_error(error_out, ERR_OWNERSHIP, 0, component_id)

    component = _component_at(component_index)
    root = load_i64(component, 16)
    if _kit_valid(root) == 0:
        return _set_error(error_out, ERR_STALE_NODE, 0, root)
    callback_index = _callback_index(load_i32(component, 24))
    if callback_index < 0:
        return _set_error(error_out, ERR_CALLBACK_FAILED, 0, component_id)

    descriptor_count_out = stack_alloc(4)
    store_i32(descriptor_count_out, 0, -1)
    # stack_alloc is a compile-time intrinsic; keep the frozen ABI size literal.
    context = stack_alloc(80)
    store_i32(context, 0, ABI_VERSION)
    store_i32(context, 4, 0)
    store_i64(context, 8, component_id)
    store_ptr(context, 16, load_ptr(component, 32))
    store_i32(context, 24, load_i32(component, 40))
    store_i32(context, 28, 0)
    store_ptr(context, 32, load_ptr(component, 48))
    store_i32(context, 40, load_i32(component, 44))
    store_i32(context, 44, 0)
    store_ptr(context, 48, descriptor_arena)
    store_i32(context, 56, descriptor_capacity)
    store_i32(context, 60, 0)
    store_ptr(context, 64, descriptor_count_out)
    store_ptr(context, 72, error_out)

    callback_record = _callback_at(callback_index)
    callback_result = call_i32_ptr1(
        int_to_ptr(load_i64(callback_record, 8)), context
    )
    if callback_result < 0:
        if callback_result <= ERR_ABI_VERSION and callback_result >= ERR_CALLBACK_FAILED:
            return _set_error(error_out, callback_result, 0, component_id)
        return _set_error(error_out, ERR_CALLBACK_FAILED, 0, component_id)
    descriptor_count = load_i32(descriptor_count_out, 0)
    if descriptor_count != callback_result:
        return _set_error(error_out, ERR_CALLBACK_FAILED, 0, component_id)
    if descriptor_count < 0 or descriptor_count > descriptor_capacity:
        return _set_error(error_out, ERR_CAPACITY, 0, component_id)

    status = _validate_descriptors(component_id, descriptor_arena, descriptor_count)
    if status != OK:
        return _set_error(error_out, status, 0, component_id)
    effect_count = _effect_count(component_id, descriptor_arena, descriptor_count)
    if effect_count > effect_capacity:
        return _set_error(error_out, ERR_CAPACITY, EFFECT_PHASE_STRUCTURAL, component_id)
    needed_bindings = _needed_insert_bindings(
        component_id, descriptor_arena, descriptor_count
    )
    if needed_bindings > _free_binding_count():
        return _set_error(error_out, ERR_CAPACITY, EFFECT_PHASE_STRUCTURAL, component_id)
    needed_nodes = _needed_new_nodes(component_id, descriptor_arena, descriptor_count)
    if needed_nodes > _kit_available():
        return _set_error(error_out, ERR_CAPACITY, EFFECT_PHASE_STRUCTURAL, component_id)

    work_count = descriptor_count if descriptor_count > 0 else 1
    retire_count_limit = _active_binding_count(component_id)
    retire_alloc = retire_count_limit if retire_count_limit > 0 else 1
    nodes = calloc(work_count, 8)
    slots = calloc(work_count, 8)
    modes = calloc(work_count, 4)
    retired = calloc(retire_alloc, 8)
    if (
        ptr_is_null(nodes)
        or ptr_is_null(slots)
        or ptr_is_null(modes)
        or ptr_is_null(retired)
    ):
        _free_work(nodes, slots, modes, retired)
        return _set_error(error_out, ERR_CAPACITY, EFFECT_PHASE_STRUCTURAL, component_id)

    i = 0
    while i < descriptor_count:
        store_i64(nodes, i * 8, -1)
        store_i64(slots, i * 8, -1)
        i = i + 1

    # Stage every allocation while detached.  Existing bindings remain the
    # committed owner map until the kernel accepts the complete new order.
    i = 0
    while i < descriptor_count:
        descriptor = _descriptor(descriptor_arena, i)
        key = load_i64(descriptor, 8)
        old_index = _binding_index(component_id, key)
        if old_index < 0:
            binding_index = _free_binding_index()
            if binding_index < 0:
                _discard_staged(nodes, slots, modes, descriptor_count)
                _free_work(nodes, slots, modes, retired)
                return _set_error(
                    error_out, ERR_CAPACITY, EFFECT_PHASE_STRUCTURAL, component_id
                )
            store_i32(_binding_at(binding_index), 0, BINDING_RESERVED)
            store_i64(slots, i * 8, binding_index)
            store_i32(modes, i * 4, 1)
            node_id = _kit_create(-1)
            if node_id < 0:
                _discard_staged(nodes, slots, modes, descriptor_count)
                _free_work(nodes, slots, modes, retired)
                return _set_error(
                    error_out, ERR_CAPACITY, EFFECT_PHASE_STRUCTURAL, component_id
                )
            store_i64(nodes, i * 8, node_id)
            _apply_descriptor(node_id, descriptor)
        else:
            binding = _binding_at(old_index)
            store_i64(slots, i * 8, old_index)
            if load_i32(binding, 4) != load_i32(descriptor, 16):
                store_i32(modes, i * 4, 2)
                node_id = _kit_create(-1)
                if node_id < 0:
                    _discard_staged(nodes, slots, modes, descriptor_count)
                    _free_work(nodes, slots, modes, retired)
                    return _set_error(
                        error_out,
                        ERR_CAPACITY,
                        EFFECT_PHASE_STRUCTURAL,
                        component_id,
                    )
                store_i64(nodes, i * 8, node_id)
                _apply_descriptor(node_id, descriptor)
            else:
                store_i64(nodes, i * 8, load_i64(binding, 24))
        i = i + 1

    # Materialize deterministic effects only after staged node ids exist, but
    # still before the committed sibling list or owner table changes.
    effect_index = 0
    i = 0
    while i < descriptor_count:
        descriptor = _descriptor(descriptor_arena, i)
        binding_index = load_i64(slots, i * 8)
        binding = _binding_at(binding_index)
        mode = load_i32(modes, i * 4)
        node_id = load_i64(nodes, i * 8)
        key = load_i64(descriptor, 8)
        if mode == 1:
            _write_effect(
                effect_arena, effect_index, component_id, node_id, EFFECT_INSERT, key
            )
            effect_index = effect_index + 1
        elif mode == 2:
            _write_effect(
                effect_arena, effect_index, component_id, node_id, EFFECT_REPLACE, key
            )
            effect_index = effect_index + 1
        else:
            if load_i64(binding, 32) != i:
                _write_effect(
                    effect_arena, effect_index, component_id, node_id, EFFECT_MOVE, key
                )
                effect_index = effect_index + 1
            if _descriptor_equal(binding, descriptor) == 0:
                _write_effect(
                    effect_arena,
                    effect_index,
                    component_id,
                    node_id,
                    EFFECT_UPDATE,
                    key,
                )
                effect_index = effect_index + 1
        i = i + 1

    retired_count = 0
    binding_cap = _base("pcc_gui_binding_capacity")
    # Replaced nodes retire in new descriptor order, matching their REPLACE
    # effects above.
    i = 0
    while i < descriptor_count:
        if load_i32(modes, i * 4) == 2:
            binding = _binding_at(load_i64(slots, i * 8))
            store_i64(retired, retired_count * 8, load_i64(binding, 24))
            retired_count = retired_count + 1
        i = i + 1

    # Missing children retire in their prior committed sibling order, not in
    # allocator-slot order.  Holes left by an external node removal are simply
    # skipped.
    old_order = 0
    while old_order < binding_cap:
        i = 0
        while i < binding_cap:
            binding = _binding_at(i)
            if (
                load_i32(binding, 0) == BINDING_ACTIVE
                and load_i64(binding, 8) == component_id
                and load_i64(binding, 32) == old_order
            ):
                key = load_i64(binding, 16)
                if _descriptor_key_present(
                    descriptor_arena, descriptor_count, component_id, key
                ) == 0:
                    old_node = load_i64(binding, 24)
                    store_i64(retired, retired_count * 8, old_node)
                    retired_count = retired_count + 1
                    _write_effect(
                        effect_arena,
                        effect_index,
                        component_id,
                        old_node,
                        EFFECT_REMOVE,
                        key,
                    )
                    effect_index = effect_index + 1
                # There is at most one active binding per committed order.
                i = binding_cap
            else:
                i = i + 1
        old_order = old_order + 1

    if effect_index != effect_count:
        _discard_staged(nodes, slots, modes, descriptor_count)
        _free_work(nodes, slots, modes, retired)
        return _set_error(
            error_out, ERR_INVALID_TRANSITION, EFFECT_PHASE_STRUCTURAL, component_id
        )

    status = _events_before_commit(
        component_id, effect_arena, effect_count, error_out
    )
    if status != OK:
        _discard_staged(nodes, slots, modes, descriptor_count)
        _free_work(nodes, slots, modes, retired)
        return status

    status = _kit_replace_children(root, nodes, descriptor_count)
    if status != 0:
        _events_abort_commit(component_id, error_out)
        _discard_staged(nodes, slots, modes, descriptor_count)
        _free_work(nodes, slots, modes, retired)
        return _set_error(
            error_out, ERR_INVALID_TRANSITION, EFFECT_PHASE_STRUCTURAL, component_id
        )

    # Structural ownership is now committed.  Lifecycle creation and passive
    # scheduling run after this point; their failure is reported as a
    # post-commit callback failure and never rewrites the accepted tree.
    i = 0
    while i < binding_cap:
        binding = _binding_at(i)
        if (
            load_i32(binding, 0) == BINDING_ACTIVE
            and load_i64(binding, 8) == component_id
            and _descriptor_key_present(
                descriptor_arena,
                descriptor_count,
                component_id,
                load_i64(binding, 16),
            )
            == 0
        ):
            _clear_binding(i)
        i = i + 1
    i = 0
    while i < descriptor_count:
        descriptor = _descriptor(descriptor_arena, i)
        node_id = load_i64(nodes, i * 8)
        if load_i32(modes, i * 4) == 0:
            _apply_descriptor(node_id, descriptor)
        _publish_binding(
            load_i64(slots, i * 8), component_id, node_id, i, descriptor
        )
        i = i + 1
    store_i64(component, 80, descriptor_count)

    i = 0
    while i < retired_count:
        old_node = load_i64(retired, i * 8)
        if _kit_valid(old_node) != 0:
            _kit_destroy(old_node)
        i = i + 1

    if not ptr_is_null(effect_count_out):
        store_i32(effect_count_out, 0, effect_count)
    status = _events_after_commit(
        component_id, effect_arena, effect_count, error_out
    )
    _free_work(nodes, slots, modes, retired)
    return status
