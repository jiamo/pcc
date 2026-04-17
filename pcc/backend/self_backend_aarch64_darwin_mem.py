from __future__ import annotations

"""AArch64 Darwin memory/opcode helpers for the self backend."""

from . import BackendUnavailable
from .self_backend_ir import TypeDesc


def stack_load_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or value_type.is_fp or (value_type.is_int and value_type.width > 16):
        return "ldur"
    if value_type.is_int and value_type.width <= 8:
        return "ldurb"
    if value_type.is_int and value_type.width <= 16:
        return "ldurh"
    return "ldur"


def stack_store_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or value_type.is_fp or (value_type.is_int and value_type.width > 16):
        return "stur"
    if value_type.is_int and value_type.width <= 8:
        return "sturb"
    if value_type.is_int and value_type.width <= 16:
        return "sturh"
    return "stur"


def mem_load_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or (value_type.is_int and value_type.width > 32):
        return "ldr"
    if value_type.is_int and value_type.width <= 8:
        return "ldrb"
    if value_type.is_int and value_type.width <= 16:
        return "ldrh"
    return "ldr"


def mem_store_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or (value_type.is_int and value_type.width > 32):
        return "str"
    if value_type.is_int and value_type.width <= 8:
        return "strb"
    if value_type.is_int and value_type.width <= 16:
        return "strh"
    return "str"


def chunk_load_op(size: int, *, stack: bool) -> str:
    if size == 8:
        return "ldur" if stack else "ldr"
    if size == 4:
        return "ldur" if stack else "ldr"
    if size == 2:
        return "ldurh" if stack else "ldrh"
    if size == 1:
        return "ldurb" if stack else "ldrb"
    raise BackendUnavailable(f"self backend does not support aggregate chunk load size {size}")


def chunk_store_op(size: int, *, stack: bool) -> str:
    if size == 8:
        return "stur" if stack else "str"
    if size == 4:
        return "stur" if stack else "str"
    if size == 2:
        return "sturh" if stack else "strh"
    if size == 1:
        return "sturb" if stack else "strb"
    raise BackendUnavailable(f"self backend does not support aggregate chunk store size {size}")


def aggregate_copy_chunks(size: int) -> list[tuple[int, int]]:
    chunks: list[tuple[int, int]] = []
    offset = 0
    remaining = size
    for chunk_size in (8, 4, 2, 1):
        while remaining >= chunk_size:
            chunks.append((offset, chunk_size))
            offset += chunk_size
            remaining -= chunk_size
    return chunks
