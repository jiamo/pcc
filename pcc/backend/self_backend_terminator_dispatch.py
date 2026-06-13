from __future__ import annotations

from typing import Callable

from . import BackendUnavailable
from .self_backend_ir import ParsedBlock, ParsedFunction, ParsedInstr, TypeDesc


def emit_terminator_dispatch(
    func: ParsedFunction,
    block: ParsedBlock,
    term: ParsedInstr,
    *,
    emit_ret_void: Callable[[ParsedFunction], list[str]],
    emit_ret: Callable[[ParsedFunction, TypeDesc, str], list[str]],
    emit_br: Callable[[ParsedFunction, str, str], list[str]],
    emit_br_cond: Callable[[ParsedFunction, str, str, str, str], list[str]],
    emit_switch: Callable[[ParsedFunction, str, TypeDesc, str, str, tuple], list[str]],
    emit_unreachable: Callable[[], list[str]],
) -> list[str]:
    kind = term.kind
    data = term.data

    if kind == "ret_void":
        return emit_ret_void(func)

    if kind == "ret":
        ret_type, value = data
        return emit_ret(func, ret_type, value)

    if kind == "br":
        target = data[0]
        return emit_br(func, block.name, target)

    if kind == "br_cond":
        cond_name, true_target, false_target = data
        return emit_br_cond(func, block.name, cond_name, true_target, false_target)

    if kind == "switch":
        value_type, value, default_target, cases = data
        return emit_switch(func, block.name, value_type, value, default_target, cases)

    if kind == "unreachable":
        return emit_unreachable()

    raise BackendUnavailable(
        f"self backend hit unknown terminator kind in {func.name!r}/{block.name!r}: {kind}"
    )
