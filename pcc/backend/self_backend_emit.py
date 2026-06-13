from __future__ import annotations

from typing import Callable

from .self_backend_ir import ParsedBlock, ParsedFunction, ParsedInstr


def emit_function_blocks(
    func: ParsedFunction,
    *,
    block_label: Callable[[str, str], str],
    emit_instruction: Callable[[ParsedFunction, ParsedBlock, ParsedInstr], list[str]],
    emit_terminator: Callable[[ParsedFunction, ParsedBlock, ParsedInstr], list[str]],
) -> list[str]:
    lines: list[str] = []
    for index, block in enumerate(func.blocks):
        if index == 0:
            lines.append(block_label(func.name, block.name) + ":")
        else:
            lines.append("")
            lines.append(block_label(func.name, block.name) + ":")
        for instr in block.instructions:
            lines.extend(emit_instruction(func, block, instr))
        assert block.terminator is not None
        lines.extend(emit_terminator(func, block, block.terminator))
    return lines
