from __future__ import annotations

from typing import Callable

from . import BackendUnavailable
from .self_backend_ir import ParsedBlock, ParsedFunction, ParsedInstr


def emit_instruction_dispatch(
    func: ParsedFunction,
    block: ParsedBlock,
    instr: ParsedInstr,
    *,
    emit_memory: Callable[[ParsedFunction, str, tuple], list[str] | None],
    emit_compute: Callable[[ParsedFunction, str, tuple], list[str] | None],
) -> list[str]:
    return emit_instruction_dispatch_parts(
        func,
        block,
        instr.kind,
        instr.data,
        emit_memory=emit_memory,
        emit_compute=emit_compute,
    )


def emit_instruction_dispatch_parts(
    func: ParsedFunction,
    block: ParsedBlock,
    kind: str,
    data: tuple,
    *,
    emit_memory: Callable[[ParsedFunction, str, tuple], list[str] | None],
    emit_compute: Callable[[ParsedFunction, str, tuple], list[str] | None],
) -> list[str]:

    memory_lines = emit_memory(func, kind, data)
    if memory_lines is not None:
        return memory_lines

    compute_lines = emit_compute(func, kind, data)
    if compute_lines is not None:
        return compute_lines

    raise BackendUnavailable(
        f"self backend hit unknown instruction kind in {func.name!r}/{block.name!r}: {kind}"
    )


__all__ = ["emit_instruction_dispatch", "emit_instruction_dispatch_parts"]
