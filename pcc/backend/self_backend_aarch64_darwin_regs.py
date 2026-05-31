from __future__ import annotations

"""AArch64 Darwin register/immediate helpers for the self backend."""

import struct

from . import BackendUnavailable
from .self_backend_ir import TypeDesc

_DIRECT_FP_IMMEDIATES = {
    1.0: "1.0",
    2.0: "2.0",
}


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
    raise BackendUnavailable("self backend ran out of scratch registers for stack address materialization")


def emit_const_to_reg(value_type: TypeDesc, reg: str, value: int) -> list[str]:
    if value_type.is_ptr:
        bits = 64
    elif value_type.is_int:
        bits = value_type.width if value_type.width <= 32 else 64
    else:
        bits = 32
    mask = (1 << bits) - 1
    unsigned = value & mask
    chunks = [((unsigned >> shift) & 0xFFFF) for shift in range(0, bits, 16)]
    first_index = 0
    while first_index < len(chunks) and chunks[first_index] == 0:
        first_index += 1
    if first_index == len(chunks):
        return [f"  movz {reg}, #0"]
    lines = [f"  movz {reg}, #{chunks[first_index]}, lsl #{first_index * 16}"]
    for index, chunk in enumerate(chunks):
        if index == first_index or chunk == 0:
            continue
        lines.append(f"  movk {reg}, #{chunk}, lsl #{index * 16}")
    return lines


def emit_fp_hex_constant(value_type: TypeDesc, reg: str, token: str) -> list[str]:
    bits = int(token, 16)
    if not value_type.is_fp:
        raise BackendUnavailable(f"self backend fp constant helper expects fp type, got {value_type.describe()}")
    if value_type.width <= 32:
        as_double = struct.unpack(">d", bits.to_bytes(8, byteorder="big", signed=False))[-1]
        immediate = _DIRECT_FP_IMMEDIATES.get(float(as_double))
        if immediate is not None:
            return [f"  fmov {reg}, #{immediate}"]
        fp_bits = struct.unpack(">I", struct.pack(">f", float(as_double)))[0]
        lines = emit_const_to_reg(TypeDesc("int", 32), "w12", fp_bits)
        lines.append(f"  fmov {reg}, w12")
        return lines
    immediate = _DIRECT_FP_IMMEDIATES.get(struct.unpack(">d", bits.to_bytes(8, byteorder="big", signed=False))[0])
    if immediate is not None:
        return [f"  fmov {reg}, #{immediate}"]
    lines = emit_const_to_reg(TypeDesc("int", 64), "x12", bits)
    lines.append(f"  fmov {reg}, x12")
    return lines


def emit_fp_constant(value_type: TypeDesc, reg: str, token: str) -> list[str]:
    if token.startswith("0x"):
        return emit_fp_hex_constant(value_type, reg, token)
    if not value_type.is_fp:
        raise BackendUnavailable(f"self backend fp constant helper expects fp type, got {value_type.describe()}")
    immediate = _DIRECT_FP_IMMEDIATES.get(float(token))
    if immediate is not None:
        return [f"  fmov {reg}, #{immediate}"]
    if value_type.width <= 32:
        bits = struct.unpack("<I", struct.pack("<f", float(token)))[0]
        lines = emit_const_to_reg(TypeDesc("int", 32), "w12", bits)
        lines.append(f"  fmov {reg}, w12")
        return lines
    bits = struct.unpack("<Q", struct.pack("<d", float(token)))[0]
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
        return [f"  mov {dest_reg}, {base_reg}"]
    op = "add" if offset > 0 else "sub"
    amount = abs(offset)
    if amount <= 4095:
        return [f"  {op} {dest_reg}, {base_reg}, #{amount}"]
    temp = scratch_reg or pick_scratch_gpr(dest_reg, base_reg)
    lines = emit_const_to_reg(TypeDesc("int", 64), temp, amount)
    lines.append(f"  {op} {dest_reg}, {base_reg}, {temp}")
    return lines


def emit_stack_adjust(offset: int) -> list[str]:
    return emit_add_offset("sp", "sp", offset, scratch_reg="x15")


def align_pow2(alignment: int) -> int:
    power = 0
    value = 1
    while value < max(1, alignment):
        power += 1
        value <<= 1
    return power
