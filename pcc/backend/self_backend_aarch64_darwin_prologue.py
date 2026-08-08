from __future__ import annotations

from .self_backend_aarch64_darwin_abi import (
    aggregate_passed_indirect,
    assign_abi_arg_regs,
    stack_arg_offsets,
)
from .self_backend_aarch64_darwin_branch_protection import (
    branch_protection_enabled,
    prologue_sign_return_address,
)
from .self_backend_aarch64_darwin_calls import emit_fixed_stack_arg_load
from .self_backend_aarch64_darwin_regalloc import allocate_aarch64_block_registers
from .self_backend_aarch64_darwin_regs import emit_stack_adjust
from .self_backend_aarch64_darwin_slots import copy_address_to_slot, store_reg_to_slot, store_value_regs_to_slot
from .self_backend_aarch64_darwin_symbols import asm_symbol
from .self_backend_ir import ParsedFunction, SlotInfo
from .self_backend_module_symbols import PreparedModuleSymbols


def emit_function_prologue(
    func: ParsedFunction,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    # Target-specific preparation belongs immediately before target emission.
    # The allocator is conservative and leaves every preassigned stack slot in
    # place, so an unsupported shape remains byte-for-byte on the spill path.
    allocate_aarch64_block_registers(func)
    symbol = asm_symbol(func.name, module_symbols)
    lines = ["", ".p2align 2"]
    if func.is_global:
        lines.append(f".globl {symbol}")
    lines.append(f"{symbol}:")
    # AArch64 branch protection (pac-ret + BTI). ``paciasp`` signs LR (x30) with
    # SP as the modifier *before* the frame save stores it, and doubles as a BTI
    # ``c`` landing pad for ``bl``/``blr`` callers. The matching ``autiasp`` is
    # emitted in the epilogue after LR is reloaded. See
    # ``self_backend_aarch64_darwin_branch_protection`` for the S-track rationale
    # (self backend must not depend on the LLVM path for CFI hardening).
    if branch_protection_enabled():
        lines.extend(prologue_sign_return_address(func))
    lines.extend(
        [
            "  stp x29, x30, [sp, #-16]!",
            "  mov x29, sp",
        ]
    )
    if func.frame_size:
        lines.extend(emit_stack_adjust(-func.frame_size))
    if func.hidden_sret_slot is not None:
        lines.extend(store_reg_to_slot("x8", func.hidden_sret_slot))

    arg_types = [arg.type for arg in func.args]
    arg_regs = assign_abi_arg_regs(arg_types)
    stack_offsets = stack_arg_offsets(arg_types, arg_regs)
    for arg, regs, stack_offset in zip(func.args, arg_regs, stack_offsets):
        if arg.name not in func.value_slots:
            continue
        if not regs:
            assert stack_offset is not None
            lines.extend(emit_fixed_stack_arg_load(func, arg, stack_offset))
            continue
        if aggregate_passed_indirect(arg.type):
            lines.extend(copy_address_to_slot(regs[0], func.value_slots[arg.name]))
            continue
        lines.extend(store_value_regs_to_slot(func.value_slots[arg.name], int(regs[0][1:])))
    return lines
