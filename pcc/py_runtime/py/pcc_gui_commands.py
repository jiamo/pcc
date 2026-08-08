"""Typed managed state and the canonical in-process GUI command boundary.

This module version-supersedes the storage formerly owned by
``pcc_gui_binding``.  The old exports are adapters into these tables; there is
only one property/binding/command owner.

``PccGuiInvokeV1`` and ``PccGuiCompletionV1`` use the frozen declarative ABI.
Invoke payloads are copied into resolver-owned fixed arenas before a callback
runs.  Synchronous and queued asynchronous callbacks complete through the
same resolver state machine, whose only terminal transitions are result,
structured error, or cancellation.

Managed state v1 admits signed scalar values and explicitly retained opaque
handles.  Semantic Python objects are deliberately excluded until they join
the GC0..4 trace/update contract.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import (
    call_i32_ptr_i64,
    call_i64_i64,
    calloc,
    cstr,
    define_global_i64,
    free,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    load_ptr,
    memcpy,
    null,
    ptr_add,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


ABI_VERSION = 1
INVOKE_SIZE = 64
COMPLETION_SIZE = 48
ERROR_SIZE = 24
STATE_SIZE = 48
BINDING_SIZE = 48
COMMAND_SIZE = 56
RESOLVER_SIZE = 80
MAX_PAYLOAD = 256

STATE_FREE = 0
STATE_I64 = 1
STATE_OPAQUE_HANDLE = 2

COMMAND_FREE = 0
COMMAND_TYPED = 1
COMMAND_LEGACY_I64 = 2
PAYLOAD_NONE = 0
PAYLOAD_BYTES = 1
PAYLOAD_I64 = 2
POLICY_ANY = 0
POLICY_TARGET = 1

INVOKE_SYNC = 0
INVOKE_ASYNC = 1

RESOLVER_FREE = 0
RESOLVER_PENDING = 1
RESOLVER_RESULT = 2
RESOLVER_ERROR = 3
RESOLVER_CANCELLED = 4

COMPLETION_RESULT = 1
COMPLETION_ERROR = 2
COMPLETION_CANCELLED = 3

OK = 0
QUEUED = 1
ERR_ABI_VERSION = -100
ERR_CAPACITY = -101
ERR_DUPLICATE_KEY = -102
ERR_INVALID_TRANSITION = -103
ERR_OWNERSHIP = -105
ERR_DUPLICATE_COMPLETION = -107
ERR_LATE_COMPLETION = -108
ERR_CANCELLED = -109
ERR_UNKNOWN_COMMAND = -110
ERR_POLICY_DENIED = -111
ERR_INVALID_PAYLOAD = -112
ERR_TEARDOWN = -115
ERR_CALLBACK_FAILED = -116

PHASE_MANAGED_STATE = 6
PHASE_COMMAND_LOOKUP = 7
PHASE_COMMAND_INVOKE = 8
PHASE_COMMAND_COMPLETE = 9


define_global_i64("pcc_gui_managed_state_records", 0)
define_global_i64("pcc_gui_managed_state_capacity", 0)
define_global_i64("pcc_gui_managed_binding_records", 0)
define_global_i64("pcc_gui_managed_binding_capacity", 0)
define_global_i64("pcc_gui_command_records", 0)
define_global_i64("pcc_gui_command_capacity", 0)
define_global_i64("pcc_gui_command_resolvers", 0)
define_global_i64("pcc_gui_command_resolver_capacity", 0)
define_global_i64("pcc_gui_command_request_payloads", 0)
define_global_i64("pcc_gui_command_completion_payloads", 0)
define_global_i64("pcc_gui_command_handle_retain", 0)
define_global_i64("pcc_gui_command_handle_release", 0)
define_global_i64("pcc_gui_command_state_generation", 1)


def _base(name: str) -> int:
    if name == "pcc_gui_managed_state_records":
        return load_i64(global_addr("pcc_gui_managed_state_records"), 0)
    if name == "pcc_gui_managed_state_capacity":
        return load_i64(global_addr("pcc_gui_managed_state_capacity"), 0)
    if name == "pcc_gui_managed_binding_records":
        return load_i64(global_addr("pcc_gui_managed_binding_records"), 0)
    if name == "pcc_gui_managed_binding_capacity":
        return load_i64(global_addr("pcc_gui_managed_binding_capacity"), 0)
    if name == "pcc_gui_command_records":
        return load_i64(global_addr("pcc_gui_command_records"), 0)
    if name == "pcc_gui_command_capacity":
        return load_i64(global_addr("pcc_gui_command_capacity"), 0)
    if name == "pcc_gui_command_resolvers":
        return load_i64(global_addr("pcc_gui_command_resolvers"), 0)
    if name == "pcc_gui_command_resolver_capacity":
        return load_i64(global_addr("pcc_gui_command_resolver_capacity"), 0)
    if name == "pcc_gui_command_request_payloads":
        return load_i64(global_addr("pcc_gui_command_request_payloads"), 0)
    if name == "pcc_gui_command_completion_payloads":
        return load_i64(global_addr("pcc_gui_command_completion_payloads"), 0)
    if name == "pcc_gui_command_handle_retain":
        return load_i64(global_addr("pcc_gui_command_handle_retain"), 0)
    if name == "pcc_gui_command_handle_release":
        return load_i64(global_addr("pcc_gui_command_handle_release"), 0)
    if name == "pcc_gui_command_state_generation":
        return load_i64(global_addr("pcc_gui_command_state_generation"), 0)
    return 0


def _state_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_managed_state_records") + index * STATE_SIZE
    )


def _binding_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_managed_binding_records") + index * BINDING_SIZE
    )


def _command_at(index: int):
    return int_to_ptr(_base("pcc_gui_command_records") + index * COMMAND_SIZE)


def _resolver_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_command_resolvers") + index * RESOLVER_SIZE
    )


def _request_payload_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_command_request_payloads") + index * MAX_PAYLOAD
    )


def _completion_payload_at(index: int):
    return int_to_ptr(
        _base("pcc_gui_command_completion_payloads") + index * MAX_PAYLOAD
    )


def _message(code: int):
    if code == ERR_CAPACITY:
        return cstr("gui command capacity exceeded")
    if code == ERR_DUPLICATE_KEY:
        return cstr("duplicate gui request or command")
    if code == ERR_INVALID_TRANSITION:
        return cstr("invalid gui command transition")
    if code == ERR_OWNERSHIP:
        return cstr("invalid gui managed-state ownership")
    if code == ERR_DUPLICATE_COMPLETION:
        return cstr("gui request already completed")
    if code == ERR_LATE_COMPLETION:
        return cstr("late gui request completion")
    if code == ERR_CANCELLED:
        return cstr("gui request cancelled by target teardown")
    if code == ERR_UNKNOWN_COMMAND:
        return cstr("unknown gui command")
    if code == ERR_POLICY_DENIED:
        return cstr("gui command target policy denied")
    if code == ERR_INVALID_PAYLOAD:
        return cstr("invalid gui command payload")
    if code == ERR_TEARDOWN:
        return cstr("gui command target was torn down")
    if code == ERR_CALLBACK_FAILED:
        return cstr("gui command callback failed")
    return cstr("invalid gui command request")


def _write_error(error_out, code: int, phase: int, subject: int) -> int:
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, code)
        store_i32(error_out, 4, phase)
        store_i64(error_out, 8, subject)
        store_ptr(error_out, 16, _message(code))
    return code


def _clear_error(error_out) -> None:
    if not ptr_is_null(error_out):
        store_i32(error_out, 0, 0)
        store_i32(error_out, 4, 0)
        store_i64(error_out, 8, 0)
        store_ptr(error_out, 16, null())


def _state_index(target_id: int, key: int) -> int:
    cap = _base("pcc_gui_managed_state_capacity")
    i = 0
    while i < cap:
        record = _state_at(i)
        if (
            load_i32(record, 0) != STATE_FREE
            and load_i64(record, 8) == target_id
            and load_i64(record, 16) == key
        ):
            return i
        i = i + 1
    return -1


def _free_state_index() -> int:
    cap = _base("pcc_gui_managed_state_capacity")
    i = 0
    while i < cap:
        if load_i32(_state_at(i), 0) == STATE_FREE:
            return i
        i = i + 1
    return -1


def _release_state_value(record) -> None:
    if load_i32(record, 0) == STATE_OPAQUE_HANDLE:
        value = load_i64(record, 24)
        callback = _base("pcc_gui_command_handle_release")
        if value != 0 and callback != 0:
            call_i64_i64(int_to_ptr(callback), value)


def _clear_state(index: int) -> None:
    record = _state_at(index)
    _release_state_value(record)
    store_i32(record, 0, STATE_FREE)
    store_i32(record, 4, 0)
    store_i64(record, 8, 0)
    store_i64(record, 16, 0)
    store_i64(record, 24, 0)
    store_i64(record, 32, 0)
    store_i64(record, 40, 0)


def _binding_index(
    source_target: int, source_key: int, target_id: int, target_key: int
) -> int:
    cap = _base("pcc_gui_managed_binding_capacity")
    i = 0
    while i < cap:
        record = _binding_at(i)
        if (
            load_i32(record, 0) != 0
            and load_i64(record, 8) == source_target
            and load_i64(record, 16) == source_key
            and load_i64(record, 24) == target_id
            and load_i64(record, 32) == target_key
        ):
            return i
        i = i + 1
    return -1


def _free_binding_index() -> int:
    cap = _base("pcc_gui_managed_binding_capacity")
    i = 0
    while i < cap:
        if load_i32(_binding_at(i), 0) == 0:
            return i
        i = i + 1
    return -1


def _clear_binding(index: int) -> None:
    record = _binding_at(index)
    store_i32(record, 0, 0)
    store_i32(record, 4, 0)
    store_i64(record, 8, 0)
    store_i64(record, 16, 0)
    store_i64(record, 24, 0)
    store_i64(record, 32, 0)
    store_i32(record, 40, 0)
    store_i32(record, 44, 0)


def _command_duplicate(
    command_id: int, policy_kind: int, policy_target: int
) -> int:
    cap = _base("pcc_gui_command_capacity")
    i = 0
    while i < cap:
        record = _command_at(i)
        if (
            load_i32(record, 0) != COMMAND_FREE
            and load_i32(record, 8) == command_id
            and load_i32(record, 12) == policy_kind
            and load_i64(record, 24) == policy_target
        ):
            return i
        i = i + 1
    return -1


def _free_command_index() -> int:
    cap = _base("pcc_gui_command_capacity")
    i = 0
    while i < cap:
        if load_i32(_command_at(i), 0) == COMMAND_FREE:
            return i
        i = i + 1
    return -1


def _clear_command(index: int) -> None:
    record = _command_at(index)
    store_i32(record, 0, COMMAND_FREE)
    store_i32(record, 4, 0)
    store_i32(record, 8, 0)
    store_i32(record, 12, 0)
    store_i64(record, 16, 0)
    store_i64(record, 24, 0)
    store_i64(record, 32, 0)
    store_i32(record, 40, 0)
    store_i32(record, 44, 0)
    store_i64(record, 48, 0)


def _resolver_index(resolver_id: int) -> int:
    cap = _base("pcc_gui_command_resolver_capacity")
    i = 0
    while i < cap:
        record = _resolver_at(i)
        if (
            load_i32(record, 0) != RESOLVER_FREE
            and load_i64(record, 32) == resolver_id
        ):
            return i
        i = i + 1
    return -1


def _request_index(request_id: int) -> int:
    cap = _base("pcc_gui_command_resolver_capacity")
    i = 0
    while i < cap:
        record = _resolver_at(i)
        if (
            load_i32(record, 0) != RESOLVER_FREE
            and load_i64(record, 8) == request_id
        ):
            return i
        i = i + 1
    return -1


def _free_resolver_index() -> int:
    cap = _base("pcc_gui_command_resolver_capacity")
    i = 0
    while i < cap:
        if load_i32(_resolver_at(i), 0) == RESOLVER_FREE:
            return i
        i = i + 1
    return -1


def _clear_resolver(index: int) -> None:
    record = _resolver_at(index)
    store_i32(record, 0, RESOLVER_FREE)
    store_i32(record, 4, 0)
    store_i64(record, 8, 0)
    store_i64(record, 16, 0)
    store_i32(record, 24, 0)
    store_i32(record, 28, 0)
    store_i64(record, 32, 0)
    store_i64(record, 40, 0)
    store_i64(record, 48, 0)
    store_i64(record, 56, 0)
    store_i32(record, 64, 0)
    store_i32(record, 68, 0)
    store_ptr(record, 72, null())


def _next_state_generation() -> int:
    generation = _base("pcc_gui_command_state_generation") + 1
    if generation <= 0 or generation >= 0x7FFFFFFFFFFFFFFF:
        return -1
    store_i64(global_addr("pcc_gui_command_state_generation"), 0, generation)
    return generation


@c_abi_typed_export(
    "pcc_gui_commands_init", "i32", ("i64", "i64", "i64", "i64")
)
def pcc_gui_commands_init(
    state_capacity: int,
    binding_capacity: int,
    command_capacity: int,
    resolver_capacity: int,
) -> int:
    if (
        state_capacity <= 0
        or state_capacity > 8192
        or binding_capacity <= 0
        or binding_capacity > 4096
        or command_capacity <= 0
        or command_capacity > 1024
        or resolver_capacity <= 0
        or resolver_capacity > 256
    ):
        return ERR_CAPACITY
    if _base("pcc_gui_managed_state_records") != 0:
        return ERR_INVALID_TRANSITION
    states = calloc(state_capacity, STATE_SIZE)
    bindings = calloc(binding_capacity, BINDING_SIZE)
    commands = calloc(command_capacity, COMMAND_SIZE)
    resolvers = calloc(resolver_capacity, RESOLVER_SIZE)
    request_payloads = calloc(resolver_capacity, MAX_PAYLOAD)
    completion_payloads = calloc(resolver_capacity, MAX_PAYLOAD)
    if (
        ptr_is_null(states)
        or ptr_is_null(bindings)
        or ptr_is_null(commands)
        or ptr_is_null(resolvers)
        or ptr_is_null(request_payloads)
        or ptr_is_null(completion_payloads)
    ):
        if not ptr_is_null(states):
            free(states)
        if not ptr_is_null(bindings):
            free(bindings)
        if not ptr_is_null(commands):
            free(commands)
        if not ptr_is_null(resolvers):
            free(resolvers)
        if not ptr_is_null(request_payloads):
            free(request_payloads)
        if not ptr_is_null(completion_payloads):
            free(completion_payloads)
        return ERR_CAPACITY
    store_i64(
        global_addr("pcc_gui_managed_state_records"), 0, ptr_to_int(states)
    )
    store_i64(
        global_addr("pcc_gui_managed_state_capacity"), 0, state_capacity
    )
    store_i64(
        global_addr("pcc_gui_managed_binding_records"), 0, ptr_to_int(bindings)
    )
    store_i64(
        global_addr("pcc_gui_managed_binding_capacity"), 0, binding_capacity
    )
    store_i64(global_addr("pcc_gui_command_records"), 0, ptr_to_int(commands))
    store_i64(global_addr("pcc_gui_command_capacity"), 0, command_capacity)
    store_i64(
        global_addr("pcc_gui_command_resolvers"), 0, ptr_to_int(resolvers)
    )
    store_i64(
        global_addr("pcc_gui_command_resolver_capacity"), 0, resolver_capacity
    )
    store_i64(
        global_addr("pcc_gui_command_request_payloads"),
        0,
        ptr_to_int(request_payloads),
    )
    store_i64(
        global_addr("pcc_gui_command_completion_payloads"),
        0,
        ptr_to_int(completion_payloads),
    )
    return OK


@c_abi_typed_export("pcc_gui_commands_ensure_legacy", "i32", ())
def pcc_gui_commands_ensure_legacy() -> int:
    if _base("pcc_gui_managed_state_records") != 0:
        return OK
    return pcc_gui_commands_init(4096, 4096, 1024, 64)


@c_abi_typed_export(
    "pcc_gui_managed_state_set_handle_ownership", "i32", ("ptr", "ptr")
)
def pcc_gui_managed_state_set_handle_ownership(retain, release) -> int:
    if ptr_is_null(retain) or ptr_is_null(release):
        return ERR_OWNERSHIP
    if (
        _base("pcc_gui_command_handle_retain") != 0
        or _base("pcc_gui_command_handle_release") != 0
    ):
        return ERR_INVALID_TRANSITION
    store_i64(
        global_addr("pcc_gui_command_handle_retain"), 0, ptr_to_int(retain)
    )
    store_i64(
        global_addr("pcc_gui_command_handle_release"), 0, ptr_to_int(release)
    )
    return OK


def _set_state_value(
    target_id: int,
    key: int,
    kind: int,
    value: int,
    auxiliary: int,
    notify: int,
) -> int:
    if (
        target_id < 0
        or key < 0
        or (kind != STATE_I64 and kind != STATE_OPAQUE_HANDLE)
        or _base("pcc_gui_managed_state_records") == 0
    ):
        return ERR_OWNERSHIP
    index = _state_index(target_id, key)
    if index < 0:
        index = _free_state_index()
    elif load_i32(_state_at(index), 0) != kind:
        # A registered slot has one stable representation.  Reinterpreting an
        # opaque retained handle as an integer (or the inverse) would bypass
        # the ownership callbacks and make bound destinations disagree.
        return ERR_OWNERSHIP
    if index < 0:
        return ERR_CAPACITY
    retained = value
    if kind == STATE_OPAQUE_HANDLE:
        callback = _base("pcc_gui_command_handle_retain")
        if value == 0 or callback == 0 or _base("pcc_gui_command_handle_release") == 0:
            return ERR_OWNERSHIP
        retained = call_i64_i64(int_to_ptr(callback), value)
        if retained == 0:
            return ERR_OWNERSHIP
    generation = _next_state_generation()
    if generation < 0:
        if kind == STATE_OPAQUE_HANDLE:
            call_i64_i64(
                int_to_ptr(_base("pcc_gui_command_handle_release")), retained
            )
        return ERR_CAPACITY
    record = _state_at(index)
    _release_state_value(record)
    store_i32(record, 0, kind)
    store_i32(record, 4, 0)
    store_i64(record, 8, target_id)
    store_i64(record, 16, key)
    store_i64(record, 24, retained)
    store_i64(record, 32, auxiliary)
    store_i64(record, 40, generation)
    if notify != 0:
        cap = _base("pcc_gui_managed_binding_capacity")
        i = 0
        while i < cap:
            binding = _binding_at(i)
            if (
                load_i32(binding, 0) != 0
                and load_i64(binding, 8) == target_id
                and load_i64(binding, 16) == key
            ):
                status = _set_state_value(
                    load_i64(binding, 24),
                    load_i64(binding, 32),
                    kind,
                    retained,
                    auxiliary,
                    0,
                )
                if status != OK:
                    return status
            i = i + 1
    return OK


@c_abi_typed_export(
    "pcc_gui_managed_state_set",
    "i32",
    ("i64", "i64", "i32", "i64", "i64"),
)
def pcc_gui_managed_state_set(
    target_id: int, key: int, kind: int, value: int, auxiliary: int
) -> int:
    return _set_state_value(target_id, key, kind, value, auxiliary, 1)


@c_abi_typed_export(
    "pcc_gui_managed_state_get", "i32", ("i64", "i64", "ptr")
)
def pcc_gui_managed_state_get(target_id: int, key: int, state_out) -> int:
    if ptr_is_null(state_out):
        return ERR_OWNERSHIP
    index = _state_index(target_id, key)
    if index < 0:
        return ERR_OWNERSHIP
    record = _state_at(index)
    store_i32(state_out, 0, load_i32(record, 0))
    store_i32(state_out, 4, 0)
    store_i64(state_out, 8, target_id)
    store_i64(state_out, 16, key)
    store_i64(state_out, 24, load_i64(record, 24))
    store_i64(state_out, 32, load_i64(record, 32))
    store_i64(state_out, 40, load_i64(record, 40))
    return OK


@c_abi_typed_export(
    "pcc_gui_managed_binding_add",
    "i32",
    ("i64", "i64", "i64", "i64"),
)
def pcc_gui_managed_binding_add(
    source_target: int, source_key: int, target_id: int, target_key: int
) -> int:
    if (
        source_target < 0
        or target_id < 0
        or source_key < 0
        or target_key < 0
    ):
        return ERR_OWNERSHIP
    if _binding_index(source_target, source_key, target_id, target_key) >= 0:
        return ERR_DUPLICATE_KEY
    source_index = _state_index(source_target, source_key)
    if source_index < 0:
        return ERR_OWNERSHIP
    free_index = _free_binding_index()
    if free_index < 0:
        return ERR_CAPACITY
    source = _state_at(source_index)
    target_index = _state_index(target_id, target_key)
    if target_index < 0 and _free_state_index() < 0:
        return ERR_CAPACITY
    if target_index >= 0 and load_i32(_state_at(target_index), 0) != load_i32(source, 0):
        return ERR_OWNERSHIP
    status = _set_state_value(
        target_id,
        target_key,
        load_i32(source, 0),
        load_i64(source, 24),
        load_i64(source, 32),
        0,
    )
    if status != OK:
        return status
    binding = _binding_at(free_index)
    store_i64(binding, 8, source_target)
    store_i64(binding, 16, source_key)
    store_i64(binding, 24, target_id)
    store_i64(binding, 32, target_key)
    store_i32(binding, 40, load_i32(source, 0))
    store_i32(binding, 0, 1)
    return OK


@c_abi_typed_export(
    "pcc_gui_managed_binding_update", "i32", ("i64", "i64")
)
def pcc_gui_managed_binding_update(source_target: int, source_key: int) -> int:
    source_index = _state_index(source_target, source_key)
    if source_index < 0:
        return ERR_OWNERSHIP
    source = _state_at(source_index)
    cap = _base("pcc_gui_managed_binding_capacity")
    i = 0
    while i < cap:
        binding = _binding_at(i)
        if (
            load_i32(binding, 0) != 0
            and load_i64(binding, 8) == source_target
            and load_i64(binding, 16) == source_key
        ):
            status = _set_state_value(
                load_i64(binding, 24),
                load_i64(binding, 32),
                load_i32(source, 0),
                load_i64(source, 24),
                load_i64(source, 32),
                0,
            )
            if status != OK:
                return status
        i = i + 1
    return OK


def _register_command(
    command_id: int,
    callback,
    payload_kind: int,
    policy_kind: int,
    policy_target: int,
    policy_context: int,
    callback_kind: int,
) -> int:
    if (
        command_id <= 0
        or ptr_is_null(callback)
        or (payload_kind < PAYLOAD_NONE or payload_kind > PAYLOAD_I64)
        or (policy_kind != POLICY_ANY and policy_kind != POLICY_TARGET)
        or (policy_kind == POLICY_TARGET and policy_target < 0)
        or (callback_kind != COMMAND_TYPED and callback_kind != COMMAND_LEGACY_I64)
        or _base("pcc_gui_command_records") == 0
    ):
        return ERR_OWNERSHIP
    if _command_duplicate(command_id, policy_kind, policy_target) >= 0:
        return ERR_DUPLICATE_KEY
    index = _free_command_index()
    if index < 0:
        return ERR_CAPACITY
    record = _command_at(index)
    store_i32(record, 4, payload_kind)
    store_i32(record, 8, command_id)
    store_i32(record, 12, policy_kind)
    store_i64(record, 16, ptr_to_int(callback))
    store_i64(record, 24, policy_target)
    store_i64(record, 32, policy_context)
    store_i32(record, 40, callback_kind)
    store_i32(record, 0, callback_kind)
    return OK


@c_abi_typed_export(
    "pcc_gui_commands_register",
    "i32",
    ("i32", "ptr", "i32", "i32", "i64", "i64"),
)
def pcc_gui_commands_register(
    command_id: int,
    callback,
    payload_kind: int,
    policy_kind: int,
    policy_target: int,
    policy_context: int,
) -> int:
    return _register_command(
        command_id,
        callback,
        payload_kind,
        policy_kind,
        policy_target,
        policy_context,
        COMMAND_TYPED,
    )


def _command_for_invoke(command_id: int, target_id: int, context: int) -> int:
    cap = _base("pcc_gui_command_capacity")
    any_match = -1
    i = 0
    while i < cap:
        record = _command_at(i)
        if (
            load_i32(record, 0) == COMMAND_TYPED
            and load_i32(record, 8) == command_id
        ):
            policy = load_i32(record, 12)
            required_context = load_i64(record, 32)
            context_ok = 1 if required_context == 0 or required_context == context else 0
            if (
                policy == POLICY_TARGET
                and load_i64(record, 24) == target_id
                and context_ok != 0
            ):
                return i
            if policy == POLICY_ANY and context_ok != 0:
                any_match = i
        i = i + 1
    return any_match


def _command_exists(command_id: int) -> int:
    cap = _base("pcc_gui_command_capacity")
    i = 0
    while i < cap:
        record = _command_at(i)
        if (
            load_i32(record, 0) == COMMAND_TYPED
            and load_i32(record, 8) == command_id
        ):
            return 1
        i = i + 1
    return 0


def _payload_valid(kind: int, payload, length: int) -> int:
    if kind == PAYLOAD_NONE:
        return 1 if length == 0 and ptr_is_null(payload) else 0
    if kind == PAYLOAD_BYTES:
        if length < 0 or length > MAX_PAYLOAD:
            return 0
        return 1 if length == 0 or not ptr_is_null(payload) else 0
    if kind == PAYLOAD_I64:
        return 1 if length == 8 and not ptr_is_null(payload) else 0
    return 0


def _terminal_error_for_resolver(index: int) -> int:
    state = load_i32(_resolver_at(index), 0)
    if state == RESOLVER_CANCELLED:
        return ERR_LATE_COMPLETION
    if state == RESOLVER_RESULT or state == RESOLVER_ERROR:
        return ERR_DUPLICATE_COMPLETION
    return ERR_LATE_COMPLETION


@c_abi_typed_export(
    "pcc_gui_commands_resolve_result", "i32", ("i64", "ptr", "i64")
)
def pcc_gui_commands_resolve_result(
    resolver_id: int, payload, payload_length: int
) -> int:
    index = _resolver_index(resolver_id)
    if index < 0:
        return ERR_LATE_COMPLETION
    record = _resolver_at(index)
    if load_i32(record, 0) != RESOLVER_PENDING:
        return _terminal_error_for_resolver(index)
    if (
        payload_length < 0
        or payload_length > MAX_PAYLOAD
        or (payload_length > 0 and ptr_is_null(payload))
    ):
        return ERR_INVALID_PAYLOAD
    if payload_length > 0:
        memcpy(_completion_payload_at(index), payload, payload_length)
    store_i32(record, 28, OK)
    store_i64(record, 56, payload_length)
    store_i32(record, 64, COMPLETION_RESULT)
    store_ptr(record, 72, null())
    store_i32(record, 0, RESOLVER_RESULT)
    return OK


@c_abi_typed_export(
    "pcc_gui_commands_resolve_error", "i32", ("i64", "i32", "i64")
)
def pcc_gui_commands_resolve_error(
    resolver_id: int, status: int, subject: int
) -> int:
    index = _resolver_index(resolver_id)
    if index < 0:
        return ERR_LATE_COMPLETION
    record = _resolver_at(index)
    if load_i32(record, 0) != RESOLVER_PENDING:
        return _terminal_error_for_resolver(index)
    if status >= 0:
        return ERR_INVALID_TRANSITION
    store_i32(record, 28, status)
    store_i64(record, 56, 0)
    store_i32(record, 64, COMPLETION_ERROR)
    store_i32(record, 68, 0)
    store_ptr(record, 72, _message(status))
    store_i64(record, 40, subject)
    store_i32(record, 0, RESOLVER_ERROR)
    return OK


def _cancel_resolver(index: int) -> int:
    record = _resolver_at(index)
    if load_i32(record, 0) != RESOLVER_PENDING:
        return 0
    store_i32(record, 28, ERR_CANCELLED)
    store_i64(record, 56, 0)
    store_i32(record, 64, COMPLETION_CANCELLED)
    store_i32(record, 68, 0)
    store_ptr(record, 72, _message(ERR_CANCELLED))
    store_i32(record, 0, RESOLVER_CANCELLED)
    return 1


@c_abi_typed_export("pcc_gui_commands_invoke", "i32", ("ptr",))
def pcc_gui_commands_invoke(invoke) -> int:
    if ptr_is_null(invoke):
        return ERR_OWNERSHIP
    request_id = load_i64(invoke, 0)
    command_id = load_i32(invoke, 8)
    flags = load_i32(invoke, 12)
    target_id = load_i64(invoke, 16)
    payload = load_ptr(invoke, 24)
    payload_length = load_i64(invoke, 32)
    policy_context = load_i64(invoke, 40)
    resolver_id = load_i64(invoke, 48)
    error_out = load_ptr(invoke, 56)
    _clear_error(error_out)
    if (
        request_id <= 0
        or command_id <= 0
        or target_id < 0
        or resolver_id <= 0
        or (flags != INVOKE_SYNC and flags != INVOKE_ASYNC)
        or _base("pcc_gui_command_records") == 0
    ):
        return _write_error(
            error_out, ERR_OWNERSHIP, PHASE_COMMAND_LOOKUP, request_id
        )
    if _request_index(request_id) >= 0 or _resolver_index(resolver_id) >= 0:
        return _write_error(
            error_out, ERR_DUPLICATE_KEY, PHASE_COMMAND_LOOKUP, request_id
        )
    command_index = _command_for_invoke(
        command_id, target_id, policy_context
    )
    if command_index < 0:
        code = (
            ERR_POLICY_DENIED
            if _command_exists(command_id) != 0
            else ERR_UNKNOWN_COMMAND
        )
        return _write_error(error_out, code, PHASE_COMMAND_LOOKUP, command_id)
    command = _command_at(command_index)
    if _payload_valid(load_i32(command, 4), payload, payload_length) == 0:
        return _write_error(
            error_out, ERR_INVALID_PAYLOAD, PHASE_COMMAND_INVOKE, request_id
        )
    index = _free_resolver_index()
    if index < 0:
        return _write_error(
            error_out, ERR_CAPACITY, PHASE_COMMAND_INVOKE, request_id
        )
    stable_payload = _request_payload_at(index)
    if payload_length > 0:
        memcpy(stable_payload, payload, payload_length)
    resolver = _resolver_at(index)
    store_i32(resolver, 4, flags)
    store_i64(resolver, 8, request_id)
    store_i64(resolver, 16, target_id)
    store_i32(resolver, 24, command_id)
    store_i32(resolver, 28, 0)
    store_i64(resolver, 32, resolver_id)
    store_i64(resolver, 40, policy_context)
    store_i64(resolver, 48, payload_length)
    store_i64(resolver, 56, 0)
    store_i32(resolver, 64, 0)
    store_ptr(resolver, 72, null())
    store_i32(resolver, 0, RESOLVER_PENDING)
    # stack_alloc is a compile-time intrinsic; keep the frozen ABI size literal.
    stable_invoke = stack_alloc(64)
    store_i64(stable_invoke, 0, request_id)
    store_i32(stable_invoke, 8, command_id)
    store_i32(stable_invoke, 12, flags)
    store_i64(stable_invoke, 16, target_id)
    store_ptr(
        stable_invoke,
        24,
        stable_payload if payload_length > 0 else null(),
    )
    store_i64(stable_invoke, 32, payload_length)
    store_i64(stable_invoke, 40, policy_context)
    store_i64(stable_invoke, 48, resolver_id)
    store_ptr(stable_invoke, 56, error_out)
    callback = int_to_ptr(load_i64(command, 16))
    callback_status = call_i32_ptr_i64(callback, stable_invoke, resolver_id)
    state = load_i32(resolver, 0)
    if callback_status < 0:
        if state == RESOLVER_PENDING:
            pcc_gui_commands_resolve_error(
                resolver_id, ERR_CALLBACK_FAILED, command_id
            )
        return _write_error(
            error_out,
            ERR_CALLBACK_FAILED,
            PHASE_COMMAND_INVOKE,
            command_id,
        )
    if callback_status == OK:
        if state == RESOLVER_PENDING:
            pcc_gui_commands_resolve_error(
                resolver_id, ERR_CALLBACK_FAILED, command_id
            )
            return _write_error(
                error_out,
                ERR_CALLBACK_FAILED,
                PHASE_COMMAND_COMPLETE,
                request_id,
            )
        return OK
    if callback_status == QUEUED:
        if flags != INVOKE_ASYNC or state != RESOLVER_PENDING:
            if state == RESOLVER_PENDING:
                pcc_gui_commands_resolve_error(
                    resolver_id, ERR_INVALID_TRANSITION, request_id
                )
            return _write_error(
                error_out,
                ERR_INVALID_TRANSITION,
                PHASE_COMMAND_COMPLETE,
                request_id,
            )
        return QUEUED
    if state == RESOLVER_PENDING:
        pcc_gui_commands_resolve_error(
            resolver_id, ERR_INVALID_TRANSITION, request_id
        )
    return _write_error(
        error_out,
        ERR_INVALID_TRANSITION,
        PHASE_COMMAND_COMPLETE,
        request_id,
    )


@c_abi_typed_export(
    "pcc_gui_commands_request_payload", "i32", ("i64", "ptr")
)
def pcc_gui_commands_request_payload(resolver_id: int, payload_out) -> int:
    if ptr_is_null(payload_out):
        return ERR_OWNERSHIP
    index = _resolver_index(resolver_id)
    if index < 0:
        return ERR_LATE_COMPLETION
    record = _resolver_at(index)
    store_ptr(payload_out, 0, _request_payload_at(index))
    store_i64(payload_out, 8, load_i64(record, 48))
    return OK


@c_abi_typed_export(
    "pcc_gui_commands_completion", "i32", ("i64", "ptr")
)
def pcc_gui_commands_completion(resolver_id: int, completion_out) -> int:
    if ptr_is_null(completion_out):
        return ERR_OWNERSHIP
    index = _resolver_index(resolver_id)
    if index < 0:
        return ERR_LATE_COMPLETION
    record = _resolver_at(index)
    state = load_i32(record, 0)
    if state == RESOLVER_PENDING:
        return ERR_INVALID_TRANSITION
    store_i64(completion_out, 0, load_i64(record, 8))
    store_i32(completion_out, 8, load_i32(record, 64))
    store_i32(completion_out, 12, load_i32(record, 28))
    if state == RESOLVER_RESULT and load_i64(record, 56) > 0:
        store_ptr(completion_out, 16, _completion_payload_at(index))
    else:
        store_ptr(completion_out, 16, null())
    store_i64(completion_out, 24, load_i64(record, 56))
    store_ptr(completion_out, 32, load_ptr(record, 72))
    store_i32(completion_out, 40, load_i32(record, 4))
    store_i32(completion_out, 44, 0)
    return OK


@c_abi_typed_export(
    "pcc_gui_commands_release_completion", "i32", ("i64",)
)
def pcc_gui_commands_release_completion(resolver_id: int) -> int:
    index = _resolver_index(resolver_id)
    if index < 0:
        return ERR_LATE_COMPLETION
    if load_i32(_resolver_at(index), 0) == RESOLVER_PENDING:
        return ERR_INVALID_TRANSITION
    _clear_resolver(index)
    return OK


@c_abi_typed_export("pcc_gui_commands_pending_count", "i64", ("i64",))
def pcc_gui_commands_pending_count(target_id: int) -> int:
    cap = _base("pcc_gui_command_resolver_capacity")
    total = 0
    i = 0
    while i < cap:
        record = _resolver_at(i)
        if (
            load_i32(record, 0) == RESOLVER_PENDING
            and (target_id < 0 or load_i64(record, 16) == target_id)
        ):
            total = total + 1
        i = i + 1
    return total


@c_abi_typed_export("pcc_gui_commands_target_teardown", "i32", ("i64",))
def pcc_gui_commands_target_teardown(target_id: int) -> int:
    if target_id < 0:
        return ERR_TEARDOWN
    # Component retirement is authoritative and can happen before the command
    # service is initialized.  That no-owner state is an idempotent teardown,
    # not a failure of otherwise independent component cleanup.
    if _base("pcc_gui_managed_state_records") == 0:
        return 0
    cap = _base("pcc_gui_managed_state_capacity")
    i = 0
    while i < cap:
        record = _state_at(i)
        if load_i32(record, 0) != STATE_FREE and load_i64(record, 8) == target_id:
            _clear_state(i)
        i = i + 1
    cap = _base("pcc_gui_managed_binding_capacity")
    i = 0
    while i < cap:
        record = _binding_at(i)
        if (
            load_i32(record, 0) != 0
            and (
                load_i64(record, 8) == target_id
                or load_i64(record, 24) == target_id
            )
        ):
            _clear_binding(i)
        i = i + 1
    cap = _base("pcc_gui_command_capacity")
    i = 0
    while i < cap:
        record = _command_at(i)
        if (
            load_i32(record, 0) != COMMAND_FREE
            and load_i32(record, 12) == POLICY_TARGET
            and load_i64(record, 24) == target_id
        ):
            _clear_command(i)
        i = i + 1
    cancelled = 0
    cap = _base("pcc_gui_command_resolver_capacity")
    i = 0
    while i < cap:
        record = _resolver_at(i)
        if (
            load_i32(record, 0) == RESOLVER_PENDING
            and load_i64(record, 16) == target_id
        ):
            cancelled = cancelled + _cancel_resolver(i)
        i = i + 1
    return cancelled


@c_abi_typed_export("pcc_gui_commands_cancel_all", "i64", ())
def pcc_gui_commands_cancel_all() -> int:
    cap = _base("pcc_gui_command_resolver_capacity")
    total = 0
    i = 0
    while i < cap:
        total = total + _cancel_resolver(i)
        i = i + 1
    return total


@c_abi_typed_export("pcc_gui_commands_shutdown", "i64", ())
def pcc_gui_commands_shutdown() -> int:
    """Cancel requests and release every command-owned managed-state handle.

    Resolver terminal records remain readable until their consumers release
    them; they contain only bounded copied bytes and static diagnostics.  No
    target registry, binding, command callback or retained handle survives.
    """
    if _base("pcc_gui_managed_state_records") == 0:
        return 0
    cancelled = pcc_gui_commands_cancel_all()
    cap = _base("pcc_gui_managed_state_capacity")
    i = 0
    while i < cap:
        if load_i32(_state_at(i), 0) != STATE_FREE:
            _clear_state(i)
        i = i + 1
    cap = _base("pcc_gui_managed_binding_capacity")
    i = 0
    while i < cap:
        if load_i32(_binding_at(i), 0) != 0:
            _clear_binding(i)
        i = i + 1
    cap = _base("pcc_gui_command_capacity")
    i = 0
    while i < cap:
        if load_i32(_command_at(i), 0) != COMMAND_FREE:
            _clear_command(i)
        i = i + 1
    return cancelled


@c_abi_typed_export(
    "pcc_gui_commands_register_legacy", "i32", ("i64", "i32", "ptr")
)
def pcc_gui_commands_register_legacy(
    target_id: int, command_id: int, callback
) -> int:
    return _register_command(
        command_id,
        callback,
        PAYLOAD_I64,
        POLICY_TARGET,
        target_id,
        0,
        COMMAND_LEGACY_I64,
    )


def _legacy_command_index(target_id: int, command_id: int) -> int:
    cap = _base("pcc_gui_command_capacity")
    i = 0
    while i < cap:
        record = _command_at(i)
        if (
            load_i32(record, 0) == COMMAND_LEGACY_I64
            and load_i32(record, 8) == command_id
            and load_i64(record, 24) == target_id
        ):
            return i
        i = i + 1
    return -1


@c_abi_typed_export(
    "pcc_gui_commands_has_legacy", "i32", ("i64", "i32")
)
def pcc_gui_commands_has_legacy(target_id: int, command_id: int) -> int:
    return 1 if _legacy_command_index(target_id, command_id) >= 0 else 0


@c_abi_typed_export(
    "pcc_gui_commands_invoke_legacy", "i32", ("i64", "i32", "i64")
)
def pcc_gui_commands_invoke_legacy(
    target_id: int, command_id: int, argument: int
) -> int:
    index = _legacy_command_index(target_id, command_id)
    if index < 0:
        return 0
    return call_i64_i64(int_to_ptr(load_i64(_command_at(index), 16)), argument)
