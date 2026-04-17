from __future__ import annotations

"""Target-neutral value/liveness analysis helpers for the self backend."""

from .self_backend_ir import ParsedFunction, ParsedInstr
from .self_backend_parse import (
    const_int_from_value,
    is_aggregate_literal_value,
    is_float_literal,
    is_hex_literal,
)


def is_local_value_ref(value: str) -> bool:
    return not (
        value == "null"
        or value == "poison"
        or value == "zeroinitializer"
        or is_aggregate_literal_value(value)
        or value.startswith("@")
        or const_int_from_value(value) is not None
        or is_hex_literal(value)
        or is_float_literal(value)
    )


def instruction_defined_value(instr: ParsedInstr) -> str | None:
    if instr.kind in {
        "alloca",
        "load",
        "binop",
        "fbinop",
        "fneg",
        "icmp",
        "fcmp",
        "cast",
        "select",
        "freeze",
        "insertelement",
        "extractelement",
        "shufflevector",
        "extractvalue",
        "insertvalue",
        "va_arg",
        "gep",
    }:
        return instr.data[0]
    if instr.kind == "call":
        return instr.data[0]
    return None


def instruction_used_values(instr: ParsedInstr) -> list[str]:
    kind = instr.kind
    data = instr.data
    values: list[str] = []
    if kind == "store":
        _value_type, value, _ptr_type, ptr_name = data
        values = [value, ptr_name]
    elif kind == "load":
        _dest, _value_type, _ptr_type, ptr_name = data
        values = [ptr_name]
    elif kind == "va_arg":
        _dest, _ap_type, ptr_name, _value_type = data
        values = [ptr_name]
    elif kind in {"binop", "fbinop", "icmp", "fcmp"}:
        _op, _dest, _value_type, lhs, rhs = data
        values = [lhs, rhs]
    elif kind == "fneg":
        _dest, _value_type, value = data
        values = [value]
    elif kind == "cast":
        _op, _dest, _src_type, value, _dst_type = data
        values = [value]
    elif kind == "select":
        _dest, _value_type, cond, true_value, false_value = data
        values = [cond, true_value, false_value]
    elif kind == "freeze":
        _dest, _value_type, value = data
        values = [value]
    elif kind == "insertelement":
        _dest, _vector_type, vector_value, _elem_type, elem_value, index_value = data
        values = [vector_value, elem_value, index_value]
    elif kind == "extractelement":
        _dest, _vector_type, vector_value, index_value, _elem_type = data
        values = [vector_value, index_value]
    elif kind == "shufflevector":
        _dest, _vector_type, lhs, rhs, _mask_type, mask_value = data
        values = [lhs, rhs, mask_value]
    elif kind == "extractvalue":
        _dest, _aggregate_type, value, _indices, _result_type, _offset = data
        values = [value]
    elif kind == "insertvalue":
        _dest, _aggregate_type, aggregate_value, _elem_type, elem_value, _indices, _offset = data
        values = [aggregate_value, elem_value]
    elif kind == "gep":
        _dest, _base_type, _ptr_type, ptr_value, indices = data
        values = [ptr_value, *[index_value for _index_type, index_value in indices]]
    elif kind == "call":
        _dest, _ret_type, callee, is_indirect, args, _fixed_arg_count, _is_vararg = data
        if is_indirect:
            values.append(callee)
        values.extend(arg_value for _arg_type, arg_value in args)
    return [value for value in values if is_local_value_ref(value)]


def terminator_used_values(term: ParsedInstr) -> list[str]:
    values: list[str] = []
    if term.kind == "ret":
        _ret_type, value = term.data
        values = [value]
    elif term.kind == "br_cond":
        cond_name, _true_target, _false_target = term.data
        values = [cond_name]
    elif term.kind == "switch":
        _value_type, value, _default_target, _cases = term.data
        values = [value]
    return [value for value in values if is_local_value_ref(value)]


def collect_block_local_last_uses(func: ParsedFunction) -> dict[str, dict[str, int]]:
    def_blocks: dict[str, str] = {}
    block_lengths = {block.name: len(block.instructions) for block in func.blocks}
    for block in func.blocks:
        for phi in block.phis:
            def_blocks[phi.dest] = block.name
        for instr in block.instructions:
            dest = instruction_defined_value(instr)
            if dest is not None:
                def_blocks[dest] = block.name

    use_blocks: dict[str, set[str]] = {}
    last_uses: dict[tuple[str, str], int] = {}
    for block in func.blocks:
        term_pos = len(block.instructions)
        for phi in block.phis:
            for incoming in phi.incoming:
                if not is_local_value_ref(incoming.value):
                    continue
                use_blocks.setdefault(incoming.value, set()).add(incoming.label)
                key = (incoming.label, incoming.value)
                last_uses[key] = max(last_uses.get(key, -1), block_lengths[incoming.label])
        for index, instr in enumerate(block.instructions):
            for value in instruction_used_values(instr):
                use_blocks.setdefault(value, set()).add(block.name)
                key = (block.name, value)
                last_uses[key] = max(last_uses.get(key, -1), index)
        for value in terminator_used_values(block.terminator):
            use_blocks.setdefault(value, set()).add(block.name)
            key = (block.name, value)
            last_uses[key] = max(last_uses.get(key, -1), term_pos)

    block_local_last_uses: dict[str, dict[str, int]] = {}
    for value, block_name in def_blocks.items():
        if use_blocks.get(value) == {block_name}:
            block_local_last_uses.setdefault(block_name, {})[value] = last_uses[(block_name, value)]
    return block_local_last_uses


def collect_used_values(func: ParsedFunction) -> set[str]:
    used: set[str] = set()
    for block in func.blocks:
        for phi in block.phis:
            for incoming in phi.incoming:
                if is_local_value_ref(incoming.value):
                    used.add(incoming.value)
        for instr in block.instructions:
            used.update(instruction_used_values(instr))
        used.update(terminator_used_values(block.terminator))
    return used


def value_has_uses(func: ParsedFunction, value_name: str) -> bool:
    if value_name in collect_used_values(func):
        return True
    for block in func.blocks:
        for phi in block.phis:
            for incoming in phi.incoming:
                if incoming.value == value_name:
                    return True
        for instr in block.instructions:
            if value_name in instruction_used_values(instr):
                return True
        if value_name in terminator_used_values(block.terminator):
            return True
    return False
