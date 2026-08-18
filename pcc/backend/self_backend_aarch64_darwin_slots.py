from __future__ import annotations

from .arm64_encode import encode_emitted_load_store_parts
from .self_backend_aarch64_fragments import AArch64EmissionFragments
from .self_backend_aarch64_darwin_abi import (
    abi_value_reg_names,
    aggregate_hfa_members,
    aggregate_reg_chunks,
    reg_name,
    reg_name_indexed,
)
from .self_backend_aarch64_darwin_mem import (
    aggregate_copy_chunks,
    chunk_load_op,
    chunk_store_op,
    emitted_memory_instruction_line,
    emitted_move_register_line,
    emitted_movewide_instruction_line,
    mem_load_op,
    mem_store_op,
    stack_load_op,
    stack_store_op,
)
from .self_backend_aarch64_darwin_regs import append_add_offset, emit_add_offset, pick_scratch_gpr
from .self_backend_ir import (
    ParsedFunction,
    SlotInfo,
    TypeDesc,
    parsed_function_value_slot_id,
    parsed_function_value_slot_offset,
    parsed_function_value_slot_type,
)
from .self_backend_kernel import TYPE_KIND_INT
from .self_backend_value_arena import CompilerInt2, CompilerInt4


def _pick_copy_data_reg(
    src_addr_reg: str, dst_addr_reg: str, *, chunk_size: int
) -> str:
    candidates = ("x14", "x15", "x12", "x11", "x10", "x9")
    for reg in candidates:
        if reg != src_addr_reg and reg != dst_addr_reg:
            return reg if chunk_size > 4 else f"w{reg[1:]}"
    raise RuntimeError("no scratch register available for aggregate copy")


def _base_xreg(reg: str) -> str:
    return reg if reg.startswith("x") else f"x{reg[1:]}"


def _pick_temp_xreg(*forbidden: str) -> str:
    forbidden_x = {_base_xreg(reg) for reg in forbidden}
    for reg in ("x17", "x16", "x15", "x14", "x13", "x12", "x11", "x10", "x9"):
        if reg not in forbidden_x:
            return reg
    raise RuntimeError(
        "no scratch register available for aggregate chunk materialization"
    )


def _chunk_reg_alias(base_reg: str, chunk_size: int) -> str:
    return base_reg if chunk_size > 4 else f"w{base_reg[1:]}"


def _prepare_nonconflicting_addr_reg(
    addr_reg: str, regs: tuple[str, ...]
) -> tuple[list[str], str]:
    if all(_base_xreg(reg) != addr_reg for reg in regs):
        return [], addr_reg
    scratch = _pick_temp_xreg(addr_reg, *regs)
    return emit_add_offset(scratch, addr_reg, 0), scratch


def emit_slot_base_address_parts(offset: int, reg: str) -> list[str]:
    return emit_add_offset(reg, "x29", -offset)


def append_slot_base_address_parts(
    owner: AArch64EmissionFragments,
    fragment: CompilerInt2,
    offset: int,
    reg: str,
) -> None:
    append_add_offset(owner, fragment, reg, "x29", -offset)


def emit_slot_base_address(slot: SlotInfo, reg: str) -> list[str]:
    return emit_slot_base_address_parts(slot.offset, reg)


def emit_value_slot_base_address(
    func: ParsedFunction, value_name: str, reg: str
) -> list[str]:
    if func.indexed_slot_projection:
        slot_id = parsed_function_value_slot_id(func, value_name)
        return emit_slot_base_address_parts(
            func.indexed_kernel.slot_offset(slot_id), reg
        )
    return emit_slot_base_address_parts(
        parsed_function_value_slot_offset(func, value_name), reg
    )


def store_reg_to_slot_parts(
    reg: str, offset: int, value_type: TypeDesc
) -> list[str]:
    op = stack_store_op(value_type)
    if offset > 255:
        scratch = pick_scratch_gpr(reg)
        lines = emit_slot_base_address_parts(offset, scratch)
        lines.append(emitted_memory_instruction_line(op, reg, scratch))
        return lines
    return [emitted_memory_instruction_line(op, reg, "x29", -offset)]


def append_store_reg_to_slot_parts(
    owner: AArch64EmissionFragments,
    fragment: CompilerInt2,
    reg: str,
    offset: int,
    is_integer: bool,
    width: int,
) -> None:
    chunk_size = 8
    if is_integer and width <= 8:
        chunk_size = 1
    elif is_integer and width <= 16:
        chunk_size = 2
    op = chunk_store_op(chunk_size, stack=True)
    if chunk_size == 2:
        # This encoder does not yet own halfword native records. Reject at
        # its canonical boundary before publishing a large frame address.
        encode_emitted_load_store_parts(op, reg, "x29", 0)
    if offset > 255:
        scratch = pick_scratch_gpr(reg)
        append_slot_base_address_parts(owner, fragment, offset, scratch)
        owner.append_memory(fragment, op, reg, scratch)
        return
    owner.append_memory(fragment, op, reg, "x29", -offset)


def store_reg_to_slot(reg: str, slot: SlotInfo) -> list[str]:
    return store_reg_to_slot_parts(reg, slot.offset, slot.type)


def store_reg_to_value_slot(
    reg: str, func: ParsedFunction, value_name: str
) -> list[str]:
    if func.indexed_slot_projection:
        slot_id = parsed_function_value_slot_id(func, value_name)
        kernel = func.indexed_kernel
        return store_reg_to_slot_parts(
            reg,
            kernel.slot_offset(slot_id),
            kernel.type_desc(kernel.slot_type_id(slot_id)),
        )
    return store_reg_to_slot_parts(
        reg,
        parsed_function_value_slot_offset(func, value_name),
        parsed_function_value_slot_type(func, value_name),
    )


def load_slot_to_reg_parts(
    offset: int, value_type: TypeDesc, reg: str
) -> list[str]:
    op = stack_load_op(value_type)
    if offset > 255:
        scratch = pick_scratch_gpr(reg)
        lines = emit_slot_base_address_parts(offset, scratch)
        lines.append(emitted_memory_instruction_line(op, reg, scratch))
        return lines
    return [emitted_memory_instruction_line(op, reg, "x29", -offset)]


def append_load_slot_to_reg_parts(
    owner: AArch64EmissionFragments,
    fragment: CompilerInt2,
    offset: int,
    is_integer: bool,
    width: int,
    reg: str,
) -> None:
    chunk_size = 8
    if is_integer and width <= 8:
        chunk_size = 1
    elif is_integer and width <= 16:
        chunk_size = 2
    op = chunk_load_op(chunk_size, stack=True)
    if chunk_size == 2:
        encode_emitted_load_store_parts(op, reg, "x29", 0)
    if offset > 255:
        scratch = pick_scratch_gpr(reg)
        append_slot_base_address_parts(owner, fragment, offset, scratch)
        owner.append_memory(fragment, op, reg, scratch)
        return
    owner.append_memory(fragment, op, reg, "x29", -offset)


def load_slot_to_reg(slot: SlotInfo, reg: str) -> list[str]:
    return load_slot_to_reg_parts(slot.offset, slot.type, reg)


def load_value_slot_to_reg(
    func: ParsedFunction, value_name: str, reg: str
) -> list[str]:
    if func.indexed_slot_projection:
        slot_id = parsed_function_value_slot_id(func, value_name)
        kernel = func.indexed_kernel
        return load_slot_to_reg_parts(
            kernel.slot_offset(slot_id),
            kernel.type_desc(kernel.slot_type_id(slot_id)),
            reg,
        )
    return load_slot_to_reg_parts(
        parsed_function_value_slot_offset(func, value_name),
        parsed_function_value_slot_type(func, value_name),
        reg,
    )


def load_from_address(addr_reg: str, dest_reg: str, value_type: TypeDesc) -> list[str]:
    return [
        emitted_memory_instruction_line(
            mem_load_op(value_type), dest_reg, addr_reg
        )
    ]


def store_to_address(addr_reg: str, src_reg: str, value_type: TypeDesc) -> list[str]:
    return [
        emitted_memory_instruction_line(
            mem_store_op(value_type), src_reg, addr_reg
        )
    ]


def copy_address_to_address(
    src_addr_reg: str, dst_addr_reg: str, size: int
) -> list[str]:
    lines: list[str] = []
    for offset, chunk_size in aggregate_copy_chunks(size):
        reg = _pick_copy_data_reg(src_addr_reg, dst_addr_reg, chunk_size=chunk_size)
        src_reg = src_addr_reg
        dst_reg = dst_addr_reg
        if offset:
            lines.extend(emit_add_offset("x16", src_addr_reg, offset))
            lines.extend(emit_add_offset("x17", dst_addr_reg, offset))
            src_reg = "x16"
            dst_reg = "x17"
        lines.append(
            emitted_memory_instruction_line(
                chunk_load_op(chunk_size, stack=False), reg, src_reg
            )
        )
        lines.append(
            emitted_memory_instruction_line(
                chunk_store_op(chunk_size, stack=False), reg, dst_reg
            )
        )
    return lines


def copy_address_to_slot_parts(
    src_addr_reg: str, offset: int, value_type: TypeDesc
) -> list[str]:
    dst_addr_reg = "x14" if src_addr_reg == "x13" else "x13"
    lines = emit_slot_base_address_parts(offset, dst_addr_reg)
    lines.extend(
        copy_address_to_address(src_addr_reg, dst_addr_reg, value_type.slot_size)
    )
    return lines


def copy_address_to_slot(src_addr_reg: str, slot: SlotInfo) -> list[str]:
    return copy_address_to_slot_parts(src_addr_reg, slot.offset, slot.type)


def copy_address_to_value_slot(
    src_addr_reg: str, func: ParsedFunction, value_name: str
) -> list[str]:
    if func.indexed_slot_projection:
        slot_id = parsed_function_value_slot_id(func, value_name)
        kernel = func.indexed_kernel
        return copy_address_to_slot_parts(
            src_addr_reg,
            kernel.slot_offset(slot_id),
            kernel.type_desc(kernel.slot_type_id(slot_id)),
        )
    return copy_address_to_slot_parts(
        src_addr_reg,
        parsed_function_value_slot_offset(func, value_name),
        parsed_function_value_slot_type(func, value_name),
    )


def copy_slot_to_address_parts(
    offset: int, value_type: TypeDesc, dst_addr_reg: str
) -> list[str]:
    lines = emit_slot_base_address_parts(offset, "x13")
    lines.extend(copy_address_to_address("x13", dst_addr_reg, value_type.slot_size))
    return lines


def copy_slot_to_address(slot: SlotInfo, dst_addr_reg: str) -> list[str]:
    return copy_slot_to_address_parts(slot.offset, slot.type, dst_addr_reg)


def copy_value_slot_to_address(
    func: ParsedFunction, value_name: str, dst_addr_reg: str
) -> list[str]:
    if func.indexed_slot_projection:
        slot_id = parsed_function_value_slot_id(func, value_name)
        kernel = func.indexed_kernel
        return copy_slot_to_address_parts(
            kernel.slot_offset(slot_id),
            kernel.type_desc(kernel.slot_type_id(slot_id)),
            dst_addr_reg,
        )
    return copy_slot_to_address_parts(
        parsed_function_value_slot_offset(func, value_name),
        parsed_function_value_slot_type(func, value_name),
        dst_addr_reg,
    )


def copy_slot_to_slot_parts(
    src_offset: int,
    src_type: TypeDesc,
    dst_offset: int,
    dst_type: TypeDesc,
) -> list[str]:
    lines = emit_slot_base_address_parts(src_offset, "x12")
    lines.extend(emit_slot_base_address_parts(dst_offset, "x13"))
    lines.extend(copy_address_to_address("x12", "x13", src_type.slot_size))
    return lines


def copy_slot_to_slot(src_slot: SlotInfo, dst_slot: SlotInfo) -> list[str]:
    return copy_slot_to_slot_parts(
        src_slot.offset, src_slot.type, dst_slot.offset, dst_slot.type
    )


def copy_value_slot_to_value_slot(
    func: ParsedFunction, src_name: str, dst_name: str
) -> list[str]:
    if func.indexed_slot_projection:
        src_slot_id = parsed_function_value_slot_id(func, src_name)
        dst_slot_id = parsed_function_value_slot_id(func, dst_name)
        kernel = func.indexed_kernel
        return copy_slot_to_slot_parts(
            kernel.slot_offset(src_slot_id),
            kernel.type_desc(kernel.slot_type_id(src_slot_id)),
            kernel.slot_offset(dst_slot_id),
            kernel.type_desc(kernel.slot_type_id(dst_slot_id)),
        )
    return copy_slot_to_slot_parts(
        parsed_function_value_slot_offset(func, src_name),
        parsed_function_value_slot_type(func, src_name),
        parsed_function_value_slot_offset(func, dst_name),
        parsed_function_value_slot_type(func, dst_name),
    )


def zero_address(addr_reg: str, size: int) -> list[str]:
    if size <= 0:
        return []
    zero_reg = "x14" if addr_reg != "x14" else "x15"
    lines = [emitted_movewide_instruction_line("movz", zero_reg, 0)]
    for offset, chunk_size in aggregate_copy_chunks(size):
        reg = zero_reg if chunk_size > 4 else f"w{zero_reg[1:]}"
        target_reg = addr_reg
        if offset:
            lines.extend(emit_add_offset("x16", addr_reg, offset))
            target_reg = "x16"
        lines.append(
            emitted_memory_instruction_line(
                chunk_store_op(chunk_size, stack=False), reg, target_reg
            )
        )
    return lines


def zero_slot_parts(offset: int, value_type: TypeDesc) -> list[str]:
    lines = emit_slot_base_address_parts(offset, "x15")
    lines.extend(zero_address("x15", value_type.slot_size))
    return lines


def zero_slot(slot: SlotInfo) -> list[str]:
    return zero_slot_parts(slot.offset, slot.type)


def zero_value_slot(func: ParsedFunction, value_name: str) -> list[str]:
    if func.indexed_slot_projection:
        slot_id = parsed_function_value_slot_id(func, value_name)
        kernel = func.indexed_kernel
        return zero_slot_parts(
            kernel.slot_offset(slot_id),
            kernel.type_desc(kernel.slot_type_id(slot_id)),
        )
    return zero_slot_parts(
        parsed_function_value_slot_offset(func, value_name),
        parsed_function_value_slot_type(func, value_name),
    )


def _store_aggregate_chunk_to_address(
    addr_reg: str,
    offset: int,
    src_reg: str,
    chunk_size: int,
) -> list[str]:
    lines: list[str] = []
    src_base = _base_xreg(src_reg)
    addr_scratch = _pick_temp_xreg(addr_reg, src_base)
    value_scratch = _pick_temp_xreg(addr_reg, src_base, addr_scratch)
    for sub_offset, sub_size in aggregate_copy_chunks(chunk_size):
        target_addr = addr_reg
        if offset + sub_offset:
            lines.extend(emit_add_offset(addr_scratch, addr_reg, offset + sub_offset))
            target_addr = addr_scratch
        store_reg = _chunk_reg_alias(src_base, sub_size)
        if sub_offset:
            lines.append(f"  lsr {value_scratch}, {src_base}, #{sub_offset * 8}")
            store_reg = _chunk_reg_alias(value_scratch, sub_size)
        lines.append(
            emitted_memory_instruction_line(
                chunk_store_op(sub_size, stack=False), store_reg, target_addr
            )
        )
    return lines


def _load_aggregate_chunk_from_address(
    addr_reg: str,
    offset: int,
    dest_reg: str,
    chunk_size: int,
) -> list[str]:
    lines: list[str] = []
    dest_base = _base_xreg(dest_reg)
    acc_base = dest_base
    if dest_base == addr_reg:
        acc_base = _pick_temp_xreg(addr_reg)
    dest_alias = _chunk_reg_alias(acc_base, chunk_size)
    value_scratch = _pick_temp_xreg(addr_reg, dest_base, acc_base)
    merge_reg = value_scratch if chunk_size > 4 else f"w{value_scratch[1:]}"
    addr_scratch = _pick_temp_xreg(addr_reg, dest_base, acc_base, value_scratch)
    lines.append(emitted_movewide_instruction_line("movz", dest_alias, 0))
    for sub_offset, sub_size in aggregate_copy_chunks(chunk_size):
        source_addr = addr_reg
        if offset + sub_offset:
            lines.extend(emit_add_offset(addr_scratch, addr_reg, offset + sub_offset))
            source_addr = addr_scratch
        load_reg = _chunk_reg_alias(value_scratch, sub_size)
        lines.append(
            emitted_memory_instruction_line(
                chunk_load_op(sub_size, stack=False), load_reg, source_addr
            )
        )
        if sub_offset:
            lines.append(f"  lsl {merge_reg}, {merge_reg}, #{sub_offset * 8}")
        lines.append(f"  orr {dest_alias}, {dest_alias}, {merge_reg}")
    if acc_base != dest_base:
        lines.append(
            emitted_move_register_line(
                _chunk_reg_alias(dest_base, chunk_size),
                dest_alias,
            )
        )
    return lines


def _hfa_value_memory_lines(
    value_type: TypeDesc,
    start_index: int,
    addr_reg: str,
    store: bool,
) -> list[str] | None:
    raw_hfa = aggregate_hfa_members(value_type)
    if not raw_hfa:
        return None
    hfa = list(raw_hfa)
    lines: list[str] = []
    index = 0
    while index < len(hfa):
        member_type, member_offset = hfa[index]
        prefix = "s" if member_type.width <= 32 else "d"
        reg = f"{prefix}{start_index + index}"
        op = mem_store_op(member_type) if store else mem_load_op(member_type)
        lines.append(
            emitted_memory_instruction_line(op, reg, addr_reg, member_offset)
        )
        index += 1
    return lines


def store_value_regs_to_slot_parts(
    offset: int, value_type: TypeDesc, start_index: int
) -> list[str]:
    if not (value_type.is_array or value_type.is_struct):
        return store_reg_to_slot_parts(
            reg_name(value_type, start_index), offset, value_type
        )
    if aggregate_hfa_members(value_type):
        lines = emit_slot_base_address_parts(offset, "x15")
        lines.extend(_hfa_value_memory_lines(value_type, start_index, "x15", True))
        return lines
    chunks = aggregate_reg_chunks(value_type)
    regs = abi_value_reg_names(value_type, start_index)
    if offset > 255 or any(
        chunk_size not in (1, 2, 4, 8) for chunk_size in chunks
    ):
        lines = emit_slot_base_address_parts(offset, "x15")
        chunk_offset = 0
        for reg, chunk_size in zip(regs, chunks):
            if chunk_size in (1, 2, 4, 8):
                lines.append(
                    emitted_memory_instruction_line(
                        chunk_store_op(chunk_size, stack=False),
                        reg,
                        "x15",
                        chunk_offset,
                    )
                )
            else:
                lines.extend(
                    _store_aggregate_chunk_to_address(
                        "x15", chunk_offset, reg, chunk_size
                    )
                )
            chunk_offset += chunk_size
        return lines
    lines: list[str] = []
    stack_offset = offset
    for reg, chunk_size in zip(regs, chunks):
        lines.append(
            emitted_memory_instruction_line(
                chunk_store_op(chunk_size, stack=True),
                reg,
                "x29",
                -stack_offset,
            )
        )
        stack_offset -= chunk_size
    return lines


def store_value_regs_to_slot(slot: SlotInfo, start_index: int) -> list[str]:
    return store_value_regs_to_slot_parts(slot.offset, slot.type, start_index)


def store_value_regs_to_value_slot(
    func: ParsedFunction, value_name: str, start_index: int
) -> list[str]:
    if func.indexed_slot_projection:
        slot_id = parsed_function_value_slot_id(func, value_name)
        kernel = func.indexed_kernel
        return store_value_regs_to_slot_parts(
            kernel.slot_offset(slot_id),
            kernel.type_desc(kernel.slot_type_id(slot_id)),
            start_index,
        )
    return store_value_regs_to_slot_parts(
        parsed_function_value_slot_offset(func, value_name),
        parsed_function_value_slot_type(func, value_name),
        start_index,
    )


def store_scalar_reg_to_value_slot_indexed(
    func: ParsedFunction,
    value_id: int,
    type_id: int,
    reg_index: int,
) -> list[str]:
    kernel = func.indexed_kernel
    slot_id = kernel.value_slot_id(value_id)
    if slot_id < 0:
        return []
    header: CompilerInt4 = kernel.type_header(type_id)
    if header.first == TYPE_KIND_INT and header.second <= 8:
        op = "sturb"
    elif header.first == TYPE_KIND_INT and header.second <= 16:
        op = "sturh"
    else:
        op = "stur"
    reg = reg_name_indexed(kernel, type_id, reg_index)
    offset = kernel.slot_offset(slot_id)
    if offset > 255:
        lines = emit_slot_base_address_parts(offset, "x15")
        lines.append(emitted_memory_instruction_line(op, reg, "x15"))
        return lines
    return [emitted_memory_instruction_line(op, reg, "x29", -offset)]


def load_slot_to_value_regs_parts(
    offset: int, value_type: TypeDesc, start_index: int
) -> list[str]:
    if not (value_type.is_array or value_type.is_struct):
        return load_slot_to_reg_parts(
            offset, value_type, reg_name(value_type, start_index)
        )
    if aggregate_hfa_members(value_type):
        lines = emit_slot_base_address_parts(offset, "x15")
        lines.extend(_hfa_value_memory_lines(value_type, start_index, "x15", False))
        return lines
    chunks = aggregate_reg_chunks(value_type)
    regs = abi_value_reg_names(value_type, start_index)
    if offset > 255 or any(
        chunk_size not in (1, 2, 4, 8) for chunk_size in chunks
    ):
        lines = emit_slot_base_address_parts(offset, "x15")
        chunk_offset = 0
        for reg, chunk_size in zip(regs, chunks):
            if chunk_size in (1, 2, 4, 8):
                lines.append(
                    emitted_memory_instruction_line(
                        chunk_load_op(chunk_size, stack=False),
                        reg,
                        "x15",
                        chunk_offset,
                    )
                )
            else:
                lines.extend(
                    _load_aggregate_chunk_from_address(
                        "x15", chunk_offset, reg, chunk_size
                    )
                )
            chunk_offset += chunk_size
        return lines
    lines: list[str] = []
    stack_offset = offset
    for reg, chunk_size in zip(regs, chunks):
        lines.append(
            emitted_memory_instruction_line(
                chunk_load_op(chunk_size, stack=True),
                reg,
                "x29",
                -stack_offset,
            )
        )
        stack_offset -= chunk_size
    return lines


def load_slot_to_value_regs(slot: SlotInfo, start_index: int) -> list[str]:
    return load_slot_to_value_regs_parts(slot.offset, slot.type, start_index)


def load_value_slot_to_value_regs(
    func: ParsedFunction, value_name: str, start_index: int
) -> list[str]:
    if func.indexed_slot_projection:
        slot_id = parsed_function_value_slot_id(func, value_name)
        kernel = func.indexed_kernel
        return load_slot_to_value_regs_parts(
            kernel.slot_offset(slot_id),
            kernel.type_desc(kernel.slot_type_id(slot_id)),
            start_index,
        )
    return load_slot_to_value_regs_parts(
        parsed_function_value_slot_offset(func, value_name),
        parsed_function_value_slot_type(func, value_name),
        start_index,
    )


def load_value_from_address(
    addr_reg: str, value_type: TypeDesc, start_index: int
) -> list[str]:
    if not (value_type.is_array or value_type.is_struct):
        return load_from_address(
            addr_reg, reg_name(value_type, start_index), value_type
        )
    hfa_lines = _hfa_value_memory_lines(value_type, start_index, addr_reg, False)
    if hfa_lines is not None:
        return hfa_lines
    regs = abi_value_reg_names(value_type, start_index)
    lines, base_addr_reg = _prepare_nonconflicting_addr_reg(addr_reg, regs)
    offset = 0
    for reg, chunk_size in zip(regs, aggregate_reg_chunks(value_type)):
        if chunk_size in (1, 2, 4, 8):
            lines.append(
                emitted_memory_instruction_line(
                    chunk_load_op(chunk_size, stack=False),
                    reg,
                    base_addr_reg,
                    offset,
                )
            )
        else:
            lines.extend(
                _load_aggregate_chunk_from_address(
                    base_addr_reg, offset, reg, chunk_size
                )
            )
        offset += chunk_size
    return lines


def store_value_to_address(
    addr_reg: str, value_type: TypeDesc, start_index: int
) -> list[str]:
    if not (value_type.is_array or value_type.is_struct):
        return store_to_address(addr_reg, reg_name(value_type, start_index), value_type)
    hfa_lines = _hfa_value_memory_lines(value_type, start_index, addr_reg, True)
    if hfa_lines is not None:
        return hfa_lines
    regs = abi_value_reg_names(value_type, start_index)
    lines, base_addr_reg = _prepare_nonconflicting_addr_reg(addr_reg, regs)
    offset = 0
    for reg, chunk_size in zip(regs, aggregate_reg_chunks(value_type)):
        if chunk_size in (1, 2, 4, 8):
            lines.append(
                emitted_memory_instruction_line(
                    chunk_store_op(chunk_size, stack=False),
                    reg,
                    base_addr_reg,
                    offset,
                )
            )
        else:
            lines.extend(
                _store_aggregate_chunk_to_address(
                    base_addr_reg, offset, reg, chunk_size
                )
            )
        offset += chunk_size
    return lines
