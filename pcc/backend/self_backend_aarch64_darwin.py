from __future__ import annotations

"""Asm-first self backend bootstrap for AArch64 Darwin.

This backend consumes current LLVM IR text as a bootstrap input and lowers a
bounded but growing truthful subset to native AArch64 Darwin assembly.

Supported slice today:
- scalar integer types (`i1`, `i8`, `i16`, `i32`, `i64`)
- pointer scalars (`T*`, including pointer args/returns/local slots)
- `void` functions / calls / returns
- local `alloca`, `load`, `store`
- direct calls
- integer arithmetic / compares / branches / phi / simple loops
- scalar casts: `zext`, `sext`, `trunc`, `bitcast`, `ptrtoint`, `inttoptr`

Unsupported shapes still raise ``BackendUnavailable`` instead of guessing.
"""
from . import BackendUnavailable
from .self_backend_aarch64_darwin_abi import (
    aggregate_returned_indirect as _aggregate_returned_indirect,
)
from .self_backend_aarch64_darwin_compute import (
    emit_compute_instruction as _compute_emit_instruction,
)
from .self_backend_aarch64_darwin_data import emit_globals as _emit_globals
from .self_backend_aarch64_darwin_memory import (
    emit_memory_instruction as _memory_emit_instruction,
)
from .self_backend_aarch64_darwin_symbols import (
    block_label as _block_label,
)
from .self_backend_aarch64_darwin_prologue import (
    emit_function_prologue as _prologue_emit_function_prologue,
)
from .self_backend_aarch64_darwin_terminators import (
    emit_branch_terminator as _terms_emit_branch_terminator,
    emit_cond_branch_terminator as _terms_emit_cond_branch_terminator,
    emit_epilogue as _terms_emit_epilogue,
    emit_switch_terminator as _terms_emit_switch_terminator,
    emit_unreachable_terminator as _terms_emit_unreachable_terminator,
)
from .self_backend_aarch64_darwin_returns import (
    emit_return_terminator as _rets_emit_return_terminator,
)
from .self_backend_emit import emit_function_blocks
from .self_backend_instruction_dispatch import emit_instruction_dispatch
from .self_backend_ir import (
    ParsedFunction,
    ParsedInstr,
)
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_prepare import prepare_module_for_target
from .self_backend_target_match import is_aarch64_darwin_triple
from .self_backend_terminator_dispatch import emit_terminator_dispatch


_MODULE_SYMBOLS = PreparedModuleSymbols(
    internal_prefix="",
    defined_symbols=frozenset(),
    internal_symbols=frozenset(),
)


def emit_aarch64_darwin_asm(ir_text: str) -> str:
    global _MODULE_SYMBOLS
    prepared = prepare_module_for_target(
        ir_text,
        aggregate_returned_indirect=_aggregate_returned_indirect,
    )
    triple = prepared.triple
    if not is_aarch64_darwin_triple(triple):
        raise BackendUnavailable(
            f"self backend asm MVP only supports AArch64 Darwin, got {triple!r}"
        )

    globals_ = prepared.globals_
    functions = prepared.functions
    _MODULE_SYMBOLS = prepared.module_symbols

    lines = _emit_globals(globals_, _MODULE_SYMBOLS)
    if functions:
        lines.append(".section __TEXT,__text,regular,pure_instructions")
        for func in functions:
            lines.extend(_emit_function(func))
    if lines:
        lines.append(".subsections_via_symbols")
    return "\n".join(lines) + "\n"
def _emit_function(func: ParsedFunction) -> list[str]:
    lines = _prologue_emit_function_prologue(func, _MODULE_SYMBOLS)
    lines.extend(
        emit_function_blocks(
            func,
            block_label=_block_label,
            emit_instruction=_emit_instruction,
            emit_terminator=_emit_terminator,
        )
    )
    return lines


def _emit_instruction(func: ParsedFunction, block: ParsedBlock, instr: ParsedInstr) -> list[str]:
    return emit_instruction_dispatch(
        func,
        block,
        instr,
        emit_memory=_emit_memory_with_symbols,
        emit_compute=_emit_compute_with_symbols,
    )


def _emit_memory_with_symbols(func: ParsedFunction, kind: str, data) -> list[str]:
    return _memory_emit_instruction(func, kind, data, _MODULE_SYMBOLS)


def _emit_compute_with_symbols(func: ParsedFunction, kind: str, data) -> list[str]:
    return _compute_emit_instruction(func, kind, data, _MODULE_SYMBOLS)


def _emit_return_with_symbols(
    func: ParsedFunction, ret_type, value,
) -> list[str]:
    return _rets_emit_return_terminator(
        func,
        ret_type=ret_type,
        value=value,
        module_symbols=_MODULE_SYMBOLS,
    )


def _emit_branch_with_symbols(
    func: ParsedFunction, source_block: str, target: str,
) -> list[str]:
    return _terms_emit_branch_terminator(
        func,
        source_block=source_block,
        target=target,
        module_symbols=_MODULE_SYMBOLS,
    )


def _emit_cond_branch_with_symbols(
    func: ParsedFunction,
    block_name: str,
    cond_name: str,
    true_target: str,
    false_target: str,
) -> list[str]:
    return _terms_emit_cond_branch_terminator(
        func,
        block_name=block_name,
        cond_name=cond_name,
        true_target=true_target,
        false_target=false_target,
        module_symbols=_MODULE_SYMBOLS,
    )


def _emit_switch_with_symbols(
    func: ParsedFunction,
    block_name: str,
    value_type,
    value: str,
    default_target: str,
    cases,
) -> list[str]:
    return _terms_emit_switch_terminator(
        func,
        block_name=block_name,
        value_type=value_type,
        value=value,
        default_target=default_target,
        cases=cases,
        module_symbols=_MODULE_SYMBOLS,
    )


def _emit_terminator(func: ParsedFunction, block: ParsedBlock, term: ParsedInstr) -> list[str]:
    return emit_terminator_dispatch(
        func,
        block,
        term,
        emit_ret_void=_terms_emit_epilogue,
        emit_ret=_emit_return_with_symbols,
        emit_br=_emit_branch_with_symbols,
        emit_br_cond=_emit_cond_branch_with_symbols,
        emit_switch=_emit_switch_with_symbols,
        emit_unreachable=_terms_emit_unreachable_terminator,
    )
