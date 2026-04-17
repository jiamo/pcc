from __future__ import annotations

"""Asm-first self backend bootstrap for x86_64 Linux.

This is the first truthful translated slice for the Linux x86_64 target. It is
intentionally narrow and explicit:

- scalar integer / pointer args and returns
- local `alloca`, scalar `load`, scalar `store`
- integer scalar `binop`
- direct calls
- `ret` / `ret void`

Anything outside this slice still raises ``BackendUnavailable`` instead of
guessing.
"""

import struct

from . import BackendUnavailable
from .self_backend_emit import emit_function_blocks
from .self_backend_instruction_dispatch import emit_instruction_dispatch
from .self_backend_ir import ParsedFunction, ParsedInstr, TypeDesc, _align_to
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import (
    aggregate_literal_to_bytes,
    const_int_from_value,
    is_aggregate_literal_value,
)
from .self_backend_prepare import prepare_module_for_target
from .self_backend_target_match import is_x86_64_linux_triple
from .self_backend_terminator_dispatch import emit_terminator_dispatch
from .self_backend_x86_64_linux_data import emit_globals

_MODULE_SYMBOLS = PreparedModuleSymbols(
    internal_prefix="",
    defined_symbols=frozenset(),
    internal_symbols=frozenset(),
)

_ARG_REGS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")
_ARG_REGS_32 = ("edi", "esi", "edx", "ecx", "r8d", "r9d")
_ARG_REGS_16 = ("di", "si", "dx", "cx", "r8w", "r9w")
_ARG_REGS_8 = ("dil", "sil", "dl", "cl", "r8b", "r9b")
_FP_ARG_REGS = ("xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5", "xmm6", "xmm7")


def _asm_symbol(name: str) -> str:
    if name in _MODULE_SYMBOLS.internal_symbols:
        return f"{_MODULE_SYMBOLS.internal_prefix}{name}"
    return name


def _mem_size(type_desc: TypeDesc) -> str:
    if type_desc.is_fp:
        return "QWORD PTR" if type_desc.width > 32 else "DWORD PTR"
    if type_desc.is_ptr or type_desc.width > 32:
        return "QWORD PTR"
    if type_desc.width <= 8:
        return "BYTE PTR"
    if type_desc.width <= 16:
        return "WORD PTR"
    return "DWORD PTR"


def _slot_addr(offset: int) -> str:
    return f"[rbp - {offset}]"


def _stack_arg_addr(offset: int) -> str:
    return f"[rbp + {offset}]"


def _reg_name(type_desc: TypeDesc, index: int) -> str:
    if index not in (0, 1, 10, 11):
        raise BackendUnavailable(f"x86_64 self backend scratch register index {index} not supported")
    if type_desc.is_fp:
        return {0: "xmm0", 1: "xmm1", 10: "xmm10", 11: "xmm11"}[index]
    if type_desc.is_ptr:
        return {0: "rax", 1: "rbx", 10: "r10", 11: "r11"}[index]
    if type_desc.width <= 8:
        return {0: "al", 1: "bl", 10: "r10b", 11: "r11b"}[index]
    if type_desc.width <= 16:
        return {0: "ax", 1: "bx", 10: "r10w", 11: "r11w"}[index]
    if type_desc.width <= 32:
        return {0: "eax", 1: "ebx", 10: "r10d", 11: "r11d"}[index]
    return {0: "rax", 1: "rbx", 10: "r10", 11: "r11"}[index]


def _stack_arg_reg(type_desc: TypeDesc, index: int) -> str:
    if index >= len(_ARG_REGS):
        raise BackendUnavailable("x86_64 self backend stack args beyond six regs not translated yet")
    if type_desc.is_fp:
        raise BackendUnavailable("x86_64 self backend fp args not translated yet")
    if type_desc.is_ptr:
        return _ARG_REGS[index]
    if type_desc.width <= 8:
        return _ARG_REGS_8[index]
    if type_desc.width <= 16:
        return _ARG_REGS_16[index]
    if type_desc.width <= 32:
        return _ARG_REGS_32[index]
    return _ARG_REGS[index]


def _is_memory_aggregate_arg(type_desc: TypeDesc) -> bool:
    return (type_desc.is_array or type_desc.is_struct) and type_desc.slot_size > 16


def _stack_arg_storage_size(type_desc: TypeDesc) -> int:
    if _is_memory_aggregate_arg(type_desc):
        return _align_to(type_desc.slot_size, 8)
    return 8


def _iter_arg_locations(arg_types: list[TypeDesc]) -> tuple[list[tuple[str, str | int]], int, int]:
    locations: list[tuple[str, str | int]] = []
    gp_index = 0
    fp_index = 0
    stack_offset = 0
    for arg_type in arg_types:
        if _is_memory_aggregate_arg(arg_type):
            locations.append(("stack_byval", stack_offset))
            stack_offset += _stack_arg_storage_size(arg_type)
            continue
        if arg_type.is_fp:
            if fp_index < len(_FP_ARG_REGS):
                locations.append(("reg", _FP_ARG_REGS[fp_index]))
                fp_index += 1
                continue
            locations.append(("stack_scalar", stack_offset))
            stack_offset += _stack_arg_storage_size(arg_type)
            continue
        if arg_type.is_array or arg_type.is_struct:
            raise BackendUnavailable(
                f"x86_64 self backend aggregate arg classification not translated yet: {arg_type.describe()}"
            )
        if gp_index < len(_ARG_REGS):
            locations.append(("reg", _stack_arg_reg(arg_type, gp_index)))
            gp_index += 1
            continue
        locations.append(("stack_scalar", stack_offset))
        stack_offset += _stack_arg_storage_size(arg_type)
    return locations, stack_offset, fp_index


def _store_reg_to_slot(reg: str, offset: int, type_desc: TypeDesc) -> list[str]:
    if type_desc.is_fp:
        op = "movsd" if type_desc.width > 32 else "movss"
        return [f"  {op} {_mem_size(type_desc)} {_slot_addr(offset)}, {reg}"]
    return [f"  mov {_mem_size(type_desc)} {_slot_addr(offset)}, {reg}"]


def _load_slot_to_reg(offset: int, reg: str, type_desc: TypeDesc) -> list[str]:
    if type_desc.is_fp:
        op = "movsd" if type_desc.width > 32 else "movss"
        return [f"  {op} {reg}, {_mem_size(type_desc)} {_slot_addr(offset)}"]
    return [f"  mov {reg}, {_mem_size(type_desc)} {_slot_addr(offset)}"]


def _global_addr(name: str) -> str:
    return f"{_asm_symbol(name[1:])}[rip]"


def _block_label(func_name: str, block_name: str) -> str:
    return f".L{func_name}_{block_name}"


def _edge_label(func_name: str, source_block: str, target_block: str) -> str:
    return f".L{func_name}_{source_block}_to_{target_block}"


def _load_global_to_reg(name: str, reg: str, type_desc: TypeDesc) -> list[str]:
    if type_desc.is_fp:
        op = "movsd" if type_desc.width > 32 else "movss"
        return [f"  {op} {reg}, {_mem_size(type_desc)} {_global_addr(name)}"]
    return [f"  mov {reg}, {_mem_size(type_desc)} {_global_addr(name)}"]


def _store_reg_to_global(name: str, reg: str, type_desc: TypeDesc) -> list[str]:
    if type_desc.is_fp:
        op = "movsd" if type_desc.width > 32 else "movss"
        return [f"  {op} {_mem_size(type_desc)} {_global_addr(name)}, {reg}"]
    return [f"  mov {_mem_size(type_desc)} {_global_addr(name)}, {reg}"]


def _load_from_address(addr_reg: str, reg: str, type_desc: TypeDesc) -> list[str]:
    if type_desc.is_fp:
        op = "movsd" if type_desc.width > 32 else "movss"
        return [f"  {op} {reg}, {_mem_size(type_desc)} [{addr_reg}]"]
    return [f"  mov {reg}, {_mem_size(type_desc)} [{addr_reg}]"]


def _store_to_address(addr_reg: str, reg: str, type_desc: TypeDesc) -> list[str]:
    if type_desc.is_fp:
        op = "movsd" if type_desc.width > 32 else "movss"
        return [f"  {op} {_mem_size(type_desc)} [{addr_reg}], {reg}"]
    return [f"  mov {_mem_size(type_desc)} [{addr_reg}], {reg}"]


def _zero_address(addr_reg: str, size: int) -> list[str]:
    if size <= 0:
        return []
    lines = ["  xor eax, eax"]
    offset = 0
    while size - offset >= 8:
        lines.append(f"  mov QWORD PTR [{addr_reg} + {offset}], rax")
        offset += 8
    if size - offset >= 4:
        lines.append(f"  mov DWORD PTR [{addr_reg} + {offset}], eax")
        offset += 4
    if size - offset >= 2:
        lines.append(f"  mov WORD PTR [{addr_reg} + {offset}], ax")
        offset += 2
    if size - offset >= 1:
        lines.append(f"  mov BYTE PTR [{addr_reg} + {offset}], al")
        offset += 1
    return lines


def _copy_address_to_address(src_reg: str, dst_reg: str, size: int) -> list[str]:
    if size <= 0:
        return []
    lines: list[str] = []
    offset = 0
    while size - offset >= 8:
        lines.append(f"  mov rax, QWORD PTR [{src_reg} + {offset}]")
        lines.append(f"  mov QWORD PTR [{dst_reg} + {offset}], rax")
        offset += 8
    if size - offset >= 4:
        lines.append(f"  mov eax, DWORD PTR [{src_reg} + {offset}]")
        lines.append(f"  mov DWORD PTR [{dst_reg} + {offset}], eax")
        offset += 4
    if size - offset >= 2:
        lines.append(f"  mov ax, WORD PTR [{src_reg} + {offset}]")
        lines.append(f"  mov WORD PTR [{dst_reg} + {offset}], ax")
        offset += 2
    if size - offset >= 1:
        lines.append(f"  mov al, BYTE PTR [{src_reg} + {offset}]")
        lines.append(f"  mov BYTE PTR [{dst_reg} + {offset}], al")
    return lines


def _materialize_fp_constant(value: str, type_desc: TypeDesc, reg: str) -> list[str]:
    if type_desc.width <= 32:
        if value.startswith("0x"):
            bits = int(value, 16) & 0xFFFFFFFF
        else:
            bits = struct.unpack("<I", struct.pack("<f", float(value)))[0]
        gp_reg = "r10d" if reg == "xmm10" else "r11d"
        return [f"  mov {gp_reg}, 0x{bits:08x}", f"  movd {reg}, {gp_reg}"]
    if value.startswith("0x"):
        bits = int(value, 16) & 0xFFFFFFFFFFFFFFFF
    else:
        bits = struct.unpack("<Q", struct.pack("<d", float(value)))[0]
    gp_reg = "r10" if reg == "xmm10" else "r11"
    return [f"  mov {gp_reg}, 0x{bits:016x}", f"  movq {reg}, {gp_reg}"]


def _materialize_value(func: ParsedFunction, value: str, type_desc: TypeDesc, reg: str) -> list[str]:
    if type_desc.is_void:
        return []
    if value == "null":
        return [f"  xor {reg}, {reg}"]
    if value in func.alloca_slots and type_desc.is_ptr:
        return [f"  lea {reg}, {_slot_addr(func.alloca_slots[value].offset)}"]
    if value.startswith("@"):
        if type_desc.is_ptr:
            return [f"  lea {reg}, {_global_addr(value)}"]
        return _load_global_to_reg(value, reg, type_desc)
    if value in func.value_slots:
        slot = func.value_slots[value]
        return _load_slot_to_reg(slot.offset, reg, type_desc)
    if type_desc.is_fp:
        return _materialize_fp_constant(value, type_desc, reg)
    const_value = const_int_from_value(value)
    if const_value is None:
        raise BackendUnavailable(
            f"x86_64 self backend cannot materialize value {value!r} as {type_desc.describe()} in {func.name!r}"
        )
    return [f"  mov {reg}, {const_value}"]


def _materialize_aggregate_value_address(
    func: ParsedFunction,
    value: str,
    type_desc: TypeDesc,
    reg: str,
) -> list[str]:
    if value in func.value_slots:
        return [f"  lea {reg}, {_slot_addr(func.value_slots[value].offset)}"]
    if value.startswith("@"):
        return [f"  lea {reg}, {_global_addr(value)}"]
    raise BackendUnavailable(
        f"x86_64 self backend aggregate value source not translated yet in {func.name!r}: {value}"
    )


def _emit_memcpy_intrinsic_call(
    func: ParsedFunction,
    args: tuple[tuple[TypeDesc, str], ...],
) -> list[str]:
    if len(args) < 4:
        raise BackendUnavailable(
            f"x86_64 self backend memcpy intrinsic expects at least 4 args in {func.name!r}"
        )
    dst_type, dst_value = args[0]
    src_type, src_value = args[1]
    size_type, size_value = args[2]
    _isvolatile_type, _isvolatile_value = args[3]
    if not (dst_type.is_ptr and src_type.is_ptr and size_type.is_int):
        raise BackendUnavailable(
            f"x86_64 self backend memcpy intrinsic arg types not translated yet in {func.name!r}"
        )
    size = const_int_from_value(size_value)
    if size is None:
        raise BackendUnavailable(
            f"x86_64 self backend memcpy intrinsic only supports constant sizes right now in {func.name!r}"
        )
    lines = _materialize_value(func, src_value, src_type, "r10")
    lines.extend(_materialize_value(func, dst_value, dst_type, "r11"))
    lines.extend(_copy_address_to_address("r10", "r11", size))
    return lines


def _sign_extend_reg_to_r10(src_type: TypeDesc) -> list[str]:
    if src_type.width <= 8:
        return ["  movsx r10, r10b"]
    if src_type.width <= 16:
        return ["  movsx r10, r10w"]
    if src_type.width <= 32:
        return ["  movsxd r10, r10d"]
    return []


def _materialize_index_to_r10(func: ParsedFunction, index_value: str) -> list[str]:
    const_index = const_int_from_value(index_value)
    if const_index is not None:
        return [f"  mov r10, {const_index}"]
    if index_value.startswith("@"):
        raise BackendUnavailable("x86_64 self backend does not support symbol-valued getelementptr indices")
    index_type = func.value_types.get(index_value)
    if index_type is None:
        raise BackendUnavailable(f"x86_64 self backend does not know getelementptr index value {index_value!r}")
    if not index_type.is_int:
        raise BackendUnavailable(
            f"x86_64 self backend getelementptr index type not translated yet in {func.name!r}: {index_type.describe()}"
        )
    lines = _materialize_value(func, index_value, index_type, _reg_name(index_type, 10))
    if index_type.width < 64:
        lines.extend(_sign_extend_reg_to_r10(index_type))
    return lines


def _emit_add_immediate_to_r11(offset: int) -> list[str]:
    if offset == 0:
        return []
    if offset > 0:
        return [f"  lea r11, [r11 + {offset}]"]
    return [f"  lea r11, [r11 - {-offset}]"]


def _emit_indexed_pointer_add(func: ParsedFunction, index_value: str, elem_size: int) -> list[str]:
    if elem_size == 0:
        const_index = const_int_from_value(index_value)
        if const_index == 0:
            return []
        raise BackendUnavailable("x86_64 self backend cannot index into zero-sized element type")
    const_index = const_int_from_value(index_value)
    if const_index is not None:
        return _emit_add_immediate_to_r11(const_index * elem_size)

    lines = _materialize_index_to_r10(func, index_value)
    if elem_size == 1:
        lines.append("  add r11, r10")
        return lines
    if elem_size in (2, 4, 8):
        lines.append(f"  lea r11, [r11 + r10*{elem_size}]")
        return lines
    lines.extend(
        [
            f"  imul r10, r10, {elem_size}",
            "  add r11, r10",
        ]
    )
    return lines


def _emit_gep_instruction(
    func: ParsedFunction,
    dest: str,
    base_type: TypeDesc,
    ptr_name: str,
    indices: tuple[tuple[TypeDesc, str], ...],
) -> list[str]:
    if dest not in func.value_slots:
        return []
    if not indices:
        raise BackendUnavailable("x86_64 self backend getelementptr requires at least one index")
    ptr_type = func.value_types.get(ptr_name, TypeDesc("ptr", pointee=base_type))
    lines = _materialize_value(func, ptr_name, ptr_type, "r11")
    current = base_type
    lines.extend(_emit_indexed_pointer_add(func, indices[0][1], current.slot_size))
    for _index_type, index_value in indices[1:]:
        if current.is_array:
            assert current.elem is not None
            current = current.elem
            lines.extend(_emit_indexed_pointer_add(func, index_value, current.slot_size))
            continue
        if current.is_struct:
            struct_type = current
            field_index = const_int_from_value(index_value)
            if field_index is None:
                raise BackendUnavailable(
                    "x86_64 self backend struct getelementptr currently requires constant field indices"
                )
            current = struct_type.field_type(field_index)
            lines.extend(_emit_add_immediate_to_r11(struct_type.field_offset(field_index)))
            continue
        else:
            raise BackendUnavailable(
                f"x86_64 self backend cannot index into scalar pointee {current.describe()} with more getelementptr indices"
            )
    dest_type = func.value_types.get(dest, TypeDesc("ptr", pointee=current))
    lines.extend(_store_reg_to_slot("r11", func.value_slots[dest].offset, dest_type))
    return lines


def _emit_prologue(func: ParsedFunction) -> list[str]:
    symbol = _asm_symbol(func.name)
    lines = ["", ".text", ".p2align 4, 0x90"]
    if func.is_global:
        lines.append(f".globl {symbol}")
    lines.append(f".type {symbol}, @function")
    lines.append(f"{symbol}:")
    lines.append("  push rbp")
    lines.append("  mov rbp, rsp")
    if func.frame_size:
        lines.append(f"  sub rsp, {func.frame_size}")
    arg_locations, _total_stack, _fp_reg_count = _iter_arg_locations([arg.type for arg in func.args])
    for arg, (kind, payload) in zip(func.args, arg_locations, strict=False):
        if arg.name not in func.value_slots:
            continue
        if kind == "reg":
            lines.extend(_store_reg_to_slot(str(payload), func.value_slots[arg.name].offset, arg.type))
            continue
        if kind == "stack_scalar":
            stack_offset = 16 + int(payload)
            scratch = _reg_name(arg.type, 10)
            lines.append(f"  mov {scratch}, {_mem_size(arg.type)} {_stack_arg_addr(stack_offset)}")
            lines.extend(_store_reg_to_slot(scratch, func.value_slots[arg.name].offset, arg.type))
            continue
        if kind == "stack_byval":
            stack_offset = 16 + int(payload)
            lines.append(f"  lea r10, {_stack_arg_addr(stack_offset)}")
            lines.append(f"  lea r11, {_slot_addr(func.value_slots[arg.name].offset)}")
            lines.extend(_copy_address_to_address("r10", "r11", arg.type.slot_size))
            continue
        raise BackendUnavailable(
            f"x86_64 self backend arg location kind not translated yet in {func.name!r}: {kind}"
        )
    return lines


def _emit_memory_instruction(func: ParsedFunction, kind: str, data: tuple) -> list[str] | None:
    if kind == "alloca":
        return []

    if kind == "store":
        value_type, value, _ptr_type, ptr_name = data
        if value_type.is_array or value_type.is_struct:
            if ptr_name in func.alloca_slots:
                lines = [f"  lea r11, {_slot_addr(func.alloca_slots[ptr_name].offset)}"]
            elif ptr_name.startswith("@"):
                lines = [f"  lea r11, {_global_addr(ptr_name)}"]
            elif ptr_name in func.value_slots:
                ptr_type = func.value_types.get(ptr_name)
                if ptr_type is None or not ptr_type.is_ptr:
                    raise BackendUnavailable(
                        f"x86_64 self backend aggregate store pointer source is not a pointer in {func.name!r}: {ptr_name}"
                    )
                lines = _materialize_value(func, ptr_name, ptr_type, "r11")
            else:
                raise BackendUnavailable(
                    f"x86_64 self backend aggregate store destination not translated yet in {func.name!r}: {ptr_name}"
                )
            if value == "zeroinitializer":
                lines.extend(_zero_address("r11", value_type.slot_size))
                return lines
            lines.extend(_materialize_aggregate_value_address(func, value, value_type, "r10"))
            lines.extend(_copy_address_to_address("r10", "r11", value_type.slot_size))
            return lines
        if not (value_type.is_int or value_type.is_ptr or value_type.is_fp):
            raise BackendUnavailable(
                f"x86_64 self backend scalar store type not translated yet in {func.name!r}: {value_type.describe()}"
            )
        if ptr_name in func.alloca_slots:
            lines = _materialize_value(func, value, value_type, _reg_name(value_type, 10))
            lines.extend(_store_reg_to_slot(_reg_name(value_type, 10), func.alloca_slots[ptr_name].offset, value_type))
            return lines
        if ptr_name.startswith("@"):
            lines = _materialize_value(func, value, value_type, _reg_name(value_type, 10))
            lines.extend(_store_reg_to_global(ptr_name, _reg_name(value_type, 10), value_type))
            return lines
        if ptr_name in func.value_slots:
            ptr_type = func.value_types.get(ptr_name)
            if ptr_type is None or not ptr_type.is_ptr:
                raise BackendUnavailable(
                    f"x86_64 self backend store pointer source is not a pointer in {func.name!r}: {ptr_name}"
                )
            lines = _materialize_value(func, value, value_type, _reg_name(value_type, 10))
            lines.extend(_materialize_value(func, ptr_name, ptr_type, "r11"))
            lines.extend(_store_to_address("r11", _reg_name(value_type, 10), value_type))
            return lines
        raise BackendUnavailable(
            f"x86_64 self backend only supports scalar store to local alloca slots, globals, or pointer-valued SSA right now in {func.name!r}: {ptr_name}"
        )

    if kind == "load":
        dest, value_type, _ptr_type, ptr_name = data
        if dest not in func.value_slots:
            return []
        if value_type.is_array or value_type.is_struct:
            if ptr_name in func.alloca_slots:
                lines = [f"  lea r10, {_slot_addr(func.alloca_slots[ptr_name].offset)}"]
            elif ptr_name.startswith("@"):
                lines = [f"  lea r10, {_global_addr(ptr_name)}"]
            elif ptr_name in func.value_slots:
                ptr_type = func.value_types.get(ptr_name)
                if ptr_type is None or not ptr_type.is_ptr:
                    raise BackendUnavailable(
                        f"x86_64 self backend aggregate load pointer source is not a pointer in {func.name!r}: {ptr_name}"
                    )
                lines = _materialize_value(func, ptr_name, ptr_type, "r10")
            else:
                raise BackendUnavailable(
                    f"x86_64 self backend aggregate load source not translated yet in {func.name!r}: {ptr_name}"
                )
            lines.append(f"  lea r11, {_slot_addr(func.value_slots[dest].offset)}")
            lines.extend(_copy_address_to_address("r10", "r11", value_type.slot_size))
            return lines
        if not (value_type.is_int or value_type.is_ptr or value_type.is_fp):
            raise BackendUnavailable(
                f"x86_64 self backend scalar load type not translated yet in {func.name!r}: {value_type.describe()}"
            )
        if ptr_name in func.alloca_slots:
            lines = _load_slot_to_reg(func.alloca_slots[ptr_name].offset, _reg_name(value_type, 10), value_type)
        elif ptr_name.startswith("@"):
            lines = _load_global_to_reg(ptr_name, _reg_name(value_type, 10), value_type)
        elif ptr_name in func.value_slots:
            ptr_type = func.value_types.get(ptr_name)
            if ptr_type is None or not ptr_type.is_ptr:
                raise BackendUnavailable(
                    f"x86_64 self backend load pointer source is not a pointer in {func.name!r}: {ptr_name}"
                )
            lines = _materialize_value(func, ptr_name, ptr_type, "r11")
            lines.extend(_load_from_address("r11", _reg_name(value_type, 10), value_type))
        else:
            raise BackendUnavailable(
                f"x86_64 self backend only supports scalar load from local alloca slots, globals, or pointer-valued SSA right now in {func.name!r}: {ptr_name}"
            )
        lines.extend(_store_reg_to_slot(_reg_name(value_type, 10), func.value_slots[dest].offset, value_type))
        return lines

    return None


def _emit_compute_instruction(func: ParsedFunction, kind: str, data: tuple) -> list[str] | None:
    if kind == "binop":
        op, dest, value_type, lhs, rhs = data
        if dest not in func.value_slots:
            return []
        if not value_type.is_int:
            raise BackendUnavailable(
                f"x86_64 self backend binop type not translated yet in {func.name!r}: {value_type.describe()}"
            )
        lhs_reg = _reg_name(value_type, 10)
        rhs_reg = _reg_name(value_type, 11)
        lines = _materialize_value(func, lhs, value_type, lhs_reg)
        lines.extend(_materialize_value(func, rhs, value_type, rhs_reg))
        if op == "add":
            lines.append(f"  add {lhs_reg}, {rhs_reg}")
        elif op == "sub":
            lines.append(f"  sub {lhs_reg}, {rhs_reg}")
        elif op == "mul":
            lines.append(f"  imul {lhs_reg}, {rhs_reg}")
        elif op == "and":
            lines.append(f"  and {lhs_reg}, {rhs_reg}")
        elif op == "or":
            lines.append(f"  or {lhs_reg}, {rhs_reg}")
        elif op == "xor":
            lines.append(f"  xor {lhs_reg}, {rhs_reg}")
        elif op in {"sdiv", "srem", "udiv", "urem"}:
            dividend_reg = _reg_name(value_type, 0)
            divisor_reg = _reg_name(value_type, 10)
            lines = _materialize_value(func, lhs, value_type, dividend_reg)
            lines.extend(_materialize_value(func, rhs, value_type, divisor_reg))
            if value_type.width <= 32:
                if op in {"sdiv", "srem"}:
                    lines.append("  cdq")
                    lines.append(f"  idiv {divisor_reg}")
                    result_reg = "eax" if op == "sdiv" else "edx"
                else:
                    lines.append("  xor edx, edx")
                    lines.append(f"  div {divisor_reg}")
                    result_reg = "eax" if op == "udiv" else "edx"
            else:
                if op in {"sdiv", "srem"}:
                    lines.append("  cqo")
                    lines.append(f"  idiv {divisor_reg}")
                    result_reg = "rax" if op == "sdiv" else "rdx"
                else:
                    lines.append("  xor rdx, rdx")
                    lines.append(f"  div {divisor_reg}")
                    result_reg = "rax" if op == "udiv" else "rdx"
            lines.extend(_store_reg_to_slot(result_reg, func.value_slots[dest].offset, value_type))
            return lines
        else:
            raise BackendUnavailable(
                f"x86_64 self backend binop {op!r} not translated yet in {func.name!r}"
            )
        lines.extend(_store_reg_to_slot(lhs_reg, func.value_slots[dest].offset, value_type))
        return lines

    if kind == "fbinop":
        op, dest, value_type, lhs, rhs = data
        if dest not in func.value_slots:
            return []
        if not value_type.is_fp:
            raise BackendUnavailable(
                f"x86_64 self backend fbinop type not translated yet in {func.name!r}: {value_type.describe()}"
            )
        lhs_reg = _reg_name(value_type, 10)
        rhs_reg = _reg_name(value_type, 11)
        lines = _materialize_value(func, lhs, value_type, lhs_reg)
        lines.extend(_materialize_value(func, rhs, value_type, rhs_reg))
        if op == "fadd":
            instr = "addsd" if value_type.width > 32 else "addss"
        elif op == "fsub":
            instr = "subsd" if value_type.width > 32 else "subss"
        elif op == "fmul":
            instr = "mulsd" if value_type.width > 32 else "mulss"
        elif op == "fdiv":
            instr = "divsd" if value_type.width > 32 else "divss"
        else:
            raise BackendUnavailable(
                f"x86_64 self backend fbinop {op!r} not translated yet in {func.name!r}"
            )
        lines.append(f"  {instr} {lhs_reg}, {rhs_reg}")
        lines.extend(_store_reg_to_slot(lhs_reg, func.value_slots[dest].offset, value_type))
        return lines

    if kind == "fneg":
        dest, value_type, value = data
        if dest not in func.value_slots:
            return []
        if not value_type.is_fp:
            raise BackendUnavailable(
                f"x86_64 self backend fneg type not translated yet in {func.name!r}: {value_type.describe()}"
            )
        reg = _reg_name(value_type, 10)
        lines = _materialize_value(func, value, value_type, reg)
        if value_type.width <= 32:
            lines.extend(
                [
                    "  mov r11d, 0x80000000",
                    "  movd xmm11, r11d",
                    "  xorps xmm10, xmm11",
                ]
            )
        else:
            lines.extend(
                [
                    "  mov r11, 0x8000000000000000",
                    "  movq xmm11, r11",
                    "  xorpd xmm10, xmm11",
                ]
            )
        lines.extend(_store_reg_to_slot(reg, func.value_slots[dest].offset, value_type))
        return lines

    if kind == "icmp":
        cond, dest, value_type, lhs, rhs = data
        if dest not in func.value_slots:
            return []
        if not (value_type.is_int or value_type.is_ptr):
            raise BackendUnavailable(
                f"x86_64 self backend icmp type not translated yet in {func.name!r}: {value_type.describe()}"
            )
        lhs_reg = _reg_name(value_type, 10)
        rhs_reg = _reg_name(value_type, 11)
        lines = _materialize_value(func, lhs, value_type, lhs_reg)
        lines.extend(_materialize_value(func, rhs, value_type, rhs_reg))
        lines.append(f"  cmp {lhs_reg}, {rhs_reg}")
        if cond == "eq":
            lines.append("  sete al")
        elif cond == "ne":
            lines.append("  setne al")
        elif cond == "slt":
            lines.append("  setl al")
        elif cond == "sle":
            lines.append("  setle al")
        elif cond == "sgt":
            lines.append("  setg al")
        elif cond == "sge":
            lines.append("  setge al")
        elif cond == "ult":
            lines.append("  setb al")
        elif cond == "ule":
            lines.append("  setbe al")
        elif cond == "ugt":
            lines.append("  seta al")
        elif cond == "uge":
            lines.append("  setae al")
        else:
            raise BackendUnavailable(
                f"x86_64 self backend icmp predicate {cond!r} not translated yet in {func.name!r}"
            )
        lines.extend(_store_reg_to_slot("al", func.value_slots[dest].offset, TypeDesc("int", 1)))
        return lines

    if kind == "fcmp":
        cond, dest, value_type, lhs, rhs = data
        if dest not in func.value_slots:
            return []
        if not value_type.is_fp:
            raise BackendUnavailable(
                f"x86_64 self backend fcmp type not translated yet in {func.name!r}: {value_type.describe()}"
            )
        lhs_reg = _reg_name(value_type, 10)
        rhs_reg = _reg_name(value_type, 11)
        lines = _materialize_value(func, lhs, value_type, lhs_reg)
        lines.extend(_materialize_value(func, rhs, value_type, rhs_reg))
        cmp_op = "ucomisd" if value_type.width > 32 else "ucomiss"
        lines.append(f"  {cmp_op} {lhs_reg}, {rhs_reg}")
        if cond == "olt":
            lines.extend(["  setb al", "  setnp bl", "  and al, bl"])
        elif cond == "ole":
            lines.extend(["  setbe al", "  setnp bl", "  and al, bl"])
        elif cond == "ogt":
            lines.extend(["  seta al", "  setnp bl", "  and al, bl"])
        elif cond == "oge":
            lines.extend(["  setae al", "  setnp bl", "  and al, bl"])
        elif cond == "oeq":
            lines.extend(["  sete al", "  setnp bl", "  and al, bl"])
        elif cond == "one":
            lines.extend(["  setne al", "  setnp bl", "  and al, bl"])
        elif cond == "ult":
            lines.extend(["  setb al", "  setp bl", "  or al, bl"])
        elif cond == "ule":
            lines.extend(["  setbe al", "  setp bl", "  or al, bl"])
        elif cond == "ugt":
            lines.extend(["  seta al", "  setp bl", "  or al, bl"])
        elif cond == "uge":
            lines.extend(["  setae al", "  setp bl", "  or al, bl"])
        elif cond == "ueq":
            lines.extend(["  sete al", "  setp bl", "  or al, bl"])
        elif cond == "une":
            lines.extend(["  setne al", "  setp bl", "  or al, bl"])
        elif cond == "uno":
            lines.append("  setp al")
        else:
            raise BackendUnavailable(
                f"x86_64 self backend fcmp predicate {cond!r} not translated yet in {func.name!r}"
            )
        lines.extend(_store_reg_to_slot("al", func.value_slots[dest].offset, TypeDesc("int", 1)))
        return lines

    if kind == "cast":
        op, dest, src_type, value, dst_type = data
        if dest not in func.value_slots:
            return []
        if op == "zext" and src_type.is_int and dst_type.is_int and src_type.width <= dst_type.width:
            if value in func.value_slots and src_type.width <= 8:
                lines = [f"  movzx {_reg_name(dst_type, 10)}, BYTE PTR {_slot_addr(func.value_slots[value].offset)}"]
            elif value in func.value_slots and src_type.width <= 16:
                lines = [f"  movzx {_reg_name(dst_type, 10)}, WORD PTR {_slot_addr(func.value_slots[value].offset)}"]
            else:
                lines = _materialize_value(func, value, src_type, _reg_name(src_type, 10))
                if src_type.width < dst_type.width:
                    src_reg = _reg_name(src_type, 10)
                    dst_reg = _reg_name(dst_type, 10)
                    if src_type.width <= 8:
                        lines = [f"  movzx {dst_reg}, {src_reg}"]
                    elif src_type.width <= 16:
                        lines = [f"  movzx {dst_reg}, {src_reg}"]
                    elif src_type.width <= 32 and dst_type.width > 32:
                        lines = []
            lines.extend(_store_reg_to_slot(_reg_name(dst_type, 10), func.value_slots[dest].offset, dst_type))
            return lines
        if op == "sext" and src_type.is_int and dst_type.is_int and src_type.width <= dst_type.width:
            lines = _materialize_value(func, value, src_type, _reg_name(src_type, 10))
            if src_type.width < dst_type.width:
                lines.extend(_sign_extend_reg_to_r10(src_type))
            lines.extend(_store_reg_to_slot(_reg_name(dst_type, 10), func.value_slots[dest].offset, dst_type))
            return lines
        if op == "trunc" and src_type.is_int and dst_type.is_int and src_type.width >= dst_type.width:
            lines = _materialize_value(func, value, src_type, _reg_name(src_type, 10))
            lines.extend(_store_reg_to_slot(_reg_name(dst_type, 10), func.value_slots[dest].offset, dst_type))
            return lines
        if op == "ptrtoint" and src_type.is_ptr and dst_type.is_int:
            lines = _materialize_value(func, value, src_type, "r10")
            dest_reg = _reg_name(dst_type, 10)
            if dest_reg != "r10" and dst_type.width > 64:
                lines.append(f"  mov {dest_reg}, r10")
            lines.extend(_store_reg_to_slot(dest_reg, func.value_slots[dest].offset, dst_type))
            return lines
        if op == "inttoptr" and src_type.is_int and dst_type.is_ptr:
            lines = _materialize_value(func, value, src_type, _reg_name(src_type, 10))
            if src_type.width <= 32:
                lines.append("  mov r10d, r10d")
            lines.extend(_store_reg_to_slot("r10", func.value_slots[dest].offset, dst_type))
            return lines
        if op == "bitcast":
            if src_type.is_ptr and dst_type.is_ptr:
                lines = _materialize_value(func, value, src_type, "r10")
                lines.extend(_store_reg_to_slot("r10", func.value_slots[dest].offset, dst_type))
                return lines
            if src_type.is_int and dst_type.is_int and src_type.width == dst_type.width:
                lines = _materialize_value(func, value, src_type, _reg_name(src_type, 10))
                lines.extend(_store_reg_to_slot(_reg_name(dst_type, 10), func.value_slots[dest].offset, dst_type))
                return lines
        if op == "sitofp" and src_type.is_int and dst_type.is_fp:
            src_reg = _reg_name(src_type, 10)
            dst_reg = _reg_name(dst_type, 10)
            lines = _materialize_value(func, value, src_type, src_reg)
            cvt_op = "cvtsi2ss" if dst_type.width <= 32 else "cvtsi2sd"
            lines.append(f"  {cvt_op} {dst_reg}, {src_reg}")
            lines.extend(_store_reg_to_slot(dst_reg, func.value_slots[dest].offset, dst_type))
            return lines
        if op == "fptrunc" and src_type.is_fp and dst_type.is_fp and src_type.width > dst_type.width:
            lines = _materialize_value(func, value, src_type, _reg_name(src_type, 10))
            lines.append("  cvtsd2ss xmm10, xmm10")
            lines.extend(_store_reg_to_slot(_reg_name(dst_type, 10), func.value_slots[dest].offset, dst_type))
            return lines
        if op == "fpext" and src_type.is_fp and dst_type.is_fp and src_type.width < dst_type.width:
            lines = _materialize_value(func, value, src_type, _reg_name(src_type, 10))
            lines.append("  cvtss2sd xmm10, xmm10")
            lines.extend(_store_reg_to_slot(_reg_name(dst_type, 10), func.value_slots[dest].offset, dst_type))
            return lines
        raise BackendUnavailable(
            f"x86_64 self backend cast {op!r} not translated yet in {func.name!r}: {src_type.describe()} -> {dst_type.describe()}"
        )

    if kind == "extractvalue":
        dest, aggregate_type, value, _indices, result_type, offset = data
        if dest not in func.value_slots:
            return []
        if is_aggregate_literal_value(value):
            literal_bytes = aggregate_literal_to_bytes(aggregate_type, value)
            field_bytes = literal_bytes[offset : offset + result_type.slot_size]
            field_value = str(int.from_bytes(field_bytes, byteorder="little", signed=False))
            lines = _materialize_value(func, field_value, result_type, _reg_name(result_type, 10))
            lines.extend(_store_reg_to_slot(_reg_name(result_type, 10), func.value_slots[dest].offset, result_type))
            return lines
        if value not in func.value_slots:
            raise BackendUnavailable(
                f"x86_64 self backend extractvalue source not translated yet in {func.name!r}: {value}"
            )
        lines = [f"  lea r11, {_slot_addr(func.value_slots[value].offset)}"]
        if offset:
            lines.extend(_emit_add_immediate_to_r11(offset))
        if result_type.is_array or result_type.is_struct:
            lines.append(f"  lea r10, {_slot_addr(func.value_slots[dest].offset)}")
            lines.extend(_copy_address_to_address("r11", "r10", result_type.slot_size))
            return lines
        lines.extend(_load_from_address("r11", _reg_name(result_type, 10), result_type))
        lines.extend(_store_reg_to_slot(_reg_name(result_type, 10), func.value_slots[dest].offset, result_type))
        return lines

    if kind == "gep":
        dest, base_type, _ptr_type, ptr_name, indices = data
        return _emit_gep_instruction(func, dest, base_type, ptr_name, indices)

    if kind == "call":
        dest, ret_type, callee, is_indirect, args, _fixed_arg_count, _is_vararg_call = data
        if callee is None or callee == "None":
            raise BackendUnavailable(
                f"x86_64 self backend unresolved direct callee not translated yet in {func.name!r}"
            )
        if not is_indirect and callee.startswith("llvm.memcpy."):
            return _emit_memcpy_intrinsic_call(func, args)
        if not is_indirect and callee.startswith(("llvm.smax.", "llvm.smin.", "llvm.umax.", "llvm.umin.")):
            if dest is None or dest not in func.value_slots:
                return []
            if len(args) != 2:
                raise BackendUnavailable(
                    f"x86_64 self backend {callee} intrinsic expects 2 args in {func.name!r}"
                )
            lhs_type, lhs = args[0]
            rhs_type, rhs = args[1]
            if lhs_type.describe() != rhs_type.describe() or lhs_type.describe() != ret_type.describe():
                raise BackendUnavailable(
                    f"x86_64 self backend {callee} intrinsic type mismatch in {func.name!r}"
                )
            if not lhs_type.is_int:
                raise BackendUnavailable(
                    f"x86_64 self backend {callee} intrinsic only supports integer results right now in {func.name!r}"
                )
            dst_reg = _reg_name(ret_type, 0)
            rhs_reg = _reg_name(ret_type, 10)
            lines = _materialize_value(func, lhs, lhs_type, dst_reg)
            lines.extend(_materialize_value(func, rhs, rhs_type, rhs_reg))
            lines.append(f"  cmp {dst_reg}, {rhs_reg}")
            cmov = {
                "llvm.smax.": "cmovl",
                "llvm.smin.": "cmovg",
                "llvm.umax.": "cmovb",
                "llvm.umin.": "cmova",
            }
            for prefix, op in cmov.items():
                if callee.startswith(prefix):
                    lines.append(f"  {op} {dst_reg}, {rhs_reg}")
                    lines.extend(_store_reg_to_slot(dst_reg, func.value_slots[dest].offset, ret_type))
                    return lines
            raise BackendUnavailable(
                f"x86_64 self backend intrinsic not translated yet in {func.name!r}: {callee}"
            )
        lines: list[str] = []
        arg_locations, stack_bytes, fp_reg_count = _iter_arg_locations([arg_type for arg_type, _value in args])
        call_stack_size = _align_to(stack_bytes, 16)
        if call_stack_size:
            lines.append(f"  sub rsp, {call_stack_size}")
        for (arg_type, value), (kind, payload) in zip(args, arg_locations, strict=False):
            if kind == "reg":
                lines.extend(_materialize_value(func, value, arg_type, str(payload)))
                continue
            if kind == "stack_scalar":
                stack_offset = int(payload)
                lines.extend(_materialize_value(func, value, arg_type, _reg_name(arg_type, 10)))
                lines.append(f"  mov {_mem_size(arg_type)} [rsp + {stack_offset}], {_reg_name(arg_type, 10)}")
                continue
            if kind == "stack_byval":
                stack_offset = int(payload)
                lines.append(f"  lea r11, [rsp + {stack_offset}]")
                if value == "zeroinitializer":
                    lines.extend(_zero_address("r11", arg_type.slot_size))
                    continue
                lines.extend(_materialize_aggregate_value_address(func, value, arg_type, "r10"))
                lines.extend(_copy_address_to_address("r10", "r11", arg_type.slot_size))
                continue
            raise BackendUnavailable(
                f"x86_64 self backend call arg location kind not translated yet in {func.name!r}: {kind}"
            )
        if _is_vararg_call:
            if fp_reg_count == 0:
                lines.append("  xor eax, eax")
            else:
                lines.append(f"  mov al, {fp_reg_count}")
        if is_indirect:
            callee_type = func.value_types.get(callee, TypeDesc("ptr", pointee=ret_type))
            if not callee_type.is_ptr:
                raise BackendUnavailable(
                    f"x86_64 self backend indirect callee is not pointer-typed in {func.name!r}: {callee}"
                )
            lines.extend(_materialize_value(func, callee, callee_type, "r11"))
            lines.append("  call r11")
        else:
            lines.append(f"  call {_asm_symbol(callee) if callee in _MODULE_SYMBOLS.defined_symbols else callee}")
        if call_stack_size:
            lines.append(f"  add rsp, {call_stack_size}")
        if dest is None or ret_type.is_void or dest not in func.value_slots:
            return lines
        if not (ret_type.is_int or ret_type.is_ptr or ret_type.is_fp):
            raise BackendUnavailable(
                f"x86_64 self backend call return type not translated yet in {func.name!r}: {ret_type.describe()}"
            )
        lines.extend(_store_reg_to_slot(_reg_name(ret_type, 0), func.value_slots[dest].offset, ret_type))
        return lines

    return None


def _emit_return_terminator(func: ParsedFunction, ret_type: TypeDesc, value: str) -> list[str]:
    if ret_type.is_void:
        return _emit_epilogue(func)
    if not (ret_type.is_int or ret_type.is_ptr or ret_type.is_fp):
        raise BackendUnavailable(
            f"x86_64 self backend return type not translated yet in {func.name!r}: {ret_type.describe()}"
        )
    lines = _materialize_value(func, value, ret_type, _reg_name(ret_type, 0))
    lines.extend(_emit_epilogue(func))
    return lines


def _emit_epilogue(func: ParsedFunction) -> list[str]:
    lines: list[str] = []
    if func.frame_size:
        lines.append(f"  add rsp, {func.frame_size}")
    lines.append("  pop rbp")
    lines.append("  ret")
    return lines


def _emit_phi_assignments(func: ParsedFunction, *, source_block: str, target_block: str) -> list[str]:
    target = func.block_map.get(target_block)
    if target is None:
        raise BackendUnavailable(
            f"x86_64 self backend branch targets unknown block {target_block!r} in {func.name!r}"
        )

    lines: list[str] = []
    for phi in target.phis:
        if phi.dest not in func.value_slots:
            continue
        match = next((incoming for incoming in phi.incoming if incoming.label == source_block), None)
        if match is None:
            raise BackendUnavailable(
                f"x86_64 self backend could not resolve phi incoming for {phi.dest!r} from {source_block!r}"
            )
        if not (phi.type.is_int or phi.type.is_ptr or phi.type.is_fp):
            raise BackendUnavailable(
                f"x86_64 self backend phi type not translated yet in {func.name!r}: {phi.type.describe()}"
            )
        reg = _reg_name(phi.type, 10)
        lines.extend(_materialize_value(func, match.value, phi.type, reg))
        lines.extend(_store_reg_to_slot(reg, func.value_slots[phi.dest].offset, phi.type))
    return lines


def _emit_branch_terminator(func: ParsedFunction, source_block: str, target: str) -> list[str]:
    lines = _emit_phi_assignments(func, source_block=source_block, target_block=target)
    lines.append(f"  jmp {_block_label(func.name, target)}")
    return lines


def _emit_cond_branch_terminator(
    func: ParsedFunction,
    source_block: str,
    cond_name: str,
    true_target: str,
    false_target: str,
) -> list[str]:
    true_edge = _edge_label(func.name, source_block, true_target)
    lines = _load_slot_to_reg(func.value_slots[cond_name].offset, "al", TypeDesc("int", 1))
    lines.extend(
        [
            "  test al, al",
            f"  jne {true_edge}",
        ]
    )
    lines.extend(_emit_phi_assignments(func, source_block=source_block, target_block=false_target))
    lines.append(f"  jmp {_block_label(func.name, false_target)}")
    lines.append("")
    lines.append(f"{true_edge}:")
    lines.extend(_emit_phi_assignments(func, source_block=source_block, target_block=true_target))
    lines.append(f"  jmp {_block_label(func.name, true_target)}")
    return lines


def _emit_switch_terminator(
    func: ParsedFunction,
    source_block: str,
    value_type: TypeDesc,
    value: str,
    default_target: str,
    cases: tuple[tuple[int, str], ...],
) -> list[str]:
    if not value_type.is_int:
        raise BackendUnavailable(
            f"x86_64 self backend switch value type not translated yet in {func.name!r}: {value_type.describe()}"
        )
    value_reg = _reg_name(value_type, 10)
    case_reg = _reg_name(value_type, 11)
    lines = _materialize_value(func, value, value_type, value_reg)
    edge_targets: list[str] = []
    for case_value, case_target in cases:
        lines.append(f"  mov {case_reg}, {case_value}")
        lines.append(f"  cmp {value_reg}, {case_reg}")
        lines.append(f"  je {_edge_label(func.name, source_block, case_target)}")
        if case_target not in edge_targets:
            edge_targets.append(case_target)
    lines.append(f"  jmp {_edge_label(func.name, source_block, default_target)}")
    if default_target not in edge_targets:
        edge_targets.append(default_target)
    for target in edge_targets:
        lines.append("")
        lines.append(f"{_edge_label(func.name, source_block, target)}:")
        lines.extend(_emit_phi_assignments(func, source_block=source_block, target_block=target))
        lines.append(f"  jmp {_block_label(func.name, target)}")
    return lines


def _emit_instruction(func: ParsedFunction, block, instr: ParsedInstr) -> list[str]:
    return emit_instruction_dispatch(
        func,
        block,
        instr,
        emit_memory=_emit_memory_instruction,
        emit_compute=_emit_compute_instruction,
    )


def _emit_terminator(func: ParsedFunction, block, term: ParsedInstr) -> list[str]:
    return emit_terminator_dispatch(
        func,
        block,
        term,
        emit_ret_void=_emit_epilogue,
        emit_ret=_emit_return_terminator,
        emit_br=_emit_branch_terminator,
        emit_br_cond=_emit_cond_branch_terminator,
        emit_switch=_emit_switch_terminator,
        emit_unreachable=_emit_unreachable_terminator,
    )


def _emit_unreachable_terminator() -> list[str]:
    return ["  ud2"]


def _aggregate_returned_direct(_ty) -> bool:
    return False


def _emit_function(func: ParsedFunction) -> list[str]:
    symbol = _asm_symbol(func.name)
    lines = _emit_prologue(func)
    lines.extend(
        emit_function_blocks(
            func,
            block_label=_block_label,
            emit_instruction=_emit_instruction,
            emit_terminator=_emit_terminator,
        )
    )
    lines.append(f".size {symbol}, .-{symbol}")
    return lines


def emit_x86_64_linux_asm(ir_text: str) -> str:
    global _MODULE_SYMBOLS
    prepared = prepare_module_for_target(
        ir_text,
        aggregate_returned_indirect=_aggregate_returned_direct,
    )
    triple = prepared.triple
    if not is_x86_64_linux_triple(triple):
        raise BackendUnavailable(
            f"self backend asm Linux slice only supports x86_64 Linux, got {triple!r}"
        )
    _MODULE_SYMBOLS = prepared.module_symbols
    lines = [".intel_syntax noprefix"]
    lines.extend(emit_globals(prepared.globals_, _MODULE_SYMBOLS))
    for func in prepared.functions:
        lines.extend(_emit_function(func))
    lines.append('.section .note.GNU-stack,"",@progbits')
    return "\n".join(lines) + "\n"
