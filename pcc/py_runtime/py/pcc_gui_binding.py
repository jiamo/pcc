"""Compatibility adapters for the canonical GUI command/state owner.

The property, binding and legacy command exports predate the declarative GUI
contract.  They remain ABI-compatible, but own no storage: every operation is
routed into :mod:`pcc_gui_commands`.  This avoids two live property/command
tables while older callers migrate to typed managed state and invoke packets.
"""

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import (
    load_i64,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
)


STATE_I64 = 1


_ensure = extern("pcc_gui_commands_ensure_legacy", (), c_int32)
_state_set = extern(
    "pcc_gui_managed_state_set",
    (c_int64, c_int64, c_int32, c_int64, c_int64),
    c_int32,
)
_state_get = extern(
    "pcc_gui_managed_state_get", (c_int64, c_int64, c_ptr), c_int32
)
_binding_add = extern(
    "pcc_gui_managed_binding_add",
    (c_int64, c_int64, c_int64, c_int64),
    c_int32,
)
_binding_update = extern(
    "pcc_gui_managed_binding_update", (c_int64, c_int64), c_int32
)
_register_legacy = extern(
    "pcc_gui_commands_register_legacy", (c_int64, c_int32, c_ptr), c_int32
)
_invoke_legacy = extern(
    "pcc_gui_commands_invoke_legacy",
    (c_int64, c_int32, c_int64),
    c_int32,
)
_has_legacy = extern(
    "pcc_gui_commands_has_legacy", (c_int64, c_int32), c_int32
)


def _target(owner) -> int:
    if ptr_is_null(owner):
        return 0
    return ptr_to_int(owner)


@c_abi_typed_export("pcc_gui_binding_set_property", "i32", ("ptr", "i32", "i64"))
def pcc_gui_binding_set_property(owner, prop_id: int, value: int) -> int:
    target = _target(owner)
    if target == 0 or prop_id < 0 or prop_id >= 256 or _ensure() != 0:
        return -1
    return _state_set(target, prop_id, STATE_I64, value, 0)


@c_abi_typed_export("pcc_gui_binding_get_property", "i64", ("ptr", "i32"))
def pcc_gui_binding_get_property(owner, prop_id: int) -> int:
    target = _target(owner)
    if target == 0 or prop_id < 0 or prop_id >= 256 or _ensure() != 0:
        return 0
    state = stack_alloc(48)
    if _state_get(target, prop_id, state) != 0:
        return 0
    return load_i64(state, 24)


@c_abi_typed_export("pcc_gui_binding_add", "i32", ("ptr", "i32", "ptr", "i32"))
def pcc_gui_binding_add(src_owner, src_prop: int, dst_owner, dst_prop: int) -> int:
    source = _target(src_owner)
    target = _target(dst_owner)
    if (
        source == 0
        or target == 0
        or src_prop < 0
        or src_prop >= 256
        or dst_prop < 0
        or dst_prop >= 256
        or _ensure() != 0
    ):
        return -1
    return _binding_add(source, src_prop, target, dst_prop)


@c_abi_typed_export("pcc_gui_binding_update_target", "void", ("ptr", "i32"))
def pcc_gui_binding_update_target(src_owner, src_prop: int) -> None:
    source = _target(src_owner)
    if source == 0 or src_prop < 0 or src_prop >= 256 or _ensure() != 0:
        return
    _binding_update(source, src_prop)


@c_abi_typed_export("pcc_gui_binding_set_command", "i32", ("ptr", "i32", "ptr"))
def pcc_gui_binding_set_command(owner, cmd_id: int, fn_ptr) -> int:
    target = _target(owner)
    if (
        target == 0
        or cmd_id <= 0
        or cmd_id >= 256
        or ptr_is_null(fn_ptr)
        or _ensure() != 0
    ):
        return -1
    return _register_legacy(target, cmd_id, fn_ptr)


@c_abi_typed_export("pcc_gui_binding_invoke_command", "i32", ("ptr", "i32", "i64"))
def pcc_gui_binding_invoke_command(owner, cmd_id: int, arg: int) -> int:
    target = _target(owner)
    if target == 0 or cmd_id <= 0 or cmd_id >= 256 or _ensure() != 0:
        return -1
    return _invoke_legacy(target, cmd_id, arg)


@c_abi_typed_export("pcc_gui_binding_has_command", "i32", ("ptr", "i32"))
def pcc_gui_binding_has_command(owner, cmd_id: int) -> int:
    target = _target(owner)
    if target == 0 or cmd_id <= 0 or cmd_id >= 256 or _ensure() != 0:
        return 0
    return _has_legacy(target, cmd_id)
