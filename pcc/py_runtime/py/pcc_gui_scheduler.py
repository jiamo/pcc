"""Bounded lane scheduler for canonical GUI components.

Updates remain in one per-component enqueue-order chain.  A selected lane is
evaluated against an owned work snapshot.  At the first skipped update the
scheduler captures the state immediately before it; every selected update
after that point remains in the queue as an always-replay record.  This is the
v1 base-queue rule that makes interrupted priority work converge in original
enqueue order.

Budget exhaustion discards all work snapshots and leaves component state,
kernel structure, owner routes, and update ownership unchanged.  A completed
state result is exposed to the component callback only for an atomic keyed
render commit; callback/commit failure restores the previous owned state.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import (
    call_i32_i64_i64_ptr,
    calloc,
    cstr,
    define_global_i64,
    free,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    ptr_add,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


SLOT_SIZE = 24
UPDATE_SIZE = 64
REDUCER_SIZE = 24
SCHEDULER_COMPONENT_SIZE = 96

ACTION_SET = 1
ACTION_REDUCE = 2
SLOT_I64 = 1
SLOT_OPAQUE_HANDLE = 2

LANE_DISCRETE = 0
LANE_ANIMATION = 1
LANE_DEFAULT = 2
LANE_BACKGROUND = 3

UPDATE_REPLAY_ALWAYS = 1
UPDATE_REDUCER_EVALUATED = 2
REDUCER_PURE = 1

WORK_IDLE = 0
WORK_YIELDED = 1
WORK_RENDERING = 2
WORK_FAILED = 3
WORK_EVALUATING = 4

OK = 0
YIELDED = 1
ERR_CAPACITY = -101
ERR_INVALID_TRANSITION = -103
ERR_REDUCER_FAILED = -104
ERR_OWNERSHIP = -105
ERR_STALE_NODE = -106
ERR_CALLBACK_FAILED = -116


_component_valid = extern("pcc_gui_component_is_valid", (c_int64,), c_int32)
_component_state_count = extern(
    "pcc_gui_component_state_count", (c_int64,), c_int32
)
_component_slot_kind = extern(
    "pcc_gui_component_state_slot_kind", (c_int64, c_int32), c_int32
)
_component_slot_value = extern(
    "pcc_gui_component_state_slot_value", (c_int64, c_int32), c_int64
)
_component_snapshot = extern(
    "pcc_gui_component_state_snapshot", (c_int64, c_ptr, c_int32), c_int32
)
_component_discard = extern(
    "pcc_gui_component_state_discard", (c_ptr, c_int32), c_int32
)
_component_replace_owned = extern(
    "pcc_gui_component_state_replace_owned", (c_int64, c_ptr, c_int32), c_int32
)
_component_handle_retain = extern(
    "pcc_gui_component_handle_retain", (c_int64,), c_int64
)
_component_handle_release = extern(
    "pcc_gui_component_handle_release", (c_int64,), c_int64
)
_component_render_commit = extern(
    "pcc_gui_component_render_commit",
    (c_int64, c_ptr, c_int32, c_ptr, c_int32, c_ptr, c_ptr),
    c_int32,
)


define_global_i64("pcc_gui_scheduler_components", 0)
define_global_i64("pcc_gui_scheduler_component_capacity", 0)
define_global_i64("pcc_gui_scheduler_updates", 0)
define_global_i64("pcc_gui_scheduler_update_capacity", 0)
define_global_i64("pcc_gui_scheduler_reducers", 0)
define_global_i64("pcc_gui_scheduler_reducer_capacity", 0)
define_global_i64("pcc_gui_scheduler_sequence", 1)
define_global_i64("pcc_gui_scheduler_epoch", 0)


def _base(name: str) -> int:
    if name == "pcc_gui_scheduler_components":
        return load_i64(global_addr("pcc_gui_scheduler_components"), 0)
    if name == "pcc_gui_scheduler_component_capacity":
        return load_i64(global_addr("pcc_gui_scheduler_component_capacity"), 0)
    if name == "pcc_gui_scheduler_updates":
        return load_i64(global_addr("pcc_gui_scheduler_updates"), 0)
    if name == "pcc_gui_scheduler_update_capacity":
        return load_i64(global_addr("pcc_gui_scheduler_update_capacity"), 0)
    if name == "pcc_gui_scheduler_reducers":
        return load_i64(global_addr("pcc_gui_scheduler_reducers"), 0)
    if name == "pcc_gui_scheduler_reducer_capacity":
        return load_i64(global_addr("pcc_gui_scheduler_reducer_capacity"), 0)
    if name == "pcc_gui_scheduler_sequence":
        return load_i64(global_addr("pcc_gui_scheduler_sequence"), 0)
    if name == "pcc_gui_scheduler_epoch":
        return load_i64(global_addr("pcc_gui_scheduler_epoch"), 0)
    return 0


def _scheduler_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_scheduler_components") + index * SCHEDULER_COMPONENT_SIZE
    )


def _update_at(index: int):
    return int_to_ptr(_base("pcc_gui_scheduler_updates") + index * UPDATE_SIZE)


def _reducer_at(index: int):
    return int_to_ptr(_base("pcc_gui_scheduler_reducers") + index * REDUCER_SIZE)


def _scheduler_index(component_id: int, create: int) -> int:
    cap = _base("pcc_gui_scheduler_component_capacity")
    free_index = -1
    i = 0
    while i < cap:
        record = _scheduler_at(i)
        if load_i32(record, 0) != 0:
            if load_i64(record, 8) == component_id:
                return i
        elif free_index < 0:
            free_index = i
        i = i + 1
    if create == 0 or free_index < 0:
        return -1
    record = _scheduler_at(free_index)
    store_i32(record, 0, 1)
    store_i32(record, 4, WORK_IDLE)
    store_i64(record, 8, component_id)
    store_i64(record, 16, 0)
    store_i32(record, 24, 0)
    store_i32(record, 28, 0)
    store_i64(record, 32, -1)
    store_i64(record, 40, -1)
    store_i32(record, 48, 0)
    store_i32(record, 52, 0)
    store_i32(record, 56, -1)
    store_i32(record, 60, 0)
    store_i32(record, 64, 0)
    store_i32(record, 68, 0)
    store_i32(record, 72, 0)
    store_i32(record, 76, 0)
    store_i64(record, 80, 0)
    store_i64(record, 88, 0)
    return free_index


def _reducer_index(reducer_id: int) -> int:
    cap = _base("pcc_gui_scheduler_reducer_capacity")
    i = 0
    while i < cap:
        record = _reducer_at(i)
        if load_i32(record, 0) != 0 and load_i32(record, 4) == reducer_id:
            return i
        i = i + 1
    return -1


def _free_update_index() -> int:
    cap = _base("pcc_gui_scheduler_update_capacity")
    i = 0
    while i < cap:
        if load_i64(_update_at(i), 0) == 0:
            return i
        i = i + 1
    return -1


def _discard_slots(slots, count: int) -> None:
    if count > 0 and not ptr_is_null(slots):
        _component_discard(slots, count)


def _copy_owned_slots(source, destination, count: int) -> int:
    i = 0
    while i < count:
        src = ptr_add(source, i * SLOT_SIZE)
        dst = ptr_add(destination, i * SLOT_SIZE)
        kind = load_i32(src, 0)
        store_i32(dst, 0, kind)
        store_i32(dst, 4, load_i32(src, 4))
        value = load_i64(src, 8)
        if kind == SLOT_OPAQUE_HANDLE and value != 0:
            retained = _component_handle_retain(value)
            if retained <= 0:
                _discard_slots(destination, i)
                return ERR_OWNERSHIP
            value = retained
        elif kind != SLOT_I64 and kind != SLOT_OPAQUE_HANDLE:
            _discard_slots(destination, i)
            return ERR_OWNERSHIP
        store_i64(dst, 8, value)
        store_i64(dst, 16, load_i64(src, 16))
        i = i + 1
    return OK


def _slots_equal(left, right, count: int) -> int:
    i = 0
    while i < count:
        off = i * SLOT_SIZE
        if load_i32(left, off) != load_i32(right, off):
            return 0
        if load_i64(left, off + 8) != load_i64(right, off + 8):
            return 0
        i = i + 1
    return 1


def _release_update(index: int) -> None:
    update = _update_at(index)
    if load_i64(update, 0) == 0:
        return
    owned_handle = load_i64(update, 56)
    if owned_handle != 0:
        _component_handle_release(owned_handle)
    store_i64(update, 56, 0)
    store_i64(update, 48, -1)
    # Enqueue sequences begin at one, so zero is the in-arena free marker.
    store_i64(update, 0, 0)


def _release_base(record) -> None:
    base = int_to_ptr(load_i64(record, 16))
    count = load_i32(record, 24)
    if not ptr_is_null(base):
        _discard_slots(base, count)
        free(base)
    store_i64(record, 16, 0)
    store_i32(record, 24, 0)


def _clear_scheduler_record(record) -> None:
    _release_base(record)
    store_i32(record, 0, 0)
    store_i32(record, 4, WORK_IDLE)
    store_i64(record, 8, 0)
    store_i32(record, 28, 0)
    store_i64(record, 32, -1)
    store_i64(record, 40, -1)


def _write_error(error_out, code: int, component_id: int) -> int:
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, code)
        store_i32(error_out, 4, 0)
        store_i64(error_out, 8, component_id)
        if code == ERR_REDUCER_FAILED:
            store_ptr(error_out, 16, cstr("gui reducer failed"))
        elif code == ERR_CAPACITY:
            store_ptr(error_out, 16, cstr("gui scheduler capacity exceeded"))
        elif code == ERR_OWNERSHIP:
            store_ptr(error_out, 16, cstr("gui scheduler ownership violation"))
        elif code == ERR_STALE_NODE:
            store_ptr(error_out, 16, cstr("stale gui scheduler component"))
        else:
            store_ptr(error_out, 16, cstr("invalid gui scheduler transition"))
    return code


def _clear_outputs(effect_count_out, error_out) -> None:
    if not ptr_is_null(effect_count_out):
        store_i32(effect_count_out, 0, 0)
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, 0)
        store_i32(error_out, 4, 0)
        store_i64(error_out, 8, 0)
        store_i64(error_out, 16, 0)


@c_abi_typed_export("pcc_gui_scheduler_init", "i32", ("i64", "i64", "i64"))
def pcc_gui_scheduler_init(
    max_components: int, max_updates: int, max_reducers: int
) -> int:
    if (
        max_components <= 0
        or max_components > 1024
        or max_updates <= 0
        or max_updates > 4096
        or max_reducers <= 0
        or max_reducers > 4096
    ):
        return ERR_CAPACITY

    old_components = _base("pcc_gui_scheduler_components")
    if old_components != 0:
        cap = _base("pcc_gui_scheduler_component_capacity")
        i = 0
        while i < cap:
            record = _scheduler_at(i)
            if load_i32(record, 0) != 0:
                _release_base(record)
            i = i + 1
        free(int_to_ptr(old_components))
    old_updates = _base("pcc_gui_scheduler_updates")
    if old_updates != 0:
        cap = _base("pcc_gui_scheduler_update_capacity")
        i = 0
        while i < cap:
            _release_update(i)
            i = i + 1
        free(int_to_ptr(old_updates))
    old_reducers = _base("pcc_gui_scheduler_reducers")
    if old_reducers != 0:
        free(int_to_ptr(old_reducers))

    store_i64(global_addr("pcc_gui_scheduler_components"), 0, 0)
    store_i64(global_addr("pcc_gui_scheduler_updates"), 0, 0)
    store_i64(global_addr("pcc_gui_scheduler_reducers"), 0, 0)
    store_i64(global_addr("pcc_gui_scheduler_component_capacity"), 0, 0)
    store_i64(global_addr("pcc_gui_scheduler_update_capacity"), 0, 0)
    store_i64(global_addr("pcc_gui_scheduler_reducer_capacity"), 0, 0)

    components = calloc(max_components, SCHEDULER_COMPONENT_SIZE)
    updates = calloc(max_updates, UPDATE_SIZE)
    reducers = calloc(max_reducers, REDUCER_SIZE)
    if ptr_is_null(components) or ptr_is_null(updates) or ptr_is_null(reducers):
        if not ptr_is_null(components):
            free(components)
        if not ptr_is_null(updates):
            free(updates)
        if not ptr_is_null(reducers):
            free(reducers)
        return ERR_CAPACITY
    store_i64(
        global_addr("pcc_gui_scheduler_components"), 0, ptr_to_int(components)
    )
    store_i64(global_addr("pcc_gui_scheduler_updates"), 0, ptr_to_int(updates))
    store_i64(global_addr("pcc_gui_scheduler_reducers"), 0, ptr_to_int(reducers))
    store_i64(global_addr("pcc_gui_scheduler_component_capacity"), 0, max_components)
    store_i64(global_addr("pcc_gui_scheduler_update_capacity"), 0, max_updates)
    store_i64(global_addr("pcc_gui_scheduler_reducer_capacity"), 0, max_reducers)
    store_i64(global_addr("pcc_gui_scheduler_sequence"), 0, 1)
    store_i64(global_addr("pcc_gui_scheduler_epoch"), 0, 0)
    return OK


@c_abi_typed_export(
    "pcc_gui_scheduler_register_reducer", "i32", ("i32", "ptr", "i32")
)
def pcc_gui_scheduler_register_reducer(
    reducer_id: int, reducer_callback, flags: int
) -> int:
    if reducer_id <= 0 or ptr_is_null(reducer_callback) or flags != REDUCER_PURE:
        return ERR_OWNERSHIP
    if _reducer_index(reducer_id) >= 0:
        return -102
    cap = _base("pcc_gui_scheduler_reducer_capacity")
    i = 0
    while i < cap:
        record = _reducer_at(i)
        if load_i32(record, 0) == 0:
            store_i32(record, 4, reducer_id)
            store_i32(record, 8, flags)
            store_i64(record, 16, ptr_to_int(reducer_callback))
            store_i32(record, 0, 1)
            return OK
        i = i + 1
    return ERR_CAPACITY


def _enqueue(
    component_id: int,
    lane: int,
    slot: int,
    action: int,
    operand: int,
    reducer_id: int,
    sequence_out,
) -> int:
    if not ptr_is_null(sequence_out):
        store_i64(sequence_out, 0, 0)
    if _component_valid(component_id) == 0:
        return ERR_STALE_NODE
    if lane < LANE_DISCRETE or lane > LANE_BACKGROUND:
        return ERR_INVALID_TRANSITION
    count = _component_state_count(component_id)
    if slot < 0 or slot >= count:
        return ERR_INVALID_TRANSITION
    kind = _component_slot_kind(component_id, slot)
    if action == ACTION_REDUCE:
        if kind != SLOT_I64 or _reducer_index(reducer_id) < 0:
            return ERR_REDUCER_FAILED
    elif action == ACTION_SET:
        if kind != SLOT_I64 and kind != SLOT_OPAQUE_HANDLE:
            return ERR_OWNERSHIP
    else:
        return ERR_INVALID_TRANSITION

    scheduler_index = _scheduler_index(component_id, 1)
    if scheduler_index < 0:
        return ERR_CAPACITY
    scheduler = _scheduler_at(scheduler_index)
    work_state = load_i32(scheduler, 4)
    if work_state == WORK_EVALUATING or work_state == WORK_FAILED:
        return ERR_INVALID_TRANSITION
    if (
        action == ACTION_SET
        and kind == SLOT_I64
        and load_i32(scheduler, 28) == 0
        and _component_slot_value(component_id, slot) == operand
    ):
        return OK

    update_index = _free_update_index()
    if update_index < 0:
        return ERR_CAPACITY
    sequence = _base("pcc_gui_scheduler_sequence")
    if sequence <= 0 or sequence >= 0x7FFFFFFFFFFFFFFF:
        return ERR_CAPACITY
    owned_handle = 0
    if kind == SLOT_OPAQUE_HANDLE and operand != 0:
        owned_handle = _component_handle_retain(operand)
        if owned_handle <= 0:
            return ERR_OWNERSHIP

    store_i64(global_addr("pcc_gui_scheduler_sequence"), 0, sequence + 1)
    update = _update_at(update_index)
    store_i64(update, 0, sequence)
    store_i64(update, 8, component_id)
    store_i32(update, 16, lane)
    store_i32(update, 20, slot)
    store_i32(update, 24, action)
    store_i32(update, 28, 0)
    store_i64(update, 32, operand)
    store_i32(update, 40, reducer_id)
    store_i32(update, 44, 0)
    store_i64(update, 48, -1)
    store_i64(update, 56, owned_handle)

    tail = load_i64(scheduler, 40)
    if tail < 0:
        store_i64(scheduler, 32, update_index)
    else:
        store_i64(_update_at(tail), 48, update_index)
    store_i64(scheduler, 40, update_index)
    store_i32(scheduler, 28, load_i32(scheduler, 28) + 1)
    epoch = _base("pcc_gui_scheduler_epoch") + 1
    store_i64(global_addr("pcc_gui_scheduler_epoch"), 0, epoch)
    store_i64(scheduler, 80, epoch)
    if load_i32(scheduler, 4) == WORK_YIELDED and lane < load_i32(scheduler, 56):
        store_i32(scheduler, 60, load_i32(scheduler, 60) + 1)
        store_i32(scheduler, 4, WORK_IDLE)
    if not ptr_is_null(sequence_out):
        store_i64(sequence_out, 0, sequence)
    return OK


@c_abi_typed_export(
    "pcc_gui_scheduler_enqueue_set",
    "i32",
    ("i64", "i32", "i32", "i64", "ptr"),
)
def pcc_gui_scheduler_enqueue_set(
    component_id: int, lane: int, slot: int, value: int, sequence_out
) -> int:
    return _enqueue(component_id, lane, slot, ACTION_SET, value, 0, sequence_out)


@c_abi_typed_export(
    "pcc_gui_scheduler_enqueue_reduce",
    "i32",
    ("i64", "i32", "i32", "i32", "i64", "ptr"),
)
def pcc_gui_scheduler_enqueue_reduce(
    component_id: int,
    lane: int,
    slot: int,
    reducer_id: int,
    operand: int,
    sequence_out,
) -> int:
    return _enqueue(
        component_id, lane, slot, ACTION_REDUCE, operand, reducer_id, sequence_out
    )


@c_abi_typed_export("pcc_gui_scheduler_pending", "i32", ("i64",))
def pcc_gui_scheduler_pending(component_id: int) -> int:
    index = _scheduler_index(component_id, 0)
    if index < 0:
        return 0
    return load_i32(_scheduler_at(index), 28)


@c_abi_typed_export("pcc_gui_scheduler_cancel", "i32", ("i64",))
def pcc_gui_scheduler_cancel(component_id: int) -> int:
    index = _scheduler_index(component_id, 0)
    if index < 0:
        return OK
    record = _scheduler_at(index)
    work_state = load_i32(record, 4)
    if work_state == WORK_EVALUATING or work_state == WORK_RENDERING:
        return ERR_INVALID_TRANSITION
    update_index = load_i64(record, 32)
    while update_index >= 0:
        update = _update_at(update_index)
        nxt = load_i64(update, 48)
        _release_update(update_index)
        update_index = nxt
    _clear_scheduler_record(record)
    return OK


@c_abi_typed_export("pcc_gui_scheduler_shutdown", "i32", ())
def pcc_gui_scheduler_shutdown() -> int:
    """Release all queued work before application/component teardown."""
    if _base("pcc_gui_scheduler_components") == 0:
        return OK
    cap = _base("pcc_gui_scheduler_component_capacity")
    i = 0
    while i < cap:
        record = _scheduler_at(i)
        if load_i32(record, 0) != 0:
            state = load_i32(record, 4)
            if state == WORK_EVALUATING or state == WORK_RENDERING:
                return ERR_INVALID_TRANSITION
        i = i + 1
    i = 0
    while i < cap:
        record = _scheduler_at(i)
        if load_i32(record, 0) != 0:
            status = pcc_gui_scheduler_cancel(load_i64(record, 8))
            if status != OK:
                return status
        i = i + 1
    return OK


def _lane_pending(record, lane: int) -> int:
    update_index = load_i64(record, 32)
    while update_index >= 0:
        update = _update_at(update_index)
        if (
            load_i64(update, 0) != 0
            and (load_i32(update, 28) & UPDATE_REPLAY_ALWAYS) == 0
            and load_i32(update, 16) == lane
        ):
            return 1
        update_index = load_i64(update, 48)
    return 0


def _select_lane(record) -> int:
    if _lane_pending(record, LANE_BACKGROUND) != 0 and load_i32(record, 76) >= 32:
        return LANE_BACKGROUND
    if _lane_pending(record, LANE_DEFAULT) != 0 and load_i32(record, 72) >= 8:
        return LANE_DEFAULT
    if _lane_pending(record, LANE_ANIMATION) != 0 and load_i32(record, 68) >= 2:
        return LANE_ANIMATION
    lane = LANE_DISCRETE
    while lane <= LANE_BACKGROUND:
        if _lane_pending(record, lane) != 0:
            return lane
        lane = lane + 1
    return -1


def _age_pending_lanes(record, selected_lane: int) -> None:
    lane = LANE_DISCRETE
    while lane <= LANE_BACKGROUND:
        off = 64 + lane * 4
        if lane == selected_lane or _lane_pending(record, lane) == 0:
            store_i32(record, off, 0)
        else:
            age = load_i32(record, off)
            if age < 0x7FFFFFFF:
                store_i32(record, off, age + 1)
        lane = lane + 1


def _apply_update(work, update) -> int:
    slot_index = load_i32(update, 20)
    slot = ptr_add(work, slot_index * SLOT_SIZE)
    kind = load_i32(slot, 0)
    action = load_i32(update, 24)
    old_value = load_i64(slot, 8)
    new_value = old_value
    if action == ACTION_SET:
        new_value = load_i64(update, 32)
        if kind == SLOT_OPAQUE_HANDLE:
            owned = load_i64(update, 56)
            if owned != 0:
                new_value = _component_handle_retain(owned)
                if new_value <= 0:
                    return ERR_OWNERSHIP
            if new_value == old_value:
                if new_value != 0:
                    _component_handle_release(new_value)
            elif old_value != 0:
                _component_handle_release(old_value)
        elif kind != SLOT_I64:
            return ERR_OWNERSHIP
    elif action == ACTION_REDUCE:
        if kind != SLOT_I64:
            return ERR_REDUCER_FAILED
        reducer_index = _reducer_index(load_i32(update, 40))
        if reducer_index < 0:
            return ERR_REDUCER_FAILED
        result_out = stack_alloc(8)
        store_i64(result_out, 0, old_value)
        reducer = _reducer_at(reducer_index)
        flags = load_i32(update, 28)
        if (flags & UPDATE_REDUCER_EVALUATED) != 0:
            store_i32(update, 44, load_i32(update, 44) + 1)
        else:
            store_i32(update, 28, flags | UPDATE_REDUCER_EVALUATED)
        status = call_i32_i64_i64_ptr(
            int_to_ptr(load_i64(reducer, 16)),
            old_value,
            load_i64(update, 32),
            result_out,
        )
        if status != 0:
            return ERR_REDUCER_FAILED
        new_value = load_i64(result_out, 0)
    else:
        return ERR_INVALID_TRANSITION
    if new_value != old_value:
        store_i64(slot, 8, new_value)
        store_i64(slot, 16, load_i64(slot, 16) + 1)
    return OK


def _free_run_buffers(current, work, new_base, post_base, dispositions) -> None:
    if not ptr_is_null(current):
        free(current)
    if not ptr_is_null(work):
        free(work)
    if not ptr_is_null(new_base):
        free(new_base)
    if not ptr_is_null(post_base):
        free(post_base)
    if not ptr_is_null(dispositions):
        free(dispositions)


def _run_lane(
    component_id: int,
    selected_lane: int,
    budget: int,
    descriptor_arena,
    descriptor_capacity: int,
    effect_arena,
    effect_capacity: int,
    effect_count_out,
    error_out,
) -> int:
    scheduler_index = _scheduler_index(component_id, 0)
    if scheduler_index < 0:
        return OK
    scheduler = _scheduler_at(scheduler_index)
    if budget <= 0:
        return _write_error(error_out, ERR_INVALID_TRANSITION, component_id)
    work_state = load_i32(scheduler, 4)
    if work_state != WORK_IDLE and work_state != WORK_YIELDED:
        return _write_error(error_out, ERR_INVALID_TRANSITION, component_id)
    state_count = _component_state_count(component_id)
    if state_count < 0 or state_count > 64:
        return _write_error(error_out, ERR_STALE_NODE, component_id)
    store_i32(scheduler, 4, WORK_EVALUATING)
    slot_alloc = state_count if state_count > 0 else 1
    update_cap = _base("pcc_gui_scheduler_update_capacity")
    current = calloc(slot_alloc, SLOT_SIZE)
    work = calloc(slot_alloc, SLOT_SIZE)
    new_base = calloc(slot_alloc, SLOT_SIZE)
    post_base = calloc(slot_alloc, SLOT_SIZE)
    dispositions = calloc(update_cap, 4)
    if (
        ptr_is_null(current)
        or ptr_is_null(work)
        or ptr_is_null(new_base)
        or ptr_is_null(post_base)
        or ptr_is_null(dispositions)
    ):
        store_i32(scheduler, 4, WORK_FAILED)
        _free_run_buffers(current, work, new_base, post_base, dispositions)
        return _write_error(error_out, ERR_CAPACITY, component_id)
    if _component_snapshot(component_id, current, state_count) != state_count:
        store_i32(scheduler, 4, WORK_FAILED)
        _free_run_buffers(current, work, new_base, post_base, dispositions)
        return _write_error(error_out, ERR_OWNERSHIP, component_id)
    base_ptr = int_to_ptr(load_i64(scheduler, 16))
    if not ptr_is_null(base_ptr):
        status = _copy_owned_slots(base_ptr, work, state_count)
    else:
        status = _copy_owned_slots(current, work, state_count)
    if status != OK:
        store_i32(scheduler, 4, WORK_FAILED)
        _discard_slots(current, state_count)
        _free_run_buffers(current, work, new_base, post_base, dispositions)
        return _write_error(error_out, status, component_id)

    original_head = load_i64(scheduler, 32)
    original_tail = load_i64(scheduler, 40)
    update_index = original_head
    steps = 0
    skipped = 0
    new_base_valid = 0
    remaining_old = 0
    while update_index >= 0:
        if steps >= budget:
            _discard_slots(current, state_count)
            _discard_slots(work, state_count)
            if new_base_valid != 0:
                _discard_slots(new_base, state_count)
            store_i32(scheduler, 4, WORK_YIELDED)
            store_i32(scheduler, 56, selected_lane)
            store_i64(scheduler, 88, load_i64(scheduler, 80))
            _free_run_buffers(current, work, new_base, post_base, dispositions)
            return YIELDED
        update = _update_at(update_index)
        flags = load_i32(update, 28)
        selected = 1 if (flags & UPDATE_REPLAY_ALWAYS) != 0 else 0
        if load_i32(update, 16) == selected_lane:
            selected = 1
        if selected != 0:
            status = _apply_update(work, update)
            if status != OK:
                _discard_slots(current, state_count)
                _discard_slots(work, state_count)
                if new_base_valid != 0:
                    _discard_slots(new_base, state_count)
                store_i32(scheduler, 4, WORK_FAILED)
                _free_run_buffers(current, work, new_base, post_base, dispositions)
                return _write_error(error_out, status, component_id)
            if skipped != 0:
                store_i32(dispositions, update_index * 4, 2)
                remaining_old = remaining_old + 1
            else:
                store_i32(dispositions, update_index * 4, 0)
        else:
            if skipped == 0:
                status = _copy_owned_slots(work, new_base, state_count)
                if status != OK:
                    store_i32(scheduler, 4, WORK_FAILED)
                    _discard_slots(current, state_count)
                    _discard_slots(work, state_count)
                    _free_run_buffers(
                        current, work, new_base, post_base, dispositions
                    )
                    return _write_error(error_out, status, component_id)
                new_base_valid = 1
                skipped = 1
            store_i32(dispositions, update_index * 4, 1)
            remaining_old = remaining_old + 1
        steps = steps + 1
        if update_index == original_tail:
            update_index = -1
        else:
            update_index = load_i64(update, 48)

    changed = 1 - _slots_equal(current, work, state_count)
    post_base_valid = 0
    if changed != 0:
        status = _copy_owned_slots(work, post_base, state_count)
        if status != OK:
            store_i32(scheduler, 4, WORK_FAILED)
            _discard_slots(current, state_count)
            _discard_slots(work, state_count)
            if new_base_valid != 0:
                _discard_slots(new_base, state_count)
            _free_run_buffers(current, work, new_base, post_base, dispositions)
            return _write_error(error_out, status, component_id)
        post_base_valid = 1
        store_i32(scheduler, 4, WORK_RENDERING)
        status = _component_replace_owned(component_id, work, state_count)
        if status != OK:
            store_i32(scheduler, 4, WORK_FAILED)
            _discard_slots(current, state_count)
            _discard_slots(work, state_count)
            _discard_slots(post_base, state_count)
            if new_base_valid != 0:
                _discard_slots(new_base, state_count)
            _free_run_buffers(current, work, new_base, post_base, dispositions)
            return _write_error(error_out, status, component_id)
        store_i32(scheduler, 52, load_i32(scheduler, 52) + 1)
        status = _component_render_commit(
            component_id,
            descriptor_arena,
            descriptor_capacity,
            effect_arena,
            effect_capacity,
            effect_count_out,
            error_out,
        )
        if status != OK:
            rollback_status = _component_replace_owned(
                component_id, current, state_count
            )
            if rollback_status != OK:
                _discard_slots(current, state_count)
            _discard_slots(post_base, state_count)
            if new_base_valid != 0:
                _discard_slots(new_base, state_count)
            _free_run_buffers(current, work, new_base, post_base, dispositions)
            store_i32(scheduler, 4, WORK_FAILED)
            return status
        _discard_slots(current, state_count)
    else:
        _discard_slots(current, state_count)
        _discard_slots(work, state_count)

    # A render callback may enqueue more work.  It starts after the frozen tail
    # and is never consumed by this commit.
    post_head = -1
    if original_tail >= 0:
        post_head = load_i64(_update_at(original_tail), 48)
    if post_head >= 0 and post_base_valid == 0:
        # Enqueue is admitted only while the render callback owns a complete
        # candidate state.  Observing appended work without that state means a
        # forbidden concurrent/reentrant transition; keep the original queue
        # and committed component untouched.
        if new_base_valid != 0:
            _discard_slots(new_base, state_count)
        store_i32(scheduler, 4, WORK_FAILED)
        _free_run_buffers(current, work, new_base, post_base, dispositions)
        return _write_error(error_out, ERR_INVALID_TRANSITION, component_id)

    new_head = -1
    new_tail = -1
    update_index = original_head
    while update_index >= 0:
        update = _update_at(update_index)
        nxt = load_i64(update, 48)
        disposition = load_i32(dispositions, update_index * 4)
        if disposition == 0:
            _release_update(update_index)
        else:
            if disposition == 2:
                store_i32(update, 28, load_i32(update, 28) | UPDATE_REPLAY_ALWAYS)
            if new_head < 0:
                new_head = update_index
            else:
                store_i64(_update_at(new_tail), 48, update_index)
            new_tail = update_index
            store_i64(update, 48, -1)
        if update_index == original_tail:
            update_index = -1
        else:
            update_index = nxt

    post_count = 0
    post_tail = -1
    update_index = post_head
    while update_index >= 0:
        post_count = post_count + 1
        post_tail = update_index
        update_index = load_i64(_update_at(update_index), 48)
    if post_head >= 0:
        if new_head < 0:
            new_head = post_head
        else:
            store_i64(_update_at(new_tail), 48, post_head)
        new_tail = post_tail

    _release_base(scheduler)
    if remaining_old > 0:
        store_i64(scheduler, 16, ptr_to_int(new_base))
        store_i32(scheduler, 24, state_count)
        new_base = int_to_ptr(0)
        if post_base_valid != 0:
            _discard_slots(post_base, state_count)
    elif post_count > 0:
        store_i64(scheduler, 16, ptr_to_int(post_base))
        store_i32(scheduler, 24, state_count)
        post_base = int_to_ptr(0)
    else:
        if new_base_valid != 0:
            _discard_slots(new_base, state_count)
        if post_base_valid != 0:
            _discard_slots(post_base, state_count)

    store_i64(scheduler, 32, new_head)
    store_i64(scheduler, 40, new_tail)
    store_i32(scheduler, 28, remaining_old + post_count)
    store_i32(scheduler, 4, WORK_IDLE)
    store_i32(scheduler, 56, selected_lane)
    _age_pending_lanes(scheduler, selected_lane)
    _free_run_buffers(current, work, new_base, post_base, dispositions)
    return OK


@c_abi_typed_export(
    "pcc_gui_scheduler_run_sync",
    "i32",
    ("i64", "ptr", "i32", "ptr", "i32", "ptr", "ptr"),
)
def pcc_gui_scheduler_run_sync(
    component_id: int,
    descriptor_arena,
    descriptor_capacity: int,
    effect_arena,
    effect_capacity: int,
    effect_count_out,
    error_out,
) -> int:
    _clear_outputs(effect_count_out, error_out)
    index = _scheduler_index(component_id, 0)
    if index < 0 or _lane_pending(_scheduler_at(index), LANE_DISCRETE) == 0:
        return OK
    return _run_lane(
        component_id,
        LANE_DISCRETE,
        _base("pcc_gui_scheduler_update_capacity") + 1,
        descriptor_arena,
        descriptor_capacity,
        effect_arena,
        effect_capacity,
        effect_count_out,
        error_out,
    )


@c_abi_typed_export(
    "pcc_gui_scheduler_run_budgeted",
    "i32",
    ("i64", "i64", "ptr", "i32", "ptr", "i32", "ptr", "ptr"),
)
def pcc_gui_scheduler_run_budgeted(
    component_id: int,
    budget: int,
    descriptor_arena,
    descriptor_capacity: int,
    effect_arena,
    effect_capacity: int,
    effect_count_out,
    error_out,
) -> int:
    _clear_outputs(effect_count_out, error_out)
    index = _scheduler_index(component_id, 0)
    if index < 0:
        return OK
    lane = _select_lane(_scheduler_at(index))
    if lane < 0:
        return OK
    return _run_lane(
        component_id,
        lane,
        budget,
        descriptor_arena,
        descriptor_capacity,
        effect_arena,
        effect_capacity,
        effect_count_out,
        error_out,
    )


@c_abi_typed_export("pcc_gui_scheduler_last_lane", "i32", ("i64",))
def pcc_gui_scheduler_last_lane(component_id: int) -> int:
    index = _scheduler_index(component_id, 0)
    if index < 0:
        return -1
    return load_i32(_scheduler_at(index), 56)


@c_abi_typed_export("pcc_gui_scheduler_restart_count", "i32", ("i64",))
def pcc_gui_scheduler_restart_count(component_id: int) -> int:
    index = _scheduler_index(component_id, 0)
    if index < 0:
        return 0
    return load_i32(_scheduler_at(index), 60)


@c_abi_typed_export("pcc_gui_scheduler_render_count", "i32", ("i64",))
def pcc_gui_scheduler_render_count(component_id: int) -> int:
    index = _scheduler_index(component_id, 0)
    if index < 0:
        return 0
    return load_i32(_scheduler_at(index), 52)


@c_abi_typed_export("pcc_gui_scheduler_work_state", "i32", ("i64",))
def pcc_gui_scheduler_work_state(component_id: int) -> int:
    index = _scheduler_index(component_id, 0)
    if index < 0:
        return WORK_IDLE
    return load_i32(_scheduler_at(index), 4)


@c_abi_typed_export("pcc_gui_scheduler_can_unmount", "i32", ("i64",))
def pcc_gui_scheduler_can_unmount(component_id: int) -> int:
    state = pcc_gui_scheduler_work_state(component_id)
    return 0 if state == WORK_EVALUATING or state == WORK_RENDERING else 1
