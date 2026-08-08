from __future__ import annotations

from typing import Any, Callable

from .self_backend_ir import ParsedBlock, ParsedFunction, ParsedInstr


def emit_function_blocks(
    func: ParsedFunction,
    *,
    block_label: Callable[[str, str], str],
    emit_instruction: Callable[[ParsedFunction, ParsedBlock, ParsedInstr], list[str]],
    emit_terminator: Callable[[ParsedFunction, ParsedBlock, ParsedInstr], list[str]],
    blocks: list[ParsedBlock] | None = None,
    emit_block_prefix: Callable[[ParsedFunction, ParsedBlock], list[str]] | None = None,
    emit_instruction_suffix: Callable[
        [ParsedFunction, ParsedBlock, int, ParsedInstr], list[str]
    ] | None = None,
    emit_terminator_prefix: Callable[
        [ParsedFunction, ParsedBlock, ParsedInstr], list[str]
    ] | None = None,
    stack_map_plan: Any = None,
) -> list[str]:
    lines: list[str] = []
    entry_index: dict = {}
    suffix_index: dict = {}
    term_index: dict = {}
    if stack_map_plan is not None:
        # Per-call plan lookups scan every label; indexing once keeps huge
        # generated module tops (72k blocks) linear instead of quadratic.
        entry_index, suffix_index, term_index = stack_map_plan.build_line_index()
    emitted_blocks = func.blocks if blocks is None else blocks
    for index, block in enumerate(emitted_blocks):
        if index == 0:
            lines.append(block_label(func.name, block.name) + ":")
        else:
            lines.append("")
            lines.append(block_label(func.name, block.name) + ":")
        if emit_block_prefix is not None:
            lines.extend(emit_block_prefix(func, block))
        elif stack_map_plan is not None and block.name in entry_index:
            lines.extend(entry_index[block.name])
        block_suffix_index = None
        if stack_map_plan is not None and block.name in suffix_index:
            block_suffix_index = suffix_index[block.name]
        for instruction_index, instr in enumerate(block.instructions):
            lines.extend(emit_instruction(func, block, instr))
            if emit_instruction_suffix is not None:
                lines.extend(
                    emit_instruction_suffix(
                        func, block, instruction_index, instr
                    )
                )
            elif (
                block_suffix_index is not None
                and instruction_index in block_suffix_index
            ):
                lines.extend(block_suffix_index[instruction_index])
        assert block.terminator is not None
        if emit_terminator_prefix is not None:
            lines.extend(emit_terminator_prefix(func, block, block.terminator))
        elif stack_map_plan is not None and block.name in term_index:
            lines.extend(term_index[block.name])
        lines.extend(emit_terminator(func, block, block.terminator))
    return lines
