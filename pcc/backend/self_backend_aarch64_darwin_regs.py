from __future__ import annotations

"""AArch64 Darwin register/immediate helpers for the self backend."""

from . import BackendUnavailable
from .aarch64_fp_immediates import direct_fp_immediate_literal
from .self_backend_aarch64_darwin_mem import (
    emitted_addsub_immediate_line,
    emitted_addsub_register_line,
    emitted_move_register_line,
    emitted_movewide_instruction_line,
)
from .self_backend_float_bits import bits_to_float64, float32_to_bits, float64_to_bits
from .self_backend_aarch64_fragments import AArch64EmissionFragments
from .self_backend_ir import TypeDesc
from .self_backend_value_arena import CompilerInt2


def as_x_reg(reg: str) -> str:
    if reg.startswith("w"):
        return f"x{reg[1:]}"
    return reg


def pick_scratch_gpr(
    exclude_a: str | None = None,
    exclude_b: str | None = None,
) -> str:
    excluded = set()
    if exclude_a is not None:
        excluded.add(as_x_reg(exclude_a))
    if exclude_b is not None:
        excluded.add(as_x_reg(exclude_b))
    for reg in ("x15", "x14", "x13", "x12"):
        if reg not in excluded:
            return reg
    raise BackendUnavailable(
        "self backend ran out of scratch registers for stack address materialization"
    )


def emit_const_to_reg(value_type: TypeDesc, reg: str, value: int) -> list[str]:
    if value_type.is_ptr:
        bits = 64
    elif value_type.is_int:
        bits = value_type.width if value_type.width <= 32 else 64
    else:
        bits = 32
    return emit_const_to_reg_bits(bits, reg, value)


def emit_const_to_reg_bits(bits: int, reg: str, value: int) -> list[str]:
    # Arithmetic right shift plus a 16-bit mask produces the desired two's
    # complement chunks for negative values without constructing ``1 << 64``.
    # Keeping intermediates small is important because this emitter is also a
    # pcc-compiled program.
    chunks = []
    for shift in range(0, bits, 16):
        chunk_width = bits - shift
        if chunk_width > 16:
            chunk_width = 16
        chunk_mask = (1 << chunk_width) - 1
        chunks.append((value >> shift) & chunk_mask)
    first_index = 0
    while first_index < len(chunks) and chunks[first_index] == 0:
        first_index += 1
    if first_index == len(chunks):
        return [emitted_movewide_instruction_line("movz", reg, 0)]
    lines = [
        emitted_movewide_instruction_line(
            "movz",
            reg,
            chunks[first_index],
            first_index * 16,
            True,
        )
    ]
    for index, chunk in enumerate(chunks):
        if index == first_index or chunk == 0:
            continue
        lines.append(
            emitted_movewide_instruction_line(
                "movk",
                reg,
                chunk,
                index * 16,
                True,
            )
        )
    return lines


def append_const_to_reg(
    owner: AArch64EmissionFragments,
    fragment: CompilerInt2,
    is_pointer: bool,
    is_integer: bool,
    width: int,
    reg: str,
    value: int,
) -> None:
    if is_pointer:
        bits = 64
    elif is_integer:
        bits = width if width <= 32 else 64
    else:
        bits = 32
    append_const_to_reg_bits(owner, fragment, bits, reg, value)


def append_const_to_reg_bits(
    owner: AArch64EmissionFragments,
    fragment: CompilerInt2,
    bits: int,
    reg: str,
    value: int,
) -> None:
    """Publish immediate chunks directly without an intermediate sequence."""
    shift = 0
    emitted_first = False
    while shift < bits:
        chunk_width = bits - shift
        if chunk_width > 16:
            chunk_width = 16
        chunk = (value >> shift) & ((1 << chunk_width) - 1)
        if chunk:
            if emitted_first:
                owner.append_movewide(fragment, "movk", reg, chunk, shift)
            else:
                owner.append_movewide(fragment, "movz", reg, chunk, shift)
                emitted_first = True
        shift += 16
    if not emitted_first:
        owner.append_movewide(fragment, "movz", reg, 0)


def emit_fp_hex_constant(value_type: TypeDesc, reg: str, token: str) -> list[str]:
    bits = int(token, 16)
    if not value_type.is_fp:
        raise BackendUnavailable(
            f"self backend fp constant helper expects fp type, got {value_type.describe()}"
        )
    if value_type.width <= 32:
        as_double = bits_to_float64(bits)
        immediate = direct_fp_immediate_literal(float(as_double))
        if immediate is not None:
            return [f"  fmov {reg}, #{immediate}"]
        fp_bits = float32_to_bits(float(as_double))
        lines = emit_const_to_reg(TypeDesc("int", 32), "w12", fp_bits)
        lines.append(f"  fmov {reg}, w12")
        return lines
    immediate = direct_fp_immediate_literal(bits_to_float64(bits))
    if immediate is not None:
        return [f"  fmov {reg}, #{immediate}"]
    lines = emit_const_to_reg(TypeDesc("int", 64), "x12", bits)
    lines.append(f"  fmov {reg}, x12")
    return lines


def emit_fp_constant(value_type: TypeDesc, reg: str, token: str) -> list[str]:
    if token.startswith("0x"):
        return emit_fp_hex_constant(value_type, reg, token)
    if not value_type.is_fp:
        raise BackendUnavailable(
            f"self backend fp constant helper expects fp type, got {value_type.describe()}"
        )
    immediate = direct_fp_immediate_literal(float(token))
    if immediate is not None:
        return [f"  fmov {reg}, #{immediate}"]
    if value_type.width <= 32:
        bits = float32_to_bits(float(token))
        lines = emit_const_to_reg(TypeDesc("int", 32), "w12", bits)
        lines.append(f"  fmov {reg}, w12")
        return lines
    bits = float64_to_bits(float(token))
    lines = emit_const_to_reg(TypeDesc("int", 64), "x12", bits)
    lines.append(f"  fmov {reg}, x12")
    return lines


def emit_add_offset(
    dest_reg: str,
    base_reg: str,
    offset: int,
    *,
    scratch_reg: str | None = None,
) -> list[str]:
    if offset == 0:
        if dest_reg == base_reg:
            return []
        return [emitted_move_register_line(dest_reg, base_reg)]
    op = "add" if offset > 0 else "sub"
    amount = abs(offset)
    if amount <= 4095:
        return [
            emitted_addsub_immediate_line(
                op,
                dest_reg,
                base_reg,
                amount,
            )
        ]
    temp = scratch_reg or pick_scratch_gpr(dest_reg, base_reg)
    lines = emit_const_to_reg(TypeDesc("int", 64), temp, amount)
    lines.append(emitted_addsub_register_line(op, dest_reg, base_reg, temp))
    return lines


def append_add_offset(
    owner: AArch64EmissionFragments,
    fragment: CompilerInt2,
    dest_reg: str,
    base_reg: str,
    offset: int,
    *,
    scratch_reg: str | None = None,
) -> None:
    if offset == 0:
        if dest_reg != base_reg:
            owner.append_move(fragment, dest_reg, base_reg)
        return
    op = "add" if offset > 0 else "sub"
    amount = abs(offset)
    if amount <= 4095:
        owner.append_addsub_immediate(fragment, op, dest_reg, base_reg, amount)
        return
    temp = scratch_reg or pick_scratch_gpr(dest_reg, base_reg)
    append_const_to_reg(owner, fragment, False, True, 64, temp, amount)
    owner.append_addsub_register(fragment, op, dest_reg, base_reg, temp)


def emit_stack_adjust(offset: int) -> list[str]:
    return emit_add_offset("sp", "sp", offset, scratch_reg="x15")


def align_pow2(alignment: int) -> int:
    power = 0
    value = 1
    while value < max(1, alignment):
        power += 1
        value <<= 1
    return power
