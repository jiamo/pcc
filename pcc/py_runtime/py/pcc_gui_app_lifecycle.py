"""Webview-free application run-event and shutdown state machine.

The selected v1 event set is intentionally smaller than Tauri's surface:
Ready, Resumed, MainEventsCleared, native WindowEvent, Darwin Opened/Reopen,
cancellable ExitRequested, and exactly one terminal Exit. Native adapters
copy payload bytes into this owner's bounded queue before returning.

An accepted exit performs the owned cleanup chain in one order: scheduler
work, command resolvers/state, components/listeners/effects, passive effects,
then the retained native window handle. Only after that chain succeeds is
Exit delivered. Webview events have no kind and cannot enter this module.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import (
    call_i32_ptr1,
    call_i64_i64,
    calloc,
    cstr,
    define_global_i64,
    free,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    memcpy,
    null,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


APP_EVENT_SIZE = 48
MAX_EVENT_PAYLOAD = 256

EVENT_READY = 1
EVENT_RESUMED = 2
EVENT_MAIN_EVENTS_CLEARED = 3
EVENT_WINDOW = 4
EVENT_OPENED = 5
EVENT_REOPEN = 6
EVENT_EXIT_REQUESTED = 7
EVENT_EXIT = 8

EVENT_FLAG_EXIT_CODE = 1

APP_UNINITIALIZED = 0
APP_CREATED = 1
APP_READY = 2
APP_RESUMED = 3
APP_ACTIVE = 4
APP_EXIT_REQUESTED = 5
APP_TERMINATING = 6
APP_EXITED = 7

OK = 0
CANCEL = 1
ERR_CAPACITY = -101
ERR_INVALID_TRANSITION = -103
ERR_OWNERSHIP = -105
ERR_LATE = -108
ERR_INVALID_PAYLOAD = -112
ERR_TEARDOWN = -115
ERR_CALLBACK_FAILED = -116

PHASE_APP_EVENT = 10
PHASE_APP_DRAIN = 11
PHASE_APP_TEARDOWN = 12


_scheduler_shutdown = extern("pcc_gui_scheduler_shutdown", (), c_int32)
_commands_shutdown = extern("pcc_gui_commands_shutdown", (), c_int64)
_components_shutdown = extern("pcc_gui_components_shutdown", (), c_int32)
_events_shutdown = extern("pcc_gui_events_shutdown", (c_ptr,), c_int32)


define_global_i64("pcc_gui_app_lifecycle_records", 0)
define_global_i64("pcc_gui_app_lifecycle_payloads", 0)
define_global_i64("pcc_gui_app_lifecycle_capacity", 0)
define_global_i64("pcc_gui_app_lifecycle_head", 0)
define_global_i64("pcc_gui_app_lifecycle_tail", 0)
define_global_i64("pcc_gui_app_lifecycle_sequence", 1)
define_global_i64("pcc_gui_app_lifecycle_state_value", 0)
define_global_i64("pcc_gui_app_lifecycle_callback", 0)
define_global_i64("pcc_gui_app_lifecycle_work_drain", 0)
define_global_i64("pcc_gui_app_lifecycle_window_id", 0)
define_global_i64("pcc_gui_app_lifecycle_window_handle", 0)
define_global_i64("pcc_gui_app_lifecycle_window_release", 0)
define_global_i64("pcc_gui_app_lifecycle_cancel_used", 0)
define_global_i64("pcc_gui_app_lifecycle_terminal_count_value", 0)


def _base(name: str) -> int:
    if name == "pcc_gui_app_lifecycle_records":
        return load_i64(global_addr("pcc_gui_app_lifecycle_records"), 0)
    if name == "pcc_gui_app_lifecycle_payloads":
        return load_i64(global_addr("pcc_gui_app_lifecycle_payloads"), 0)
    if name == "pcc_gui_app_lifecycle_capacity":
        return load_i64(global_addr("pcc_gui_app_lifecycle_capacity"), 0)
    if name == "pcc_gui_app_lifecycle_head":
        return load_i64(global_addr("pcc_gui_app_lifecycle_head"), 0)
    if name == "pcc_gui_app_lifecycle_tail":
        return load_i64(global_addr("pcc_gui_app_lifecycle_tail"), 0)
    if name == "pcc_gui_app_lifecycle_sequence":
        return load_i64(global_addr("pcc_gui_app_lifecycle_sequence"), 0)
    if name == "pcc_gui_app_lifecycle_state_value":
        return load_i64(global_addr("pcc_gui_app_lifecycle_state_value"), 0)
    if name == "pcc_gui_app_lifecycle_callback":
        return load_i64(global_addr("pcc_gui_app_lifecycle_callback"), 0)
    if name == "pcc_gui_app_lifecycle_work_drain":
        return load_i64(global_addr("pcc_gui_app_lifecycle_work_drain"), 0)
    if name == "pcc_gui_app_lifecycle_window_id":
        return load_i64(global_addr("pcc_gui_app_lifecycle_window_id"), 0)
    if name == "pcc_gui_app_lifecycle_window_handle":
        return load_i64(global_addr("pcc_gui_app_lifecycle_window_handle"), 0)
    if name == "pcc_gui_app_lifecycle_window_release":
        return load_i64(global_addr("pcc_gui_app_lifecycle_window_release"), 0)
    if name == "pcc_gui_app_lifecycle_cancel_used":
        return load_i64(global_addr("pcc_gui_app_lifecycle_cancel_used"), 0)
    if name == "pcc_gui_app_lifecycle_terminal_count_value":
        return load_i64(
            global_addr("pcc_gui_app_lifecycle_terminal_count_value"), 0
        )
    return 0


def _set(name: str, value: int) -> None:
    if name == "pcc_gui_app_lifecycle_records":
        store_i64(global_addr("pcc_gui_app_lifecycle_records"), 0, value)
    elif name == "pcc_gui_app_lifecycle_payloads":
        store_i64(global_addr("pcc_gui_app_lifecycle_payloads"), 0, value)
    elif name == "pcc_gui_app_lifecycle_capacity":
        store_i64(global_addr("pcc_gui_app_lifecycle_capacity"), 0, value)
    elif name == "pcc_gui_app_lifecycle_head":
        store_i64(global_addr("pcc_gui_app_lifecycle_head"), 0, value)
    elif name == "pcc_gui_app_lifecycle_tail":
        store_i64(global_addr("pcc_gui_app_lifecycle_tail"), 0, value)
    elif name == "pcc_gui_app_lifecycle_sequence":
        store_i64(global_addr("pcc_gui_app_lifecycle_sequence"), 0, value)
    elif name == "pcc_gui_app_lifecycle_state_value":
        store_i64(global_addr("pcc_gui_app_lifecycle_state_value"), 0, value)
    elif name == "pcc_gui_app_lifecycle_callback":
        store_i64(global_addr("pcc_gui_app_lifecycle_callback"), 0, value)
    elif name == "pcc_gui_app_lifecycle_work_drain":
        store_i64(global_addr("pcc_gui_app_lifecycle_work_drain"), 0, value)
    elif name == "pcc_gui_app_lifecycle_window_id":
        store_i64(global_addr("pcc_gui_app_lifecycle_window_id"), 0, value)
    elif name == "pcc_gui_app_lifecycle_window_handle":
        store_i64(global_addr("pcc_gui_app_lifecycle_window_handle"), 0, value)
    elif name == "pcc_gui_app_lifecycle_window_release":
        store_i64(global_addr("pcc_gui_app_lifecycle_window_release"), 0, value)
    elif name == "pcc_gui_app_lifecycle_cancel_used":
        store_i64(global_addr("pcc_gui_app_lifecycle_cancel_used"), 0, value)
    elif name == "pcc_gui_app_lifecycle_terminal_count_value":
        store_i64(
            global_addr("pcc_gui_app_lifecycle_terminal_count_value"), 0, value
        )


def _event_at(position: int):
    cap = _base("pcc_gui_app_lifecycle_capacity")
    return int_to_ptr(
        _base("pcc_gui_app_lifecycle_records")
        + (position % cap) * APP_EVENT_SIZE
    )


def _payload_at(position: int):
    cap = _base("pcc_gui_app_lifecycle_capacity")
    return int_to_ptr(
        _base("pcc_gui_app_lifecycle_payloads")
        + (position % cap) * MAX_EVENT_PAYLOAD
    )


def _message(code: int):
    if code == ERR_CAPACITY:
        return cstr("gui app event queue capacity exceeded")
    if code == ERR_OWNERSHIP:
        return cstr("gui app lifecycle ownership violation")
    if code == ERR_LATE:
        return cstr("gui app event arrived after terminal Exit")
    if code == ERR_INVALID_PAYLOAD:
        return cstr("invalid gui app event payload")
    if code == ERR_TEARDOWN:
        return cstr("gui app teardown failed")
    if code == ERR_CALLBACK_FAILED:
        return cstr("gui app callback failed")
    return cstr("invalid gui app event transition")


def _clear_error(error_out) -> None:
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, 0)
        store_i32(error_out, 4, 0)
        store_i64(error_out, 8, 0)
        store_ptr(error_out, 16, null())


def _write_error(error_out, code: int, phase: int, subject: int) -> int:
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, code)
        store_i32(error_out, 4, phase)
        store_i64(error_out, 8, subject)
        store_ptr(error_out, 16, _message(code))
    return code


def _payload_shape_valid(kind: int, payload, length: int, flags: int) -> int:
    if length < 0 or length > MAX_EVENT_PAYLOAD:
        return 0
    if kind == EVENT_WINDOW or kind == EVENT_OPENED:
        return 1 if length > 0 and not ptr_is_null(payload) else 0
    if kind == EVENT_EXIT_REQUESTED:
        if length != 0 or not ptr_is_null(payload):
            return 0
        return 1 if flags == 0 or flags == EVENT_FLAG_EXIT_CODE else 0
    if (
        kind == EVENT_READY
        or kind == EVENT_RESUMED
        or kind == EVENT_MAIN_EVENTS_CLEARED
        or kind == EVENT_REOPEN
    ):
        return 1 if length == 0 and ptr_is_null(payload) and flags == 0 else 0
    return 0


@c_abi_typed_export(
    "pcc_gui_app_lifecycle_init",
    "i32",
    ("i64", "ptr", "i64", "i64", "ptr", "ptr"),
)
def pcc_gui_app_lifecycle_init(
    capacity: int,
    callback,
    window_id: int,
    window_handle: int,
    window_release,
    work_drain,
) -> int:
    if (
        capacity <= 0
        or capacity > 256
        or ptr_is_null(callback)
        or window_id < 0
        or (window_handle == 0 and not ptr_is_null(window_release))
        or (window_handle != 0 and ptr_is_null(window_release))
    ):
        return ERR_OWNERSHIP
    if _base("pcc_gui_app_lifecycle_state_value") != APP_UNINITIALIZED:
        return ERR_INVALID_TRANSITION
    records = calloc(capacity, APP_EVENT_SIZE)
    payloads = calloc(capacity, MAX_EVENT_PAYLOAD)
    if ptr_is_null(records) or ptr_is_null(payloads):
        if not ptr_is_null(records):
            free(records)
        if not ptr_is_null(payloads):
            free(payloads)
        return ERR_CAPACITY
    _set("pcc_gui_app_lifecycle_records", ptr_to_int(records))
    _set("pcc_gui_app_lifecycle_payloads", ptr_to_int(payloads))
    _set("pcc_gui_app_lifecycle_capacity", capacity)
    _set("pcc_gui_app_lifecycle_head", 0)
    _set("pcc_gui_app_lifecycle_tail", 0)
    _set("pcc_gui_app_lifecycle_sequence", 1)
    _set("pcc_gui_app_lifecycle_callback", ptr_to_int(callback))
    _set(
        "pcc_gui_app_lifecycle_work_drain",
        0 if ptr_is_null(work_drain) else ptr_to_int(work_drain),
    )
    _set("pcc_gui_app_lifecycle_window_id", window_id)
    _set("pcc_gui_app_lifecycle_window_handle", window_handle)
    _set(
        "pcc_gui_app_lifecycle_window_release",
        0 if ptr_is_null(window_release) else ptr_to_int(window_release),
    )
    _set("pcc_gui_app_lifecycle_cancel_used", 0)
    _set("pcc_gui_app_lifecycle_terminal_count_value", 0)
    _set("pcc_gui_app_lifecycle_state_value", APP_CREATED)
    return OK


@c_abi_typed_export(
    "pcc_gui_app_lifecycle_post",
    "i32",
    ("i32", "i64", "ptr", "i64", "i32", "i32"),
)
def pcc_gui_app_lifecycle_post(
    kind: int,
    window_id: int,
    payload,
    payload_length: int,
    flags: int,
    exit_code: int,
) -> int:
    state = _base("pcc_gui_app_lifecycle_state_value")
    if state == APP_UNINITIALIZED:
        return ERR_OWNERSHIP
    if state == APP_EXITED or state == APP_TERMINATING:
        return ERR_LATE
    if kind == EVENT_EXIT or kind < EVENT_READY or kind > EVENT_EXIT_REQUESTED:
        return ERR_INVALID_TRANSITION
    if _payload_shape_valid(kind, payload, payload_length, flags) == 0:
        return ERR_INVALID_PAYLOAD
    head = _base("pcc_gui_app_lifecycle_head")
    tail = _base("pcc_gui_app_lifecycle_tail")
    cap = _base("pcc_gui_app_lifecycle_capacity")
    if tail - head >= cap:
        return ERR_CAPACITY
    sequence = _base("pcc_gui_app_lifecycle_sequence")
    if sequence <= 0 or sequence >= 0x7FFFFFFFFFFFFFFF:
        return ERR_CAPACITY
    record = _event_at(tail)
    stable_payload = _payload_at(tail)
    if payload_length > 0:
        memcpy(stable_payload, payload, payload_length)
    store_i64(record, 0, sequence)
    store_i32(record, 8, kind)
    store_i32(record, 12, flags)
    store_i64(record, 16, window_id)
    store_ptr(record, 24, stable_payload if payload_length > 0 else null())
    store_i64(record, 32, payload_length)
    store_i32(record, 40, exit_code if (flags & EVENT_FLAG_EXIT_CODE) != 0 else 0)
    store_i32(record, 44, 0)
    _set("pcc_gui_app_lifecycle_sequence", sequence + 1)
    _set("pcc_gui_app_lifecycle_tail", tail + 1)
    return OK


def _call_app(record) -> int:
    callback = _base("pcc_gui_app_lifecycle_callback")
    if callback == 0:
        return ERR_CALLBACK_FAILED
    status = call_i32_ptr1(int_to_ptr(callback), record)
    store_i32(record, 44, status)
    return status


def _release_native_window() -> int:
    handle = _base("pcc_gui_app_lifecycle_window_handle")
    callback = _base("pcc_gui_app_lifecycle_window_release")
    if handle == 0:
        return OK
    if callback == 0:
        return ERR_TEARDOWN
    _set("pcc_gui_app_lifecycle_window_handle", 0)
    return OK if call_i64_i64(int_to_ptr(callback), handle) == 0 else ERR_TEARDOWN


def _release_event_storage() -> None:
    records = _base("pcc_gui_app_lifecycle_records")
    payloads = _base("pcc_gui_app_lifecycle_payloads")
    _set("pcc_gui_app_lifecycle_records", 0)
    _set("pcc_gui_app_lifecycle_payloads", 0)
    _set("pcc_gui_app_lifecycle_capacity", 0)
    _set("pcc_gui_app_lifecycle_head", 0)
    _set("pcc_gui_app_lifecycle_tail", 0)
    if records != 0:
        free(int_to_ptr(records))
    if payloads != 0:
        free(int_to_ptr(payloads))


def _finish_exit(exit_code: int, error_out) -> int:
    # Teardown is an ownership transaction, not a short-circuit chain.  Record
    # the first failing owner but continue through every later owner so a
    # callback failure cannot leak another subsystem's handles.
    failure_subject = 0
    status = _scheduler_shutdown()
    if status != OK:
        failure_subject = 1
    _commands_shutdown()
    status = _components_shutdown()
    if status != OK and failure_subject == 0:
        failure_subject = 2
    status = _events_shutdown(null())
    if status != OK and failure_subject == 0:
        failure_subject = 3
    status = _release_native_window()
    if status != OK and failure_subject == 0:
        failure_subject = 4
    _release_event_storage()
    if failure_subject != 0:
        return _write_error(
            error_out, ERR_TEARDOWN, PHASE_APP_TEARDOWN, failure_subject
        )
    # stack_alloc is a compile-time intrinsic; keep the frozen ABI size literal.
    terminal = stack_alloc(48)
    sequence = _base("pcc_gui_app_lifecycle_sequence")
    store_i64(terminal, 0, sequence)
    store_i32(terminal, 8, EVENT_EXIT)
    store_i32(terminal, 12, EVENT_FLAG_EXIT_CODE)
    store_i64(terminal, 16, _base("pcc_gui_app_lifecycle_window_id"))
    store_ptr(terminal, 24, null())
    store_i64(terminal, 32, 0)
    store_i32(terminal, 40, exit_code)
    store_i32(terminal, 44, 0)
    _set("pcc_gui_app_lifecycle_sequence", sequence + 1)
    _set("pcc_gui_app_lifecycle_head", _base("pcc_gui_app_lifecycle_tail"))
    _set("pcc_gui_app_lifecycle_terminal_count_value", 1)
    _set("pcc_gui_app_lifecycle_state_value", APP_EXITED)
    callback_status = _call_app(terminal)
    if callback_status != OK:
        return _write_error(
            error_out, ERR_CALLBACK_FAILED, PHASE_APP_EVENT, EVENT_EXIT
        )
    return OK


def _process_event(record, error_out) -> int:
    kind = load_i32(record, 8)
    state = _base("pcc_gui_app_lifecycle_state_value")
    if kind == EVENT_READY:
        if state != APP_CREATED:
            return _write_error(
                error_out, ERR_INVALID_TRANSITION, PHASE_APP_EVENT, kind
            )
        callback_status = _call_app(record)
        if callback_status != OK:
            return _write_error(
                error_out, ERR_CALLBACK_FAILED, PHASE_APP_EVENT, kind
            )
        _set("pcc_gui_app_lifecycle_state_value", APP_READY)
        return OK
    if kind == EVENT_RESUMED:
        if state != APP_READY:
            return _write_error(
                error_out, ERR_INVALID_TRANSITION, PHASE_APP_EVENT, kind
            )
        callback_status = _call_app(record)
        if callback_status != OK:
            return _write_error(
                error_out, ERR_CALLBACK_FAILED, PHASE_APP_EVENT, kind
            )
        _set("pcc_gui_app_lifecycle_state_value", APP_RESUMED)
        return OK
    if kind == EVENT_MAIN_EVENTS_CLEARED:
        if state != APP_RESUMED and state != APP_ACTIVE:
            return _write_error(
                error_out, ERR_INVALID_TRANSITION, PHASE_APP_EVENT, kind
            )
        drain = _base("pcc_gui_app_lifecycle_work_drain")
        if drain != 0 and call_i32_ptr1(int_to_ptr(drain), null()) != OK:
            return _write_error(
                error_out, ERR_CALLBACK_FAILED, PHASE_APP_DRAIN, kind
            )
        callback_status = _call_app(record)
        if callback_status != OK:
            return _write_error(
                error_out, ERR_CALLBACK_FAILED, PHASE_APP_EVENT, kind
            )
        _set("pcc_gui_app_lifecycle_state_value", APP_ACTIVE)
        return OK
    if kind == EVENT_WINDOW or kind == EVENT_OPENED or kind == EVENT_REOPEN:
        if state != APP_ACTIVE:
            return _write_error(
                error_out, ERR_INVALID_TRANSITION, PHASE_APP_EVENT, kind
            )
        callback_status = _call_app(record)
        if callback_status != OK:
            return _write_error(
                error_out, ERR_CALLBACK_FAILED, PHASE_APP_EVENT, kind
            )
        return OK
    if kind == EVENT_EXIT_REQUESTED:
        if state != APP_ACTIVE:
            return _write_error(
                error_out, ERR_INVALID_TRANSITION, PHASE_APP_EVENT, kind
            )
        _set("pcc_gui_app_lifecycle_state_value", APP_EXIT_REQUESTED)
        callback_status = _call_app(record)
        if callback_status == CANCEL:
            _set("pcc_gui_app_lifecycle_state_value", APP_ACTIVE)
            if _base("pcc_gui_app_lifecycle_cancel_used") != 0:
                return _write_error(
                    error_out, ERR_INVALID_TRANSITION, PHASE_APP_EVENT, kind
                )
            _set("pcc_gui_app_lifecycle_cancel_used", 1)
            return CANCEL
        if callback_status != OK:
            _set("pcc_gui_app_lifecycle_state_value", APP_ACTIVE)
            return _write_error(
                error_out, ERR_CALLBACK_FAILED, PHASE_APP_EVENT, kind
            )
        _set("pcc_gui_app_lifecycle_state_value", APP_TERMINATING)
        return _finish_exit(load_i32(record, 40), error_out)
    return _write_error(
        error_out, ERR_INVALID_TRANSITION, PHASE_APP_EVENT, kind
    )


@c_abi_typed_export("pcc_gui_app_lifecycle_drain", "i32", ("i32", "ptr"))
def pcc_gui_app_lifecycle_drain(limit: int, error_out) -> int:
    _clear_error(error_out)
    if limit <= 0:
        return _write_error(
            error_out, ERR_INVALID_TRANSITION, PHASE_APP_DRAIN, limit
        )
    completed = 0
    while completed < limit:
        head = _base("pcc_gui_app_lifecycle_head")
        tail = _base("pcc_gui_app_lifecycle_tail")
        if head >= tail:
            return completed
        record = _event_at(head)
        _set("pcc_gui_app_lifecycle_head", head + 1)
        status = _process_event(record, error_out)
        if status < 0:
            return status
        completed = completed + 1
        if _base("pcc_gui_app_lifecycle_state_value") == APP_EXITED:
            return completed
    return completed


@c_abi_typed_export(
    "pcc_gui_app_lifecycle_native_event",
    "i32",
    ("i32", "i64", "ptr", "i64", "i32", "i32"),
)
def pcc_gui_app_lifecycle_native_event(
    kind: int,
    window_id: int,
    payload,
    payload_length: int,
    flags: int,
    exit_code: int,
) -> int:
    """Synchronous sink used by the Objective-C delegate adapter."""
    if _base("pcc_gui_app_lifecycle_head") != _base(
        "pcc_gui_app_lifecycle_tail"
    ):
        return ERR_INVALID_TRANSITION
    status = pcc_gui_app_lifecycle_post(
        kind, window_id, payload, payload_length, flags, exit_code
    )
    if status != OK:
        return status
    head = _base("pcc_gui_app_lifecycle_head")
    record = _event_at(head)
    _set("pcc_gui_app_lifecycle_head", head + 1)
    return _process_event(record, null())


@c_abi_typed_export("pcc_gui_app_lifecycle_post_startup", "i32", ())
def pcc_gui_app_lifecycle_post_startup() -> int:
    head = _base("pcc_gui_app_lifecycle_head")
    tail = _base("pcc_gui_app_lifecycle_tail")
    if _base("pcc_gui_app_lifecycle_capacity") - (tail - head) < 2:
        return ERR_CAPACITY
    status = pcc_gui_app_lifecycle_post(EVENT_READY, 0, null(), 0, 0, 0)
    if status != OK:
        return status
    return pcc_gui_app_lifecycle_post(EVENT_RESUMED, 0, null(), 0, 0, 0)


@c_abi_typed_export("pcc_gui_app_lifecycle_state", "i32", ())
def pcc_gui_app_lifecycle_state() -> int:
    return _base("pcc_gui_app_lifecycle_state_value")


@c_abi_typed_export("pcc_gui_app_lifecycle_pending", "i64", ())
def pcc_gui_app_lifecycle_pending() -> int:
    return _base("pcc_gui_app_lifecycle_tail") - _base(
        "pcc_gui_app_lifecycle_head"
    )


@c_abi_typed_export("pcc_gui_app_lifecycle_terminal_count", "i32", ())
def pcc_gui_app_lifecycle_terminal_count() -> int:
    return _base("pcc_gui_app_lifecycle_terminal_count_value")
