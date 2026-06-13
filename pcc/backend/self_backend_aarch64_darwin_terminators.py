from __future__ import annotations

from .self_backend_aarch64_darwin_abi import reg_name
from .self_backend_aarch64_darwin_branch_protection import (
    branch_protection_enabled,
    epilogue_authenticate_and_return,
)
from .self_backend_aarch64_darwin_flow import emit_phi_assignments
from .self_backend_aarch64_darwin_materialize import materialize_value
from .self_backend_aarch64_darwin_regs import emit_const_to_reg, emit_stack_adjust
from .self_backend_aarch64_darwin_symbols import block_edge_label, block_label
from .self_backend_ir import I1, ParsedFunction, TypeDesc
from .self_backend_module_symbols import PreparedModuleSymbols


def emit_epilogue(func: ParsedFunction) -> list[str]:
    lines: list[str] = []
    if func.frame_size:
        lines.extend(emit_stack_adjust(func.frame_size))
    lines.append("  ldp x29, x30, [sp], #16")
    # Reverse-edge protection: authenticate the signed LR before returning. When
    # branch protection is on this emits ``autiasp`` + ``ret`` (SP is the same
    # modifier used by the prologue ``paciasp`` because the frame save/restore is
    # symmetric). With protection off it is a plain ``ret``.
    if branch_protection_enabled():
        lines.extend(epilogue_authenticate_and_return(func))
    else:
        lines.append("  ret")
    return lines


def emit_branch_terminator(
    func: ParsedFunction,
    *,
    source_block: str,
    target: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    lines = emit_phi_assignments(
        func,
        source_block=source_block,
        target_block=target,
        module_symbols=module_symbols,
    )
    lines.append(f"  b {block_label(func.name, target)}")
    return lines


def emit_cond_branch_terminator(
    func: ParsedFunction,
    *,
    block_name: str,
    cond_name: str,
    true_target: str,
    false_target: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    false_prep = block_edge_label(func.name, block_name, false_target)
    lines = materialize_value(func, cond_name, I1, 9, module_symbols)
    lines.append("  cbz w9, " + false_prep)
    lines.extend(
        emit_phi_assignments(
            func,
            source_block=block_name,
            target_block=true_target,
            module_symbols=module_symbols,
        )
    )
    lines.append(f"  b {block_label(func.name, true_target)}")
    lines.append(f"{false_prep}:")
    lines.extend(
        emit_phi_assignments(
            func,
            source_block=block_name,
            target_block=false_target,
            module_symbols=module_symbols,
        )
    )
    lines.append(f"  b {block_label(func.name, false_target)}")
    return lines


def emit_switch_terminator(
    func: ParsedFunction,
    *,
    block_name: str,
    value_type: TypeDesc,
    value: str,
    default_target: str,
    cases: tuple[tuple[int, str], ...],
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    value_reg = reg_name(value_type, 9)
    case_reg = reg_name(value_type, 10)
    lines = materialize_value(func, value, value_type, 9, module_symbols)
    edge_targets: list[str] = []
    for case_value, case_target in cases:
        lines.extend(emit_const_to_reg(value_type, case_reg, case_value))
        lines.append(f"  cmp {value_reg}, {case_reg}")
        lines.append(f"  b.eq {block_edge_label(func.name, block_name, case_target)}")
        if case_target not in edge_targets:
            edge_targets.append(case_target)
    lines.append(f"  b {block_edge_label(func.name, block_name, default_target)}")
    if default_target not in edge_targets:
        edge_targets.append(default_target)
    for target in edge_targets:
        edge_label = block_edge_label(func.name, block_name, target)
        lines.append(f"{edge_label}:")
        lines.extend(
            emit_phi_assignments(
                func,
                source_block=block_name,
                target_block=target,
                module_symbols=module_symbols,
            )
        )
        lines.append(f"  b {block_label(func.name, target)}")
    return lines


def emit_unreachable_terminator() -> list[str]:
    return ["  brk #0"]
