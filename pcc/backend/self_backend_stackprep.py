from __future__ import annotations

"""Target-neutral stack-slot preparation for parsed self-backend functions."""

from typing import Callable

from .self_backend_analysis import (
    collect_block_local_last_uses,
    collect_used_values,
    instruction_used_values,
)
from .self_backend_ir import (
    AllocaInfo,
    I1,
    ParsedFunction,
    SlotInfo,
    TypeDesc,
    _dot_numeric_text_key_id,
    _align_to,
)
from .self_backend_parse import gep_result_type


def _stable_value_name_key(text: str) -> int:
    """Return a deterministic integer bucket key for an SSA value name."""
    modulus = 1099511627776
    value = 0
    index = 0
    while index < len(text):
        value = (value * 131 + ord(text[index])) % modulus
        index += 1
    return value


def assign_stack_slots(
    func: ParsedFunction,
    *,
    aggregate_returned_indirect: Callable[[TypeDesc], bool],
) -> None:
    offset = 0
    func.used_values = collect_used_values(func)
    block_local_last_uses = collect_block_local_last_uses(func)
    free_slots: list[SlotInfo] = []
    active_local_values: set[str] = set()
    used_dot_numeric_ids: set[int] = set()
    used_value_indices_by_key: dict[int, list[int]] = {}
    for used_index, used_value in enumerate(func.used_values):
        used_id = _dot_numeric_text_key_id(used_value)
        if used_id >= 0:
            used_dot_numeric_ids.add(used_id)
            continue
        key = _stable_value_name_key(used_value)
        used_value_indices_by_key.setdefault(key, []).append(used_index)

    def value_is_used(value_name: str) -> bool:
        value_id = _dot_numeric_text_key_id(value_name)
        if value_id >= 0:
            return value_id in used_dot_numeric_ids
        key = _stable_value_name_key(value_name)
        for used_index in used_value_indices_by_key.get(key, []):
            if func.used_values[used_index] == value_name:
                return True
        return False

    def alloc(size: int, align: int) -> int:
        nonlocal offset
        offset = _align_to(offset, align)
        offset += size
        return offset

    def alloc_value_slot(value_name: str, value_type: TypeDesc) -> SlotInfo:
        last_uses = block_local_last_uses.get(current_block, {})
        if value_name not in last_uses:
            return SlotInfo(
                alloc(value_type.value_slot_size, value_type.value_align), value_type
            )
        for index, slot in enumerate(free_slots):
            if (
                slot.type.value_slot_size >= value_type.value_slot_size
                and slot.type.value_align >= value_type.value_align
                and slot.type.describe() == value_type.describe()
            ):
                free_slots.pop(index)
                active_local_values.add(value_name)
                return SlotInfo(slot.offset, value_type)
        active_local_values.add(value_name)
        return SlotInfo(
            alloc(value_type.value_slot_size, value_type.value_align), value_type
        )

    def maybe_free_local_value(value_name: str, position: int) -> None:
        last_uses = block_local_last_uses.get(current_block, {})
        if value_name not in active_local_values:
            return
        if last_uses.get(value_name) != position:
            return
        free_slots.append(func.value_slots[value_name])
        active_local_values.remove(value_name)

    for arg in func.args:
        if arg.type.is_void or not value_is_used(arg.name):
            continue
        func.value_slots[arg.name] = SlotInfo(
            alloc(arg.type.value_slot_size, arg.type.value_align),
            arg.type,
        )

    if aggregate_returned_indirect(func.ret_type):
        func.hidden_sret_slot = SlotInfo(
            alloc(8, 8), TypeDesc("ptr", pointee=TypeDesc("void"))
        )

    for block in func.blocks:
        current_block = block.name
        for phi in block.phis:
            func.value_types[phi.dest] = phi.type
            if value_is_used(phi.dest):
                func.value_slots.setdefault(
                    phi.dest, alloc_value_slot(phi.dest, phi.type)
                )
        for index, instr in enumerate(block.instructions):
            kind = instr.kind
            data = instr.data
            if kind == "alloca":
                name, allocated_type = data
                func.value_types[name] = allocated_type.ptr()
                if value_is_used(name):
                    func.alloca_slots.setdefault(
                        name,
                        AllocaInfo(
                            alloc(allocated_type.slot_size, allocated_type.align),
                            allocated_type,
                        ),
                    )
            elif kind == "load":
                dest, value_type, _ptr_type, _ptr = data
                func.value_types[dest] = value_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, value_type)
                    )
            elif kind == "binop":
                _op, dest, value_type, _lhs, _rhs = data
                func.value_types[dest] = value_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, value_type)
                    )
            elif kind == "fbinop":
                _op, dest, value_type, _lhs, _rhs = data
                func.value_types[dest] = value_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, value_type)
                    )
            elif kind == "fneg":
                dest, value_type, _value = data
                func.value_types[dest] = value_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, value_type)
                    )
            elif kind == "icmp":
                _cond, dest, _value_type, _lhs, _rhs = data
                result_type = (
                    TypeDesc("array", count=_value_type.count, elem=I1)
                    if _value_type.is_array and _value_type.elem is not None
                    else I1
                )
                func.value_types[dest] = result_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, result_type)
                    )
            elif kind == "fcmp":
                _cond, dest, _value_type, _lhs, _rhs = data
                result_type = (
                    TypeDesc("array", count=_value_type.count, elem=I1)
                    if _value_type.is_array and _value_type.elem is not None
                    else I1
                )
                func.value_types[dest] = result_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, result_type)
                    )
            elif kind == "cast":
                _op, dest, _src_type, _value, dst_type = data
                func.value_types[dest] = dst_type
                if value_is_used(dest):
                    func.value_slots.setdefault(dest, alloc_value_slot(dest, dst_type))
            elif kind == "select":
                dest, value_type, _cond, _true_value, _false_value = data
                func.value_types[dest] = value_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, value_type)
                    )
            elif kind == "freeze":
                dest, value_type, _value = data
                func.value_types[dest] = value_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, value_type)
                    )
            elif kind == "insertelement":
                (
                    dest,
                    vector_type,
                    _vector_value,
                    _elem_type,
                    _elem_value,
                    _index_value,
                ) = data
                func.value_types[dest] = vector_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, vector_type)
                    )
            elif kind == "extractelement":
                dest, _vector_type, _vector_value, _index_value, elem_type = data
                func.value_types[dest] = elem_type
                if value_is_used(dest):
                    func.value_slots.setdefault(dest, alloc_value_slot(dest, elem_type))
            elif kind == "shufflevector":
                dest, vector_type, _lhs, _rhs, _mask_type, _mask_value = data
                func.value_types[dest] = vector_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, vector_type)
                    )
            elif kind == "extractvalue":
                dest, _aggregate_type, _value, _indices, result_type, _offset = data
                func.value_types[dest] = result_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, result_type)
                    )
            elif kind == "insertvalue":
                (
                    dest,
                    aggregate_type,
                    _aggregate_value,
                    _elem_type,
                    _elem_value,
                    _indices,
                    _offset,
                ) = data
                func.value_types[dest] = aggregate_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, aggregate_type)
                    )
            elif kind == "va_arg":
                dest, _ap_type, _ap, value_type = data
                func.value_types[dest] = value_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, value_type)
                    )
            elif kind == "gep":
                dest, base_type, _ptr_type, _ptr, indices = data
                result_type = gep_result_type(base_type, indices)
                func.value_types[dest] = result_type
                if value_is_used(dest):
                    func.value_slots.setdefault(
                        dest, alloc_value_slot(dest, result_type)
                    )
            elif kind == "call":
                (
                    dest,
                    ret_type,
                    _callee,
                    _is_indirect,
                    _args,
                    _fixed_arg_count,
                    _is_vararg_call,
                ) = data
                if dest is not None:
                    func.value_types[dest] = ret_type
                    if value_is_used(dest) or aggregate_returned_indirect(ret_type):
                        func.value_slots.setdefault(
                            dest, alloc_value_slot(dest, ret_type)
                        )
            for value in instruction_used_values(instr):
                maybe_free_local_value(value, index)
        term_pos = len(block.instructions)
        for value in sorted(active_local_values):
            maybe_free_local_value(value, term_pos)

    func.frame_size = _align_to(offset, 16)
