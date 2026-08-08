"""Target-filtered GUI listeners and deterministic component lifecycle.

The kernel owns only painted hit testing and the complete leaf-to-root node
path.  This module is the sole callback-dispatch owner.  Listener records keep
the frozen 40-byte v1 layout; callbacks receive the painted target component
even while the listener itself is selected from a bubbling ancestor node.

Lifecycle callbacks use the frozen ``i32(u64, u32, i64)`` ABI.  Snapshot runs
before mutation, layout cleanup runs synchronously in reverse registration
order, layout creation runs synchronously after structural commit, and passive
cleanup/creation is queued in that order.  Kernel subtree destruction is
child-before-parent, so explicit unmount naturally extends the same ordering
across component ancestry.  Callback errors are recorded and reported without
silently dropping the remaining cleanup records.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    call_i32_i64_i32_i64,
    call_i32_i64_i64_ptr,
    calloc,
    cstr,
    define_global_i64,
    free,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    ptr_is_null,
    ptr_to_int,
    store_i32,
    store_i64,
    store_ptr,
)


LISTENER_SIZE = 40
CALLBACK_SIZE = 24
EFFECT_SIZE = 48
PASSIVE_SIZE = 40

CALLBACK_LISTENER = 1
CALLBACK_EFFECT = 2

LISTENER_ACTIVE = 1
LISTENER_ONCE = 2

PHASE_BEFORE_MUTATION = 0
PHASE_LAYOUT_CLEANUP = 1
PHASE_STRUCTURAL = 2
PHASE_LAYOUT_CREATE = 3
PHASE_PASSIVE_CLEANUP = 4
PHASE_PASSIVE_CREATE = 5
STRUCTURAL_REMOVE = 5

EVENTS_IDLE = 0
EVENTS_DISPATCHING = 1
EVENTS_BEFORE_COMMIT = 2
EVENTS_AFTER_COMMIT = 3
EVENTS_UNMOUNTING = 4
EVENTS_DRAINING = 5

OK = 0
ERR_CAPACITY = -101
ERR_DUPLICATE_KEY = -102
ERR_INVALID_TRANSITION = -103
ERR_OWNERSHIP = -105
ERR_STALE_NODE = -106
ERR_CALLBACK_FAILED = -116


_kit_route = extern(
    "pcc_kit_route_event_v2",
    (c_int64, c_int64, c_int64, c_int64, c_ptr, c_int64),
    c_int64,
)
_component_valid = extern("pcc_gui_component_is_valid", (c_int64,), c_int32)
_component_owner = extern(
    "pcc_gui_component_owner_for_node", (c_int64,), c_int64
)
_component_binding_owner = extern(
    "pcc_gui_component_binding_owner_for_node", (c_int64,), c_int64
)
_component_listener = extern(
    "pcc_gui_component_listener_for_node", (c_int64,), c_int64
)
_component_listener_bound = extern(
    "pcc_gui_component_listener_is_bound", (c_int64, c_int64), c_int32
)
_component_node_for_key = extern(
    "pcc_gui_component_node_for_key", (c_int64, c_int64), c_int64
)
_component_clear_listener = extern(
    "pcc_gui_component_clear_listener", (c_int64, c_int64), c_int32
)
_scheduler_cancel = extern("pcc_gui_scheduler_cancel", (c_int64,), c_int32)
_style_component_unmounted = extern(
    "pcc_gui_style_component_unmounted", (c_int64,), c_void
)


define_global_i64("pcc_gui_event_listeners", 0)
define_global_i64("pcc_gui_event_listener_capacity", 0)
define_global_i64("pcc_gui_event_callbacks", 0)
define_global_i64("pcc_gui_event_callback_capacity", 0)
define_global_i64("pcc_gui_event_effects", 0)
define_global_i64("pcc_gui_event_effect_capacity", 0)
define_global_i64("pcc_gui_event_passive", 0)
define_global_i64("pcc_gui_event_passive_capacity", 0)
define_global_i64("pcc_gui_event_sequence", 1)
define_global_i64("pcc_gui_event_state", 0)
define_global_i64("pcc_gui_event_last_error", 0)


def _base(name: str) -> int:
    if name == "pcc_gui_event_listeners":
        return load_i64(global_addr("pcc_gui_event_listeners"), 0)
    if name == "pcc_gui_event_listener_capacity":
        return load_i64(global_addr("pcc_gui_event_listener_capacity"), 0)
    if name == "pcc_gui_event_callbacks":
        return load_i64(global_addr("pcc_gui_event_callbacks"), 0)
    if name == "pcc_gui_event_callback_capacity":
        return load_i64(global_addr("pcc_gui_event_callback_capacity"), 0)
    if name == "pcc_gui_event_effects":
        return load_i64(global_addr("pcc_gui_event_effects"), 0)
    if name == "pcc_gui_event_effect_capacity":
        return load_i64(global_addr("pcc_gui_event_effect_capacity"), 0)
    if name == "pcc_gui_event_passive":
        return load_i64(global_addr("pcc_gui_event_passive"), 0)
    if name == "pcc_gui_event_passive_capacity":
        return load_i64(global_addr("pcc_gui_event_passive_capacity"), 0)
    if name == "pcc_gui_event_sequence":
        return load_i64(global_addr("pcc_gui_event_sequence"), 0)
    if name == "pcc_gui_event_state":
        return load_i64(global_addr("pcc_gui_event_state"), 0)
    if name == "pcc_gui_event_last_error":
        return load_i64(global_addr("pcc_gui_event_last_error"), 0)
    return 0


def _listener_at(index: int):
    return int_to_ptr(_base("pcc_gui_event_listeners") + index * LISTENER_SIZE)


def _callback_at(index: int):
    return int_to_ptr(_base("pcc_gui_event_callbacks") + index * CALLBACK_SIZE)


def _effect_at(index: int):
    return int_to_ptr(_base("pcc_gui_event_effects") + index * EFFECT_SIZE)


def _passive_at(index: int):
    return int_to_ptr(_base("pcc_gui_event_passive") + index * PASSIVE_SIZE)


def _set_last_error(code: int) -> None:
    if code < 0:
        store_i64(global_addr("pcc_gui_event_last_error"), 0, code)


def _write_error(error_out, code: int, phase: int, subject: int) -> int:
    _set_last_error(code)
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, code)
        store_i32(error_out, 4, phase)
        store_i64(error_out, 8, subject)
        if code == ERR_CAPACITY:
            store_ptr(error_out, 16, cstr("gui event capacity exceeded"))
        elif code == ERR_DUPLICATE_KEY:
            store_ptr(error_out, 16, cstr("duplicate gui listener or effect id"))
        elif code == ERR_STALE_NODE:
            store_ptr(error_out, 16, cstr("stale gui listener target"))
        elif code == ERR_OWNERSHIP:
            store_ptr(error_out, 16, cstr("invalid gui listener target filter"))
        elif code == ERR_CALLBACK_FAILED:
            store_ptr(error_out, 16, cstr("gui listener or lifecycle callback failed"))
        else:
            store_ptr(error_out, 16, cstr("invalid gui event transition"))
    return code


def _clear_error(error_out) -> None:
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, 0)
        store_i32(error_out, 4, 0)
        store_i64(error_out, 8, 0)
        store_i64(error_out, 16, 0)


def _callback_index(callback_id: int, kind: int) -> int:
    cap = _base("pcc_gui_event_callback_capacity")
    i = 0
    while i < cap:
        record = _callback_at(i)
        if (
            load_i32(record, 0) != 0
            and load_i32(record, 4) == callback_id
            and load_i32(record, 8) == kind
        ):
            return i
        i = i + 1
    return -1


def _callback_id_exists(callback_id: int) -> int:
    cap = _base("pcc_gui_event_callback_capacity")
    i = 0
    while i < cap:
        record = _callback_at(i)
        if load_i32(record, 0) != 0 and load_i32(record, 4) == callback_id:
            return 1
        i = i + 1
    return 0


def _listener_index(listener_id: int) -> int:
    cap = _base("pcc_gui_event_listener_capacity")
    i = 0
    while i < cap:
        if load_i64(_listener_at(i), 0) == listener_id:
            return i
        i = i + 1
    return -1


def _effect_index(effect_id: int) -> int:
    cap = _base("pcc_gui_event_effect_capacity")
    i = 0
    while i < cap:
        record = _effect_at(i)
        if load_i32(record, 0) != 0 and load_i32(record, 4) == effect_id:
            return i
        i = i + 1
    return -1


def _free_passive_count() -> int:
    cap = _base("pcc_gui_event_passive_capacity")
    total = 0
    i = 0
    while i < cap:
        if load_i64(_passive_at(i), 0) == 0:
            total = total + 1
        i = i + 1
    return total


def _can_reserve_passive(count: int) -> int:
    if count < 0 or _free_passive_count() < count:
        return 0
    sequence = _base("pcc_gui_event_sequence")
    if sequence <= 0 or sequence > 0x7FFFFFFFFFFFFFFF - count:
        return 0
    return 1


def _active_effect_count(component_id: int) -> int:
    cap = _base("pcc_gui_event_effect_capacity")
    total = 0
    i = 0
    while i < cap:
        record = _effect_at(i)
        if load_i32(record, 0) != 0 and load_i64(record, 8) == component_id:
            total = total + 1
        i = i + 1
    return total


def _has_live_records() -> int:
    cap = _base("pcc_gui_event_listener_capacity")
    i = 0
    while i < cap:
        if load_i64(_listener_at(i), 0) != 0:
            return 1
        i = i + 1
    cap = _base("pcc_gui_event_effect_capacity")
    i = 0
    while i < cap:
        if load_i32(_effect_at(i), 0) != 0:
            return 1
        i = i + 1
    cap = _base("pcc_gui_event_passive_capacity")
    i = 0
    while i < cap:
        if load_i64(_passive_at(i), 0) != 0:
            return 1
        i = i + 1
    return 0


@c_abi_typed_export(
    "pcc_gui_events_init", "i32", ("i64", "i64", "i64", "i64")
)
def pcc_gui_events_init(
    listener_capacity: int,
    callback_capacity: int,
    effect_capacity: int,
    passive_capacity: int,
) -> int:
    if (
        listener_capacity <= 0
        or listener_capacity > 2048
        or callback_capacity <= 0
        or callback_capacity > 4096
        or effect_capacity <= 0
        or effect_capacity > 2048
        or passive_capacity <= 0
        or passive_capacity > 4096
    ):
        return ERR_CAPACITY
    if _base("pcc_gui_event_state") != EVENTS_IDLE or _has_live_records() != 0:
        return ERR_INVALID_TRANSITION

    listeners = calloc(listener_capacity, LISTENER_SIZE)
    callbacks = calloc(callback_capacity, CALLBACK_SIZE)
    effects = calloc(effect_capacity, EFFECT_SIZE)
    passive = calloc(passive_capacity, PASSIVE_SIZE)
    if (
        ptr_is_null(listeners)
        or ptr_is_null(callbacks)
        or ptr_is_null(effects)
        or ptr_is_null(passive)
    ):
        if not ptr_is_null(listeners):
            free(listeners)
        if not ptr_is_null(callbacks):
            free(callbacks)
        if not ptr_is_null(effects):
            free(effects)
        if not ptr_is_null(passive):
            free(passive)
        return ERR_CAPACITY

    # Publish only after every replacement allocation exists.  An allocation
    # failure therefore leaves the previous empty registry usable instead of
    # leaving globals that point at freed storage.
    old = _base("pcc_gui_event_listeners")
    if old != 0:
        free(int_to_ptr(old))
    old = _base("pcc_gui_event_callbacks")
    if old != 0:
        free(int_to_ptr(old))
    old = _base("pcc_gui_event_effects")
    if old != 0:
        free(int_to_ptr(old))
    old = _base("pcc_gui_event_passive")
    if old != 0:
        free(int_to_ptr(old))

    store_i64(global_addr("pcc_gui_event_listeners"), 0, ptr_to_int(listeners))
    store_i64(global_addr("pcc_gui_event_callbacks"), 0, ptr_to_int(callbacks))
    store_i64(global_addr("pcc_gui_event_effects"), 0, ptr_to_int(effects))
    store_i64(global_addr("pcc_gui_event_passive"), 0, ptr_to_int(passive))
    store_i64(global_addr("pcc_gui_event_listener_capacity"), 0, listener_capacity)
    store_i64(global_addr("pcc_gui_event_callback_capacity"), 0, callback_capacity)
    store_i64(global_addr("pcc_gui_event_effect_capacity"), 0, effect_capacity)
    store_i64(global_addr("pcc_gui_event_passive_capacity"), 0, passive_capacity)
    store_i64(global_addr("pcc_gui_event_sequence"), 0, 1)
    store_i64(global_addr("pcc_gui_event_last_error"), 0, 0)
    return OK


def _register_callback(callback_id: int, kind: int, callback) -> int:
    if (
        _base("pcc_gui_event_state") != EVENTS_IDLE
        or callback_id <= 0
        or ptr_is_null(callback)
    ):
        return ERR_INVALID_TRANSITION
    if _callback_id_exists(callback_id) != 0:
        return ERR_DUPLICATE_KEY
    cap = _base("pcc_gui_event_callback_capacity")
    i = 0
    while i < cap:
        record = _callback_at(i)
        if load_i32(record, 0) == 0:
            store_i32(record, 4, callback_id)
            store_i32(record, 8, kind)
            store_i64(record, 16, ptr_to_int(callback))
            store_i32(record, 0, 1)
            return OK
        i = i + 1
    return ERR_CAPACITY


@c_abi_typed_export(
    "pcc_gui_events_register_listener_callback", "i32", ("i32", "ptr")
)
def pcc_gui_events_register_listener_callback(callback_id: int, callback) -> int:
    return _register_callback(callback_id, CALLBACK_LISTENER, callback)


@c_abi_typed_export(
    "pcc_gui_events_register_effect_callback", "i32", ("i32", "ptr")
)
def pcc_gui_events_register_effect_callback(callback_id: int, callback) -> int:
    return _register_callback(callback_id, CALLBACK_EFFECT, callback)


@c_abi_typed_export(
    "pcc_gui_events_listen",
    "i32",
    ("i64", "i64", "i32", "i32", "i32", "i64"),
)
def pcc_gui_events_listen(
    listener_id: int,
    target_component_id: int,
    event_type: int,
    callback_id: int,
    flags: int,
    policy_context: int,
) -> int:
    if _base("pcc_gui_event_state") != EVENTS_IDLE:
        return ERR_INVALID_TRANSITION
    if (
        listener_id <= 0
        or event_type <= 0
        or (flags & ~LISTENER_ONCE) != 0
        or _component_valid(target_component_id) == 0
        or _callback_index(callback_id, CALLBACK_LISTENER) < 0
    ):
        return ERR_OWNERSHIP
    existing = _listener_index(listener_id)
    if existing >= 0:
        record = _listener_at(existing)
        if (load_i32(record, 24) & LISTENER_ACTIVE) != 0:
            return ERR_DUPLICATE_KEY
        store_i64(record, 8, target_component_id)
        store_i32(record, 16, event_type)
        store_i32(record, 20, callback_id)
        store_i32(record, 24, LISTENER_ACTIVE | flags)
        store_i64(record, 32, policy_context)
        return OK
    cap = _base("pcc_gui_event_listener_capacity")
    i = 0
    while i < cap:
        record = _listener_at(i)
        if load_i64(record, 0) == 0:
            store_i64(record, 0, listener_id)
            store_i64(record, 8, target_component_id)
            store_i32(record, 16, event_type)
            store_i32(record, 20, callback_id)
            store_i32(record, 24, LISTENER_ACTIVE | flags)
            store_i32(record, 28, 0)
            store_i64(record, 32, policy_context)
            return OK
        i = i + 1
    return ERR_CAPACITY


def _clear_listener(index: int) -> None:
    record = _listener_at(index)
    store_i64(record, 0, 0)
    store_i64(record, 8, 0)
    store_i32(record, 16, 0)
    store_i32(record, 20, 0)
    store_i32(record, 24, 0)
    store_i32(record, 28, 0)
    store_i64(record, 32, 0)


def _deactivate_listener(index: int) -> None:
    record = _listener_at(index)
    store_i32(record, 24, load_i32(record, 24) & ~LISTENER_ACTIVE)


@c_abi_typed_export("pcc_gui_events_unlisten", "i32", ("i64",))
def pcc_gui_events_unlisten(listener_id: int) -> int:
    if _base("pcc_gui_event_state") != EVENTS_IDLE:
        return ERR_INVALID_TRANSITION
    index = _listener_index(listener_id)
    if index < 0:
        return ERR_STALE_NODE
    listener = _listener_at(index)
    status = _component_clear_listener(load_i64(listener, 8), listener_id)
    if status != OK:
        return status
    _deactivate_listener(index)
    return OK


def _listener_seen_before(path, index: int, listener_id: int) -> int:
    i = 0
    while i < index:
        if _component_listener(load_i64(path, i * 8)) == listener_id:
            return 1
        i = i + 1
    return 0


@c_abi_typed_export(
    "pcc_gui_events_dispatch",
    "i32",
    ("i64", "i64", "i64", "i32", "ptr", "ptr", "i32", "ptr"),
)
def pcc_gui_events_dispatch(
    root_node_id: int,
    x: int,
    y: int,
    event_type: int,
    event,
    path_arena,
    path_capacity: int,
    error_out,
) -> int:
    _clear_error(error_out)
    if _base("pcc_gui_event_state") != EVENTS_IDLE:
        return _write_error(
            error_out, ERR_INVALID_TRANSITION, PHASE_STRUCTURAL, root_node_id
        )
    if event_type <= 0 or path_capacity <= 0 or ptr_is_null(path_arena):
        return _write_error(
            error_out, ERR_OWNERSHIP, PHASE_STRUCTURAL, root_node_id
        )
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_DISPATCHING)
    path_count = _kit_route(
        root_node_id, x, y, event_type, path_arena, path_capacity
    )
    if path_count < 0:
        store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
        return _write_error(
            error_out, ERR_CAPACITY, PHASE_STRUCTURAL, root_node_id
        )
    if path_count == 0:
        store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
        return 0
    target_component = -1
    target_index = 0
    while target_index < path_count and target_component < 0:
        target_component = _component_owner(
            load_i64(path_arena, target_index * 8)
        )
        target_index = target_index + 1
    if target_component < 0:
        store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
        return _write_error(
            error_out, ERR_STALE_NODE, PHASE_STRUCTURAL, root_node_id
        )

    invoked = 0
    i = 0
    while i < path_count:
        node_id = load_i64(path_arena, i * 8)
        listener_id = _component_listener(node_id)
        if listener_id != 0 and _listener_seen_before(path_arena, i, listener_id) == 0:
            listener_index = _listener_index(listener_id)
            if listener_index < 0:
                store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
                return _write_error(
                    error_out, ERR_STALE_NODE, PHASE_STRUCTURAL, listener_id
                )
            listener = _listener_at(listener_index)
            if (
                (load_i32(listener, 24) & LISTENER_ACTIVE) != 0
                and load_i32(listener, 16) == event_type
            ):
                owner = _component_binding_owner(node_id)
                if owner < 0 or load_i64(listener, 8) != owner:
                    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
                    return _write_error(
                        error_out, ERR_OWNERSHIP, PHASE_STRUCTURAL, listener_id
                    )
                if _component_valid(owner) == 0:
                    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
                    return _write_error(
                        error_out, ERR_STALE_NODE, PHASE_STRUCTURAL, listener_id
                    )
                callback_index = _callback_index(
                    load_i32(listener, 20), CALLBACK_LISTENER
                )
                if callback_index < 0:
                    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
                    return _write_error(
                        error_out, ERR_CALLBACK_FAILED, PHASE_STRUCTURAL, listener_id
                    )
                callback = _callback_at(callback_index)
                status = call_i32_i64_i64_ptr(
                    int_to_ptr(load_i64(callback, 16)),
                    listener_id,
                    target_component,
                    event,
                )
                invoked = invoked + 1
                if (load_i32(listener, 24) & LISTENER_ONCE) != 0:
                    _component_clear_listener(owner, listener_id)
                    _deactivate_listener(listener_index)
                if status < 0:
                    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
                    return _write_error(
                        error_out, ERR_CALLBACK_FAILED, PHASE_STRUCTURAL, listener_id
                    )
                if status == 1:
                    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
                    return invoked
                if status != 0:
                    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
                    return _write_error(
                        error_out, ERR_CALLBACK_FAILED, PHASE_STRUCTURAL, listener_id
                    )
        i = i + 1
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
    return invoked


def _next_effect(component_id: int, cursor: int, ascending: int) -> int:
    cap = _base("pcc_gui_event_effect_capacity")
    best = -1
    best_sequence = 0x7FFFFFFFFFFFFFFF if ascending != 0 else -1
    i = 0
    while i < cap:
        record = _effect_at(i)
        if load_i32(record, 0) != 0 and load_i64(record, 8) == component_id:
            sequence = load_i64(record, 40)
            if ascending != 0:
                if sequence > cursor and sequence < best_sequence:
                    best = i
                    best_sequence = sequence
            elif sequence < cursor and sequence > best_sequence:
                best = i
                best_sequence = sequence
        i = i + 1
    return best


def _invoke_effects(component_id: int, phase: int, ascending: int) -> int:
    cursor = 0 if ascending != 0 else 0x7FFFFFFFFFFFFFFF
    first_error = 0
    while 1:
        index = _next_effect(component_id, cursor, ascending)
        if index < 0:
            return first_error
        effect = _effect_at(index)
        cursor = load_i64(effect, 40)
        callback_index = _callback_index(load_i32(effect, 24), CALLBACK_EFFECT)
        if callback_index < 0:
            status = ERR_CALLBACK_FAILED
        else:
            callback = _callback_at(callback_index)
            status = call_i32_i64_i32_i64(
                int_to_ptr(load_i64(callback, 16)),
                component_id,
                phase,
                load_i64(effect, 32),
            )
            if status != 0:
                status = ERR_CALLBACK_FAILED
        if status < 0 and first_error == 0:
            first_error = status


def _target_removed(structural_effects, effect_count: int, target_key: int) -> int:
    if target_key == 0 or effect_count <= 0 or ptr_is_null(structural_effects):
        return 0
    i = 0
    while i < effect_count:
        effect = int_to_ptr(ptr_to_int(structural_effects) + i * 48)
        if (
            load_i32(effect, 28) == STRUCTURAL_REMOVE
            and load_i64(effect, 40) == target_key
        ):
            return 1
        i = i + 1
    return 0


def _invoke_live_effects(
    component_id: int,
    phase: int,
    ascending: int,
    structural_effects,
    effect_count: int,
) -> int:
    cursor = 0 if ascending != 0 else 0x7FFFFFFFFFFFFFFF
    first_error = 0
    while 1:
        index = _next_effect(component_id, cursor, ascending)
        if index < 0:
            return first_error
        effect = _effect_at(index)
        cursor = load_i64(effect, 40)
        if _target_removed(
            structural_effects, effect_count, load_i64(effect, 16)
        ) == 0:
            callback_index = _callback_index(load_i32(effect, 24), CALLBACK_EFFECT)
            if callback_index < 0:
                status = ERR_CALLBACK_FAILED
            else:
                callback = _callback_at(callback_index)
                status = call_i32_i64_i32_i64(
                    int_to_ptr(load_i64(callback, 16)),
                    component_id,
                    phase,
                    load_i64(effect, 32),
                )
                if status != 0:
                    status = ERR_CALLBACK_FAILED
            if status < 0 and first_error == 0:
                first_error = status


def _queue_passive(
    component_id: int,
    callback_id: int,
    phase: int,
    payload: int,
    effect_id: int,
) -> int:
    sequence = _base("pcc_gui_event_sequence")
    if sequence <= 0 or sequence >= 0x7FFFFFFFFFFFFFFF:
        return ERR_CAPACITY
    cap = _base("pcc_gui_event_passive_capacity")
    i = 0
    while i < cap:
        record = _passive_at(i)
        if load_i64(record, 0) == 0:
            store_i64(record, 8, component_id)
            store_i32(record, 16, callback_id)
            store_i32(record, 20, phase)
            store_i64(record, 24, payload)
            store_i32(record, 32, effect_id)
            store_i32(record, 36, 0)
            store_i64(record, 0, sequence)
            store_i64(global_addr("pcc_gui_event_sequence"), 0, sequence + 1)
            return OK
        i = i + 1
    return ERR_CAPACITY


def _queue_effect_phase(component_id: int, phase: int, ascending: int) -> int:
    cursor = 0 if ascending != 0 else 0x7FFFFFFFFFFFFFFF
    while 1:
        index = _next_effect(component_id, cursor, ascending)
        if index < 0:
            return OK
        effect = _effect_at(index)
        cursor = load_i64(effect, 40)
        status = _queue_passive(
            component_id,
            load_i32(effect, 24),
            phase,
            load_i64(effect, 32),
            load_i32(effect, 4),
        )
        if status != OK:
            return status


def _queue_live_effect_phase(
    component_id: int,
    phase: int,
    ascending: int,
    structural_effects,
    effect_count: int,
) -> int:
    cursor = 0 if ascending != 0 else 0x7FFFFFFFFFFFFFFF
    while 1:
        index = _next_effect(component_id, cursor, ascending)
        if index < 0:
            return OK
        effect = _effect_at(index)
        cursor = load_i64(effect, 40)
        if _target_removed(
            structural_effects, effect_count, load_i64(effect, 16)
        ) == 0:
            status = _queue_passive(
                component_id,
                load_i32(effect, 24),
                phase,
                load_i64(effect, 32),
                load_i32(effect, 4),
            )
            if status != OK:
                return status


def _clear_removed_effects(
    component_id: int, structural_effects, effect_count: int
) -> None:
    cap = _base("pcc_gui_event_effect_capacity")
    i = 0
    while i < cap:
        effect = _effect_at(i)
        if (
            load_i32(effect, 0) != 0
            and load_i64(effect, 8) == component_id
            and _target_removed(
                structural_effects, effect_count, load_i64(effect, 16)
            )
            != 0
        ):
            _clear_effect(i)
        i = i + 1


@c_abi_typed_export(
    "pcc_gui_events_register_effect",
    "i32",
    ("i64", "i32", "i64", "i32", "i64"),
)
def pcc_gui_events_register_effect(
    component_id: int,
    effect_id: int,
    target_key: int,
    callback_id: int,
    payload: int,
) -> int:
    if _base("pcc_gui_event_state") != EVENTS_IDLE:
        return ERR_INVALID_TRANSITION
    if (
        effect_id <= 0
        or _component_valid(component_id) == 0
        or _callback_index(callback_id, CALLBACK_EFFECT) < 0
        or (target_key != 0 and _component_node_for_key(component_id, target_key) < 0)
    ):
        return ERR_OWNERSHIP
    if _effect_index(effect_id) >= 0:
        return ERR_DUPLICATE_KEY
    if _can_reserve_passive(1) == 0:
        return ERR_CAPACITY
    cap = _base("pcc_gui_event_effect_capacity")
    index = -1
    i = 0
    while i < cap:
        if load_i32(_effect_at(i), 0) == 0:
            index = i
            i = cap
        else:
            i = i + 1
    if index < 0:
        return ERR_CAPACITY
    sequence = _base("pcc_gui_event_sequence")
    if sequence <= 0 or sequence >= 0x7FFFFFFFFFFFFFFF:
        return ERR_CAPACITY
    effect = _effect_at(index)
    store_i32(effect, 4, effect_id)
    store_i64(effect, 8, component_id)
    store_i64(effect, 16, target_key)
    store_i32(effect, 24, callback_id)
    store_i32(effect, 28, 0)
    store_i64(effect, 32, payload)
    store_i64(effect, 40, sequence)
    store_i32(effect, 0, 1)
    store_i64(global_addr("pcc_gui_event_sequence"), 0, sequence + 1)

    callback = _callback_at(_callback_index(callback_id, CALLBACK_EFFECT))
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_AFTER_COMMIT)
    status = call_i32_i64_i32_i64(
        int_to_ptr(load_i64(callback, 16)),
        component_id,
        PHASE_LAYOUT_CREATE,
        payload,
    )
    if status != 0:
        store_i32(effect, 0, 0)
        store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
        return ERR_CALLBACK_FAILED
    status = _queue_passive(
        component_id, callback_id, PHASE_PASSIVE_CREATE, payload, effect_id
    )
    if status != OK:
        call_i32_i64_i32_i64(
            int_to_ptr(load_i64(callback, 16)),
            component_id,
            PHASE_LAYOUT_CLEANUP,
            payload,
        )
        store_i32(effect, 0, 0)
        store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
        return status
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
    return OK


def _clear_effect(index: int) -> None:
    record = _effect_at(index)
    store_i32(record, 0, 0)
    store_i32(record, 4, 0)
    store_i64(record, 8, 0)
    store_i64(record, 16, 0)
    store_i32(record, 24, 0)
    store_i32(record, 28, 0)
    store_i64(record, 32, 0)
    store_i64(record, 40, 0)


def _clear_component_listeners(component_id: int) -> None:
    cap = _base("pcc_gui_event_listener_capacity")
    i = 0
    while i < cap:
        record = _listener_at(i)
        if load_i64(record, 0) != 0 and load_i64(record, 8) == component_id:
            _clear_listener(i)
        i = i + 1


def _reconcile_component_listeners(component_id: int) -> None:
    cap = _base("pcc_gui_event_listener_capacity")
    i = 0
    while i < cap:
        record = _listener_at(i)
        listener_id = load_i64(record, 0)
        if listener_id != 0 and load_i64(record, 8) == component_id:
            if _component_listener_bound(component_id, listener_id) == 0:
                _clear_listener(i)
        i = i + 1


@c_abi_typed_export(
    "pcc_gui_events_before_component_commit",
    "i32",
    ("i64", "ptr", "i32", "ptr"),
)
def pcc_gui_events_before_component_commit(
    component_id: int, structural_effects, effect_count: int, error_out
) -> int:
    if _base("pcc_gui_event_effects") == 0:
        return OK
    if _base("pcc_gui_event_state") != EVENTS_IDLE:
        return _write_error(
            error_out, ERR_INVALID_TRANSITION, PHASE_BEFORE_MUTATION, component_id
        )
    count = _active_effect_count(component_id)
    if _can_reserve_passive(count * 2) == 0:
        return _write_error(
            error_out, ERR_CAPACITY, PHASE_PASSIVE_CLEANUP, component_id
        )
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_BEFORE_COMMIT)
    snapshot_status = _invoke_effects(
        component_id, PHASE_BEFORE_MUTATION, 1
    )
    cleanup_status = _invoke_effects(
        component_id, PHASE_LAYOUT_CLEANUP, 0
    )
    status = snapshot_status if snapshot_status != 0 else cleanup_status
    if status != 0:
        _invoke_effects(component_id, PHASE_LAYOUT_CREATE, 1)
        store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
        return _write_error(
            error_out, ERR_CALLBACK_FAILED, PHASE_LAYOUT_CLEANUP, component_id
        )
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
    return OK


@c_abi_typed_export(
    "pcc_gui_events_abort_component_commit", "i32", ("i64", "ptr")
)
def pcc_gui_events_abort_component_commit(component_id: int, error_out) -> int:
    if _base("pcc_gui_event_state") != EVENTS_IDLE:
        return _write_error(
            error_out, ERR_INVALID_TRANSITION, PHASE_LAYOUT_CREATE, component_id
        )
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_AFTER_COMMIT)
    status = _invoke_effects(component_id, PHASE_LAYOUT_CREATE, 1)
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
    if status != 0:
        return _write_error(
            error_out, ERR_CALLBACK_FAILED, PHASE_LAYOUT_CREATE, component_id
        )
    return OK


@c_abi_typed_export(
    "pcc_gui_events_after_component_commit",
    "i32",
    ("i64", "ptr", "i32", "ptr"),
)
def pcc_gui_events_after_component_commit(
    component_id: int, structural_effects, effect_count: int, error_out
) -> int:
    if _base("pcc_gui_event_effects") == 0:
        return OK
    if _base("pcc_gui_event_state") != EVENTS_IDLE:
        return _write_error(
            error_out, ERR_INVALID_TRANSITION, PHASE_LAYOUT_CREATE, component_id
        )
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_AFTER_COMMIT)
    create_status = _invoke_live_effects(
        component_id,
        PHASE_LAYOUT_CREATE,
        1,
        structural_effects,
        effect_count,
    )
    cleanup_status = _queue_effect_phase(
        component_id, PHASE_PASSIVE_CLEANUP, 0
    )
    creation_status = OK
    if create_status == OK:
        creation_status = _queue_live_effect_phase(
            component_id,
            PHASE_PASSIVE_CREATE,
            1,
            structural_effects,
            effect_count,
        )
    _clear_removed_effects(component_id, structural_effects, effect_count)
    _reconcile_component_listeners(component_id)
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
    status = create_status
    if status == 0:
        status = cleanup_status
    if status == 0:
        status = creation_status
    if status != 0:
        return _write_error(
            error_out, ERR_CALLBACK_FAILED, PHASE_LAYOUT_CREATE, component_id
        )
    return OK


@c_abi_typed_export(
    "pcc_gui_events_before_component_unmount", "i32", ("i64", "ptr")
)
def pcc_gui_events_before_component_unmount(component_id: int, error_out) -> int:
    if _base("pcc_gui_event_state") != EVENTS_IDLE:
        return _write_error(
            error_out, ERR_INVALID_TRANSITION, PHASE_LAYOUT_CLEANUP, component_id
        )
    if _base("pcc_gui_event_effects") == 0:
        _style_component_unmounted(component_id)
        return _scheduler_cancel(component_id)
    count = _active_effect_count(component_id)
    if _can_reserve_passive(count) == 0:
        return _write_error(
            error_out, ERR_CAPACITY, PHASE_PASSIVE_CLEANUP, component_id
        )
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_UNMOUNTING)
    snapshot_status = _invoke_effects(
        component_id, PHASE_BEFORE_MUTATION, 1
    )
    cleanup_status = _invoke_effects(
        component_id, PHASE_LAYOUT_CLEANUP, 0
    )
    queue_status = _queue_effect_phase(
        component_id, PHASE_PASSIVE_CLEANUP, 0
    )
    cap = _base("pcc_gui_event_effect_capacity")
    i = 0
    while i < cap:
        record = _effect_at(i)
        if load_i32(record, 0) != 0 and load_i64(record, 8) == component_id:
            _clear_effect(i)
        i = i + 1
    _clear_component_listeners(component_id)
    _style_component_unmounted(component_id)
    cancel_status = _scheduler_cancel(component_id)
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
    status = snapshot_status
    if status == 0:
        status = cleanup_status
    if status == 0:
        status = queue_status
    if status == 0:
        status = cancel_status
    if status != 0:
        return _write_error(
            error_out, ERR_CALLBACK_FAILED, PHASE_LAYOUT_CLEANUP, component_id
        )
    return OK


@c_abi_typed_export(
    "pcc_gui_events_node_removed", "i32", ("i64", "i64", "i64")
)
def pcc_gui_events_node_removed(
    component_id: int, node_id: int, listener_id: int
) -> int:
    if listener_id <= 0:
        return OK
    index = _listener_index(listener_id)
    if index < 0:
        return OK
    if _component_listener_bound(component_id, listener_id) == 0:
        _clear_listener(index)
    return OK


def _next_passive_index() -> int:
    cap = _base("pcc_gui_event_passive_capacity")
    best = -1
    best_sequence = 0x7FFFFFFFFFFFFFFF
    i = 0
    while i < cap:
        sequence = load_i64(_passive_at(i), 0)
        if sequence > 0 and sequence < best_sequence:
            best = i
            best_sequence = sequence
        i = i + 1
    return best


@c_abi_typed_export("pcc_gui_events_drain_passive", "i32", ("i32", "ptr"))
def pcc_gui_events_drain_passive(limit: int, error_out) -> int:
    _clear_error(error_out)
    if limit <= 0 or _base("pcc_gui_event_state") != EVENTS_IDLE:
        return _write_error(
            error_out, ERR_INVALID_TRANSITION, PHASE_PASSIVE_CLEANUP, 0
        )
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_DRAINING)
    completed = 0
    while completed < limit:
        index = _next_passive_index()
        if index < 0:
            store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
            return completed
        record = _passive_at(index)
        component_id = load_i64(record, 8)
        callback_id = load_i32(record, 16)
        phase = load_i32(record, 20)
        payload = load_i64(record, 24)
        effect_id = load_i32(record, 32)
        callback_index = _callback_index(callback_id, CALLBACK_EFFECT)
        store_i64(record, 0, 0)
        if callback_index < 0:
            store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
            return _write_error(
                error_out, ERR_CALLBACK_FAILED, phase, effect_id
            )
        callback = _callback_at(callback_index)
        status = call_i32_i64_i32_i64(
            int_to_ptr(load_i64(callback, 16)), component_id, phase, payload
        )
        if status != 0:
            store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
            return _write_error(
                error_out, ERR_CALLBACK_FAILED, phase, effect_id
            )
        completed = completed + 1
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
    return completed


@c_abi_typed_export("pcc_gui_events_last_error", "i32", ())
def pcc_gui_events_last_error() -> int:
    return _base("pcc_gui_event_last_error")


@c_abi_typed_export("pcc_gui_events_listener_count", "i32", ("i64",))
def pcc_gui_events_listener_count(component_id: int) -> int:
    cap = _base("pcc_gui_event_listener_capacity")
    total = 0
    i = 0
    while i < cap:
        record = _listener_at(i)
        if (
            load_i64(record, 0) != 0
            and (load_i32(record, 24) & LISTENER_ACTIVE) != 0
            and load_i64(record, 8) == component_id
        ):
            total = total + 1
        i = i + 1
    return total


@c_abi_typed_export("pcc_gui_events_shutdown", "i32", ("ptr",))
def pcc_gui_events_shutdown(error_out) -> int:
    """Drain passive cleanup, then retire all listener/effect records."""
    _clear_error(error_out)
    if _base("pcc_gui_event_listeners") == 0:
        return OK
    passive_cap = _base("pcc_gui_event_passive_capacity")
    if passive_cap > 0:
        status = pcc_gui_events_drain_passive(passive_cap, error_out)
        if status < 0:
            return status
        # Cleanup callbacks are not allowed to create an unbounded shutdown
        # tail.  One full-capacity drain must empty the fixed arena.
        if _next_passive_index() >= 0:
            return _write_error(
                error_out, ERR_INVALID_TRANSITION, PHASE_PASSIVE_CLEANUP, 0
            )
    cap = _base("pcc_gui_event_listener_capacity")
    i = 0
    while i < cap:
        if load_i64(_listener_at(i), 0) != 0:
            _clear_listener(i)
        i = i + 1
    cap = _base("pcc_gui_event_effect_capacity")
    i = 0
    while i < cap:
        if load_i32(_effect_at(i), 0) != 0:
            _clear_effect(i)
        i = i + 1
    store_i64(global_addr("pcc_gui_event_state"), 0, EVENTS_IDLE)
    return OK
