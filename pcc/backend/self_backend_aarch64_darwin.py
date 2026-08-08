from __future__ import annotations

import os
import sys

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
from .code_profile import apply_function_order_profile
from .self_backend_aarch64_darwin_abi import (
    aggregate_returned_indirect as _aggregate_returned_indirect,
)
from .self_backend_aarch64_darwin_compute import (
    emit_compute_instruction as _compute_emit_instruction,
)
from .self_backend_aarch64_darwin_data import emit_globals as _emit_globals
from .self_backend_aarch64_darwin_flow import (
    plan_aarch64_canonical_error_fallthroughs,
)
from .self_backend_aarch64_darwin_memory import (
    emit_memory_instruction as _memory_emit_instruction,
)
from .self_backend_aarch64_darwin_symbols import (
    asm_symbol as _asm_symbol,
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
from .self_backend_prepare import PreparedSelfBackendModule, prepare_module_for_target
from .self_backend_precise_stackmaps import (
    FunctionStackMapPlan,
    build_stack_map_plans,
    render_aarch64_stack_map_section,
)
from .self_backend_target_passes import (
    AARCH64_MEMORY_PAIR_BARRIER_BEGIN,
    AARCH64_MEMORY_PAIR_BARRIER_END,
    pair_adjacent_aarch64_64bit_memory_ops,
    plan_aarch64_madd_fusions,
    run_self_target_memory_pass_pipeline,
)
from .self_backend_target_match import is_aarch64_darwin_triple
from .self_backend_terminator_dispatch import emit_terminator_dispatch

_MODULE_SYMBOLS = PreparedModuleSymbols(
    internal_prefix="",
    defined_symbols=frozenset(),
    internal_symbols=frozenset(),
)


def _function_symbol(name: str) -> str:
    """Resolve one function symbol without a non-self-hostable lambda."""
    return _asm_symbol(name, _MODULE_SYMBOLS)

# SDK mach-o/compact_unwind_encoding.h: standard FP/LR frame, no saved pairs.
_UNWIND_ARM64_MODE_FRAME = 0x04000000

_MEMORY_PAIR_BARRIER_KINDS = (
    "load_atomic",
    "store_atomic",
    "atomicrmw",
    "cmpxchg",
    "fence",
)


def _append_compact_unwind(
    lines: list[str],
    functions: list[ParsedFunction],
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    """Append one exact frame-mode compact-unwind row per function.

    Every function emitted by this backend immediately saves FP/LR and makes
    x29 the frame pointer.  Function sizes are measured from the final text,
    after the peephole passes, so removed instructions cannot leave stale
    unwind ranges.
    """
    if not functions:
        return lines

    symbols = [_asm_symbol(func.name, module_symbols) for func in functions]
    wanted = set(symbols)
    offsets: dict[str, int] = {}
    text_offset = 0
    in_text = False
    for raw in lines:
        line = raw.strip()
        if line.startswith(".section "):
            section = line[len(".section ") :].strip()
            in_text = section.startswith("__TEXT,__text,")
            continue
        if not in_text or not line:
            continue
        if line.endswith(":"):
            label = line[:-1].strip()
            if label in wanted:
                if label in offsets:
                    raise BackendUnavailable(
                        f"duplicate self-backend function label {label!r}"
                    )
                offsets[label] = text_offset
            continue
        if line.startswith("."):
            continue
        text_offset += 4

    missing = [symbol for symbol in symbols if symbol not in offsets]
    if missing:
        raise BackendUnavailable(
            "self backend could not locate function labels for compact "
            f"unwind: {missing!r}"
        )

    starts = sorted((offsets[symbol], symbol) for symbol in symbols)
    result = list(lines)
    result.append(".section __LD,__compact_unwind,regular,debug")
    result.append(".p2align 3")
    for index, (start, symbol) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else text_offset
        length = end - start
        if length <= 0 or length % 4 != 0 or length > 0xFFFFFFFF:
            raise BackendUnavailable(
                f"bad compact-unwind range for {symbol!r}: {start}..{end}"
            )
        result.append(f"  .quad {symbol}")
        result.append(f"  .long {length}")
        result.append(f"  .long {_UNWIND_ARM64_MODE_FRAME}")
        result.append("  .quad 0")
        result.append("  .quad 0")
    return result


def _emit_trace(message: str) -> None:
    """Coarse phase marker for diagnosing self-compiled backend failures.

    pcc1 raises runtime-generated exceptions with no message, so a failing
    stage inside the emitter is otherwise invisible: the only surface is
    `PCC-PY-COMPILE-001 ... exception_type=Exception`.  Gated on
    PCC_DEBUG_SELF_BACKEND_TRACE so it costs one env probe otherwise.
    """
    if os.environ.get("PCC_DEBUG_SELF_BACKEND_TRACE"):
        sys.stderr.write("[self.emit] " + message + "\n")


def emit_aarch64_darwin_asm(ir_text: str, optimize: bool = True) -> str:
    # ``ir_text`` is a borrowed function parameter.  The self-compiled path
    # forwards it through the prepare/parser stack.  Keep this wrapper short:
    # the prepared module crosses the next call as an owned return value, while
    # the large source string no longer shares a frame with every emit pass.
    owned_ir_text = ir_text + ""
    _emit_trace("prepare begin bytes=" + str(len(owned_ir_text)))
    prepared = prepare_module_for_target(
        owned_ir_text,
        aggregate_returned_indirect=_aggregate_returned_indirect,
    )
    _emit_trace("prepare end")
    return _emit_prepared_aarch64_darwin_module(
        prepared,
        optimize,
        profile_ir_text=owned_ir_text,
    )


def _emit_prepared_aarch64_darwin_module(
    prepared: PreparedSelfBackendModule,
    optimize: bool = True,
    *,
    profile_ir_text: str = "",
) -> str:
    global _MODULE_SYMBOLS
    triple = prepared.triple
    if triple != "unknown-unknown-unknown" and not is_aarch64_darwin_triple(triple):
        raise BackendUnavailable(
            f"self backend asm MVP only supports AArch64 Darwin, got {triple!r}"
        )
    if (
        str(os.environ.get("PCC_SELF_TARGET_PASS_TRANSPORT", "") or "").strip().lower()
        == "memory"
    ):
        prepared = run_self_target_memory_pass_pipeline(
            prepared,
            "self-aarch64-darwin-v0",
            raw_passes=None,
            raw_transport="memory",
        )

    globals_ = prepared.globals_
    _emit_trace("order profile begin")
    functions, _profile_decision = apply_function_order_profile(
        prepared.functions,
        ir_text=profile_ir_text,
        target=triple,
    )
    _MODULE_SYMBOLS = prepared.module_symbols
    _emit_trace("stack map plans begin funcs=" + str(len(functions)))
    stack_map_plans = {
        plan.function_name: plan
        for plan in build_stack_map_plans(
            functions,
            globals_,
            target="aarch64-darwin",
            function_symbol=_function_symbol,
        )
    }

    _emit_trace("stack map plans end")
    lines = _emit_globals(globals_, _MODULE_SYMBOLS)
    _emit_trace("globals end")
    cold_fallthrough_edges: list[tuple[str, str, str]] = []
    if functions:
        lines.append(".section __TEXT,__text,regular,pure_instructions")
        for func in functions:
            # Plan before the prologue invokes block-local allocation.  The
            # allocator extends the delayed multiply operands through the
            # consumer and discards any plan whose stack fallback was reused.
            _emit_trace("func begin " + str(func.name))
            plan_aarch64_madd_fusions(func, enabled=optimize)
            plan_aarch64_canonical_error_fallthroughs(
                func,
                enabled=optimize,
            )
            for source_block, error_block, success_block in (
                func.aarch64_cold_fallthrough_edges
            ):
                cold_fallthrough_edges.append(
                    (
                        _block_label(func.name, source_block),
                        _block_label(func.name, error_block),
                        _block_label(func.name, success_block),
                    )
                )
            lines.extend(_emit_function(func, stack_map_plans[func.name]))
    if optimize:
        lines = _forward_adjacent_stack_store_load(lines)
        lines = _forward_one_intervening_stack_store_load(lines)
        lines = _fold_zero_store_source(lines)
        lines = _fold_mov_store_source(lines)
        lines = _fold_zero_compare_immediate(lines)
        lines = _fold_mov_compare_source(lines)
        lines = _fold_mov_zero_branch_source(lines)
        lines = _fold_mov_arith_self_update(lines)
        lines = _fold_mov_mov_chain(lines)
        lines = _fold_zero_test_branch(lines)
        lines = _fold_forwarded_cset_branch(lines)
        lines = _fold_cset_zero_branch(lines)
        lines = _drop_dead_cset_branch_stores(lines)
        lines = _thread_trampoline_branches(lines)
        lines = _fold_cond_branch_to_fallthrough(
            lines,
            cold_fallthrough_edges,
        )
        lines = _drop_fallthrough_uncond_branches(lines)
        lines = _drop_unreferenced_empty_local_labels(lines)
    # Run after register allocation and every instruction-deleting peephole,
    # but before compact-unwind sizing.  Even with optimization disabled this
    # finalizer removes the source-semantics barrier pseudo-directives.
    lines = pair_adjacent_aarch64_64bit_memory_ops(lines, enabled=optimize)
    if functions:
        lines.extend(render_aarch64_stack_map_section(
            lines,
            tuple(stack_map_plans[func.name] for func in functions),
            function_symbol=_function_symbol,
            block_label=_block_label,
        ))
    lines = _append_compact_unwind(lines, functions, _MODULE_SYMBOLS)
    if lines:
        lines.append(".subsections_via_symbols")
    return "\n".join(lines) + "\n"


def _parse_stack_transfer(line: str, opcode: str) -> tuple[str, str] | None:
    prefix = f"  {opcode} "
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix) :]
    try:
        reg, addr = rest.split(", ", 1)
    except ValueError:
        return None
    if not addr.startswith("[x29, #-") or not addr.endswith("]"):
        return None
    return reg, addr[len("[x29, #-") : -1]


def _forward_move(dest_reg: str, src_reg: str) -> str | None:
    if dest_reg == src_reg:
        return ""
    if len(dest_reg) < 2 or len(src_reg) < 2 or dest_reg[0] != src_reg[0]:
        return None
    if dest_reg[0] in ("d", "s"):
        return f"  fmov {dest_reg}, {src_reg}"
    if dest_reg[0] in ("x", "w"):
        return f"  mov {dest_reg}, {src_reg}"
    return None


def _register_alias_key(reg: str) -> str:
    if len(reg) < 2:
        return reg
    prefix = reg[0]
    index = reg[1:]
    if prefix in ("w", "x"):
        return "gpr:" + index
    if prefix in ("s", "d"):
        return "fp:" + index
    return reg


def _forward_stack_load_move(
    load_opcode: str, dest_reg: str, src_reg: str
) -> str | None:
    if load_opcode == "ldurb":
        if not dest_reg.startswith("w") or not src_reg.startswith("w"):
            return None
        return f"  and {dest_reg}, {src_reg}, #0xff"
    return _forward_move(dest_reg, src_reg)


def _forward_adjacent_stack_store_load(lines: list[str]) -> list[str]:
    out: list[str] = []
    pairs = (("stur", "ldur"), ("sturb", "ldurb"))
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            for store_opcode, load_opcode in pairs:
                store = _parse_stack_transfer(lines[index], store_opcode)
                load = _parse_stack_transfer(lines[index + 1], load_opcode)
                if store is None or load is None:
                    continue
                store_reg, store_offset = store
                load_reg, load_offset = load
                move = _forward_stack_load_move(load_opcode, load_reg, store_reg)
                if store_offset == load_offset and move is not None:
                    out.append(lines[index])
                    if move:
                        out.append(move)
                    index += 2
                    break
            else:
                out.append(lines[index])
                index += 1
                continue
            continue
        out.append(lines[index])
        index += 1
    return out


def _parse_any_stack_load(line: str) -> tuple[str, str] | None:
    for opcode in ("ldur", "ldurb"):
        parsed = _parse_stack_transfer(line, opcode)
        if parsed is not None:
            return parsed
    return None


def _forward_one_intervening_stack_store_load(lines: list[str]) -> list[str]:
    out: list[str] = []
    pairs = (("stur", "ldur"), ("sturb", "ldurb"))
    index = 0
    while index < len(lines):
        if index + 2 < len(lines):
            middle_load = _parse_any_stack_load(lines[index + 1])
            if middle_load is not None:
                middle_reg, middle_offset = middle_load
                for store_opcode, load_opcode in pairs:
                    store = _parse_stack_transfer(lines[index], store_opcode)
                    load = _parse_stack_transfer(lines[index + 2], load_opcode)
                    if store is None or load is None:
                        continue
                    store_reg, store_offset = store
                    load_reg, load_offset = load
                    move = _forward_stack_load_move(load_opcode, load_reg, store_reg)
                    if (
                        store_offset == load_offset
                        and middle_offset != store_offset
                        and _register_alias_key(middle_reg)
                        != _register_alias_key(store_reg)
                        and move is not None
                    ):
                        out.append(lines[index])
                        out.append(lines[index + 1])
                        if move:
                            out.append(move)
                        index += 3
                        break
                else:
                    out.append(lines[index])
                    index += 1
                    continue
                continue
        out.append(lines[index])
        index += 1
    return out


def _parse_cset(line: str) -> tuple[str, str] | None:
    prefix = "  cset "
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix) :]
    reg, sep, cond = rest.partition(", ")
    if not sep or not reg.startswith("w"):
        return None
    return reg, cond


def _parse_and_byte_forward(line: str) -> tuple[str, str] | None:
    prefix = "  and "
    if not line.startswith(prefix) or not line.endswith(", #0xff"):
        return None
    rest = line[len(prefix) : -len(", #0xff")]
    try:
        dest_reg, src_reg = rest.split(", ", 1)
    except ValueError:
        return None
    if not dest_reg.startswith("w") or not src_reg.startswith("w"):
        return None
    return dest_reg, src_reg


def _parse_cond_zero_branch(line: str) -> tuple[str, str, str] | None:
    if line.startswith("  cbz "):
        opcode = "cbz"
        rest = line[len("  cbz ") :]
    elif line.startswith("  cbnz "):
        opcode = "cbnz"
        rest = line[len("  cbnz ") :]
    else:
        return None
    try:
        reg, target = rest.split(", ", 1)
    except ValueError:
        return None
    if not reg.startswith("w"):
        return None
    return opcode, reg, target


def _fold_forwarded_cset_branch(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 3 < len(lines):
            cset = _parse_cset(lines[index])
            store = _parse_stack_transfer(lines[index + 1], "sturb")
            forwarded = _parse_and_byte_forward(lines[index + 2])
            branch = _parse_cond_zero_branch(lines[index + 3])
            if (
                cset is not None
                and store is not None
                and forwarded is not None
                and branch is not None
            ):
                cset_reg, _cset_cond = cset
                store_reg, _store_offset = store
                forwarded_reg, forwarded_src = forwarded
                opcode, branch_reg, branch_target = branch
                if (
                    store_reg == cset_reg
                    and forwarded_src == cset_reg
                    and branch_reg == forwarded_reg
                ):
                    out.append(lines[index])
                    out.append(lines[index + 1])
                    out.append(f"  {opcode} {cset_reg}, {branch_target}")
                    index += 4
                    continue
        out.append(lines[index])
        index += 1
    return out


def _parse_zero_movz(line: str) -> str | None:
    prefix = "  movz "
    if not line.startswith(prefix):
        return None
    parts = line[len(prefix) :].split(", ")
    if len(parts) not in (2, 3):
        return None
    reg = parts[0]
    if not reg.startswith(("w", "x")):
        return None
    if parts[1] != "#0":
        return None
    if len(parts) == 3 and parts[2] != "lsl #0":
        return None
    return reg


def _is_aarch64_scratch_reg(reg: str) -> bool:
    if len(reg) < 2 or reg[0] not in ("w", "x"):
        return False
    try:
        index = int(reg[1:])
    except ValueError:
        return False
    return 9 <= index <= 15


def _zero_reg_for(reg: str) -> str | None:
    if reg.startswith("w"):
        return "wzr"
    if reg.startswith("x"):
        return "xzr"
    return None


def _parse_store_source(line: str) -> tuple[str, str, str] | None:
    stripped = line[2:] if line.startswith("  ") else ""
    for opcode in ("stur", "str", "sturb", "strb"):
        prefix = f"{opcode} "
        if not stripped.startswith(prefix):
            continue
        rest = stripped[len(prefix) :]
        try:
            reg, addr = rest.split(", ", 1)
        except ValueError:
            return None
        return opcode, reg, addr
    return None


def _replace_store_source(line: str, source_reg: str) -> str | None:
    parsed = _parse_store_source(line)
    if parsed is None:
        return None
    opcode, _old_reg, addr = parsed
    return f"  {opcode} {source_reg}, {addr}"


def _tokens_for_reg_scan(text: str) -> list[str]:
    cleaned = []
    for ch in text:
        if ch.isalnum() or ch == "_":
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return "".join(cleaned).split()


def _reg_aliases(reg: str) -> tuple[str, ...]:
    if len(reg) < 2 or reg[0] not in ("w", "x"):
        return (reg,)
    number = reg[1:]
    if not number.isdigit():
        return (reg,)
    if reg.startswith("w"):
        return (reg, "x" + number)
    return (reg, "w" + number)


def _line_defines_reg(line: str, reg: str) -> bool:
    if not line.startswith("  "):
        return False
    stripped = line[2:]
    if not stripped or stripped.startswith(("b ", "b.", "bl ", "cbz ", "cbnz ")):
        return False
    opcode, sep, rest = stripped.partition(" ")
    if not sep:
        return False
    if opcode in (
        "cmp",
        "fcmp",
        "ret",
        "stur",
        "str",
        # Release stores consume their first register operand just like the
        # ordinary store forms above.  Treating ``stlr w10, [x9]`` as a
        # definition of w10 lets the move/store peepholes delete the move
        # which produced w10, leaving the atomic store with a stale value.
        "stlr",
        "sturb",
        "strb",
        "stlrb",
        "sturh",
        "strh",
        "stlrh",
        "stp",
    ):
        return False
    dest, _sep, _tail = rest.partition(", ")
    return dest == reg


def _line_uses_reg(line: str, reg: str) -> bool:
    if not line.startswith("  "):
        return False
    stripped = line[2:]
    opcode, sep, rest = stripped.partition(" ")
    if not sep:
        return reg in _tokens_for_reg_scan(stripped)
    if _line_defines_reg(line, reg):
        _dest, sep2, tail = rest.partition(", ")
        return bool(sep2) and reg in _tokens_for_reg_scan(tail)
    return reg in _tokens_for_reg_scan(rest)


def _line_defines_reg_alias(line: str, reg: str) -> bool:
    return any(_line_defines_reg(line, alias) for alias in _reg_aliases(reg))


def _line_uses_reg_alias(line: str, reg: str) -> bool:
    return any(_line_uses_reg(line, alias) for alias in _reg_aliases(reg))


def _can_drop_zero_mov_after_store(
    lines: list[str], start_index: int, reg: str
) -> bool:
    index = start_index
    while index < len(lines):
        line = lines[index]
        if (
            _local_label_name(line) is not None
            or _is_function_label(line)
            or line.startswith(".")
            or line.startswith(("  b ", "  b.", "  bl ", "  cbz ", "  cbnz ", "  ret"))
        ):
            return False
        if _line_uses_reg_alias(line, reg):
            return False
        if _line_defines_reg_alias(line, reg):
            return True
        index += 1
    return True


def _fold_zero_store_source(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            zero_reg = _parse_zero_movz(lines[index])
            store = _parse_store_source(lines[index + 1])
            if zero_reg is not None and store is not None:
                _opcode, store_reg, _addr = store
                replacement_reg = _zero_reg_for(zero_reg)
                if (
                    replacement_reg is not None
                    and store_reg == zero_reg
                    and _is_aarch64_scratch_reg(zero_reg)
                    and _can_drop_zero_mov_after_store(lines, index + 2, zero_reg)
                ):
                    out.append(
                        _replace_store_source(lines[index + 1], replacement_reg)
                        or lines[index + 1]
                    )
                    index += 2
                    continue
        out.append(lines[index])
        index += 1
    return out


def _parse_reg_mov(line: str) -> tuple[str, str] | None:
    prefix = "  mov "
    if not line.startswith(prefix):
        return None
    try:
        dest, src = line[len(prefix) :].split(", ", 1)
    except ValueError:
        return None
    if not dest.startswith(("w", "x")) or not src.startswith(("w", "x")):
        return None
    if dest[0] != src[0] or dest == src:
        return None
    return dest, src


def _fold_mov_store_source(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            move = _parse_reg_mov(lines[index])
            store = _parse_store_source(lines[index + 1])
            if move is not None and store is not None:
                dest_reg, src_reg = move
                _opcode, store_reg, _addr = store
                if (
                    store_reg == dest_reg
                    and _is_aarch64_scratch_reg(dest_reg)
                    and _can_drop_zero_mov_after_store(lines, index + 2, dest_reg)
                ):
                    out.append(
                        _replace_store_source(lines[index + 1], src_reg)
                        or lines[index + 1]
                    )
                    index += 2
                    continue
        out.append(lines[index])
        index += 1
    return out


def _parse_cmp_reg(line: str) -> tuple[str, str] | None:
    prefix = "  cmp "
    if not line.startswith(prefix):
        return None
    try:
        lhs, rhs = line[len(prefix) :].split(", ", 1)
    except ValueError:
        return None
    if not lhs.startswith(("w", "x")) or not rhs.startswith(("w", "x")):
        return None
    if lhs[0] != rhs[0]:
        return None
    return lhs, rhs


def _fold_zero_compare_immediate(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            zero_reg = _parse_zero_movz(lines[index])
            cmp_regs = _parse_cmp_reg(lines[index + 1])
            if zero_reg is not None and cmp_regs is not None:
                lhs, rhs = cmp_regs
                # Folding away the `movz reg, #0` is only sound when nothing
                # after the compare still reads that register. A min/max
                # intrinsic emits `movz w10,#0; cmp w9,w10; csel w11,w9,w10,cc`
                # — dropping the movz here would leave the csel reading an
                # undefined w10. Mirror the liveness guard used by
                # _fold_mov_compare_source.
                if (
                    rhs == zero_reg
                    and lhs != zero_reg
                    and _can_drop_zero_mov_after_store(lines, index + 2, zero_reg)
                ):
                    out.append(f"  cmp {lhs}, #0")
                    index += 2
                    continue
        out.append(lines[index])
        index += 1
    return out


def _fold_mov_compare_source(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            move = _parse_reg_mov(lines[index])
            cmp_regs = _parse_cmp_reg(lines[index + 1])
            if move is not None and cmp_regs is not None:
                dest_reg, src_reg = move
                lhs, rhs = cmp_regs
                if (
                    _is_aarch64_scratch_reg(dest_reg)
                    and (lhs == dest_reg or rhs == dest_reg)
                    and _can_drop_zero_mov_after_store(lines, index + 2, dest_reg)
                ):
                    if lhs == dest_reg:
                        lhs = src_reg
                    if rhs == dest_reg:
                        rhs = src_reg
                    out.append(f"  cmp {lhs}, {rhs}")
                    index += 2
                    continue
        out.append(lines[index])
        index += 1
    return out


def _branch_false_path_does_not_use_reg(lines: list[str], start_index: int) -> bool:
    index = start_index
    while index < len(lines) and not lines[index]:
        index += 1
    if index >= len(lines):
        return True
    return lines[index].startswith(("  b ", "  ret"))


def _fold_mov_zero_branch_source(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            move = _parse_reg_mov(lines[index])
            branch = _parse_cond_zero_branch(lines[index + 1])
            if move is not None and branch is not None:
                dest_reg, src_reg = move
                opcode, branch_reg, target = branch
                if (
                    branch_reg == dest_reg
                    and dest_reg.startswith("w")
                    and src_reg.startswith("w")
                    and _is_aarch64_scratch_reg(dest_reg)
                    and _branch_false_path_does_not_use_reg(lines, index + 2)
                ):
                    out.append(f"  {opcode} {src_reg}, {target}")
                    index += 2
                    continue
        out.append(lines[index])
        index += 1
    return out


def _parse_three_operand_arith(line: str) -> tuple[str, str, str, str] | None:
    if not line.startswith("  "):
        return None
    stripped = line[2:]
    opcode, sep, rest = stripped.partition(" ")
    if not sep or opcode not in ("add", "sub"):
        return None
    try:
        dest, lhs, rhs = rest.split(", ", 2)
    except ValueError:
        return None
    if not dest.startswith(("w", "x")) or not lhs.startswith(("w", "x")):
        return None
    if dest[0] != lhs[0]:
        return None
    return opcode, dest, lhs, rhs


def _fold_mov_arith_self_update(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            move = _parse_reg_mov(lines[index])
            arith = _parse_three_operand_arith(lines[index + 1])
            if move is not None and arith is not None:
                scratch, src_reg = move
                opcode, dest, lhs, rhs = arith
                if (
                    dest == scratch
                    and lhs == scratch
                    and _is_aarch64_scratch_reg(scratch)
                    and scratch not in _tokens_for_reg_scan(rhs)
                ):
                    out.append(f"  {opcode} {dest}, {src_reg}, {rhs}")
                    index += 2
                    continue
        out.append(lines[index])
        index += 1
    return out


def _fold_mov_mov_chain(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            first = _parse_reg_mov(lines[index])
            second = _parse_reg_mov(lines[index + 1])
            if first is not None and second is not None:
                scratch, src_reg = first
                dst_reg, second_src = second
                if (
                    second_src == scratch
                    and _is_aarch64_scratch_reg(scratch)
                    and _can_drop_zero_mov_after_store(lines, index + 2, scratch)
                ):
                    replacement = _forward_move(dst_reg, src_reg)
                    if replacement is not None:
                        if replacement:
                            out.append(replacement)
                        index += 2
                        continue
        out.append(lines[index])
        index += 1
    return out


def _fold_zero_test_branch(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            zero_reg = _parse_zero_movz(lines[index])
            branch = _parse_cond_zero_branch(lines[index + 1])
            if zero_reg is not None and branch is not None:
                opcode, branch_reg, target = branch
                if branch_reg == zero_reg:
                    if opcode == "cbz":
                        out.append(f"  b {target}")
                    else:
                        out.append(lines[index])
                    index += 2
                    continue
        out.append(lines[index])
        index += 1
    return out


def _invert_aarch64_cc(cond: str) -> str | None:
    mapping = {
        "eq": "ne",
        "ne": "eq",
        "lt": "ge",
        "le": "gt",
        "gt": "le",
        "ge": "lt",
        "lo": "hs",
        "ls": "hi",
        "hi": "ls",
        "hs": "lo",
        "mi": "pl",
        "pl": "mi",
        "vs": "vc",
        "vc": "vs",
    }
    return mapping.get(cond)


def _fold_cset_zero_branch(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 3 < len(lines) and (
            lines[index].startswith("  cmp ") or lines[index].startswith("  fcmp ")
        ):
            cset = _parse_cset(lines[index + 1])
            store = _parse_stack_transfer(lines[index + 2], "sturb")
            branch = _parse_cond_zero_branch(lines[index + 3])
            if cset is not None and store is not None and branch is not None:
                cset_reg, cset_cond = cset
                store_reg, _store_offset = store
                opcode, branch_reg, branch_target = branch
                branch_cond = cset_cond
                if opcode == "cbz":
                    branch_cond = _invert_aarch64_cc(cset_cond) or ""
                if branch_cond and store_reg == cset_reg and branch_reg == cset_reg:
                    out.append(lines[index])
                    out.append(lines[index + 1])
                    out.append(lines[index + 2])
                    out.append(f"  b.{branch_cond} {branch_target}")
                    index += 4
                    continue
        out.append(lines[index])
        index += 1
    return out


def _parse_direct_cond_branch(line: str) -> tuple[str, str] | None:
    prefix = "  b."
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix) :]
    cond, sep, target = rest.partition(" ")
    if not sep or not cond or not target:
        return None
    return cond, target


def _parse_uncond_branch(line: str) -> str | None:
    prefix = "  b "
    if not line.startswith(prefix):
        return None
    target = line[len(prefix) :]
    if not target:
        return None
    return target


def _branch_target(line: str) -> str | None:
    zero_branch = _parse_cond_zero_branch(line)
    if zero_branch is not None:
        return zero_branch[2]
    target = _parse_uncond_branch(line)
    if target is not None:
        return target
    parsed_cond = _parse_direct_cond_branch(line)
    if parsed_cond is not None:
        _cond, target = parsed_cond
        return target
    return None


def _retarget_branch(line: str, target: str) -> str:
    target_text = str(target)
    zero_branch = _parse_cond_zero_branch(line)
    if zero_branch is not None:
        opcode_text = str(zero_branch[0])
        reg_text = str(zero_branch[1])
        return "  " + opcode_text + " " + reg_text + ", " + target_text
    if _parse_uncond_branch(line) is not None:
        return "  b " + target_text
    parsed_cond = _parse_direct_cond_branch(line)
    if parsed_cond is not None:
        cond_text = str(parsed_cond[0])
        return "  b." + cond_text + " " + target_text
    return line


def _is_function_label(line: str) -> bool:
    return bool(line) and not line.startswith((" ", ".", "L_")) and line.endswith(":")


def _drop_dead_cset_branch_stores_in_function(lines: list[str]) -> list[str]:
    loaded_offsets = {
        offset
        for line in lines
        for parsed in [_parse_stack_transfer(line, "ldurb")]
        if parsed is not None
        for _reg, offset in [parsed]
    }
    if not loaded_offsets:
        loaded_offsets = set()
    out: list[str] = []
    index = 0
    while index < len(lines):
        if index + 3 < len(lines) and (
            lines[index].startswith("  cmp ") or lines[index].startswith("  fcmp ")
        ):
            cset = _parse_cset(lines[index + 1])
            store = _parse_stack_transfer(lines[index + 2], "sturb")
            branch = _parse_direct_cond_branch(lines[index + 3])
            if cset is not None and store is not None and branch is not None:
                cset_reg, _cset_cond = cset
                store_reg, store_offset = store
                if store_reg == cset_reg and store_offset not in loaded_offsets:
                    out.append(lines[index])
                    out.append(lines[index + 3])
                    index += 4
                    continue
        out.append(lines[index])
        index += 1
    return out


def _drop_dead_cset_branch_stores(lines: list[str]) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    in_function = False

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        out.extend(_drop_dead_cset_branch_stores_in_function(current))
        current = []

    for line in lines:
        if _is_function_label(line):
            if in_function:
                flush_current()
            else:
                out.extend(current)
                current = []
                in_function = True
            current.append(line)
            continue
        current.append(line)

    if in_function:
        flush_current()
    else:
        out.extend(current)
    return out


def _local_label_name(line: str) -> str | None:
    if line.startswith("L_") and line.endswith(":"):
        return line[:-1]
    return None


def _trampoline_targets(lines: list[str]) -> dict[str, str]:
    targets: dict[str, str] = {}
    index = 0
    while index < len(lines):
        label = _local_label_name(lines[index])
        if label is None:
            index += 1
            continue
        # Target-final stack-map anchors can legitimately be followed by a
        # single branch, but they describe a safepoint PC rather than an IR
        # trampoline block.  Threading/removing them loses the metadata record.
        if label.startswith("L_pcc_smap"):
            index += 1
            continue
        body: list[str] = []
        j = index + 1
        while j < len(lines):
            nxt = lines[j]
            if _local_label_name(nxt) is not None or _is_function_label(nxt):
                break
            if nxt.startswith("."):
                break
            if nxt:
                body.append(nxt)
            j += 1
        if len(body) == 1:
            target = _parse_uncond_branch(body[0])
            if target is not None and target != label:
                targets[label] = target
        index += 1
    return targets


def _resolve_trampoline_target(
    target: str,
    trampolines: dict[str, str],
) -> str:
    seen: set[str] = set()
    current = target
    while current in trampolines and current not in seen:
        seen.add(current)
        current = trampolines[current]
    return current


def _thread_trampoline_branches(lines: list[str]) -> list[str]:
    trampolines = _trampoline_targets(lines)
    if not trampolines:
        return lines

    label_index: dict[str, int] = {}
    for index, line in enumerate(lines):
        label = _local_label_name(line)
        if label is not None:
            label_index[label] = index

    rewritten: list[str] = []
    for index, line in enumerate(lines):
        target = _branch_target(line)
        if target is None:
            rewritten.append(line)
            continue
        resolved = _resolve_trampoline_target(target, trampolines)
        if resolved == target:
            rewritten.append(line)
            continue
        # Range guard: cbz/cbnz reach +/-32KB (8192 instructions) and
        # b.cond +/-1MB; threading a short trampoline hop into a direct
        # far branch overflows the fixup in huge functions ("fixup value
        # out of range"). Line distance conservatively over-approximates
        # instruction distance (labels/directives are 0 bytes), so skip
        # the rewrite and keep the trampoline when the resolved target is
        # too far. Unconditional `b` reaches +/-128MB and never needs the
        # guard.
        limit = 0
        if _parse_cond_zero_branch(line) is not None:
            limit = 6000
        elif _parse_direct_cond_branch(line) is not None:
            limit = 200000
        if limit:
            resolved_index = label_index.get(resolved)
            if resolved_index is None or abs(resolved_index - index) > limit:
                rewritten.append(line)
                continue
        rewritten.append(_retarget_branch(line, resolved))

    referenced = {
        target
        for line in rewritten
        for target in [_branch_target(line)]
        if target is not None
    }
    out: list[str] = []
    index = 0
    while index < len(rewritten):
        label = _local_label_name(rewritten[index])
        if label is not None and label in trampolines and label not in referenced:
            j = index + 1
            while j < len(rewritten):
                nxt = rewritten[j]
                if _local_label_name(nxt) is not None or _is_function_label(nxt):
                    break
                if nxt.startswith("."):
                    break
                j += 1
            index = j
            continue
        out.append(rewritten[index])
        index += 1
    return out


def _drop_fallthrough_uncond_branches(lines: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        target = _parse_uncond_branch(lines[index])
        if target is None or not target.startswith("L_"):
            out.append(lines[index])
            index += 1
            continue
        j = index + 1
        while j < len(lines) and not lines[j]:
            j += 1
        if j < len(lines) and _local_label_name(lines[j]) == target:
            index += 1
            continue
        out.append(lines[index])
        index += 1
    return out


def _fold_cond_branch_to_fallthrough(
    lines: list[str],
    eligible_edges: list[tuple[str, str, str]],
) -> list[str]:
    """Invert only IR-proven canonical error checks around a next block.

    ``eligible_edges`` comes from
    ``plan_aarch64_canonical_error_fallthroughs``.  Keeping that proof input
    mandatory prevents this textual peephole from changing ordinary branches
    that happen to have the same two-instruction assembly shape.
    """
    label_index: dict[str, int] = {}
    for idx, line in enumerate(lines):
        label = _local_label_name(line)
        if label is not None:
            label_index[label] = idx
    out: list[str] = []
    index = 0
    current_error_target = ""
    current_success_target = ""
    while index < len(lines):
        local_label = _local_label_name(lines[index])
        if local_label is not None:
            # Stack-map anchors identify safepoint PCs inside an IR block;
            # they do not begin a new control-flow block and therefore must
            # not discard the proof attached to the containing block.
            if not local_label.startswith("L_pcc_smap"):
                current_error_target = ""
                current_success_target = ""
                for source_label, error_target, success_target in eligible_edges:
                    if local_label == source_label:
                        current_error_target = error_target
                        current_success_target = success_target
                        break
        elif _is_function_label(lines[index]):
            current_error_target = ""
            current_success_target = ""
        if current_success_target and index + 1 < len(lines):
            else_target = _parse_uncond_branch(lines[index + 1])
            direct_branch = _parse_direct_cond_branch(lines[index])
            zero_branch = _parse_cond_zero_branch(lines[index])
            if (
                else_target is not None
                and (direct_branch is not None or zero_branch is not None)
            ):
                then_target = ""
                replacement = ""
                range_limit = 0
                if direct_branch is not None:
                    cond, then_target = direct_branch
                    inverse = _invert_aarch64_cc(cond)
                    if inverse is not None:
                        replacement = f"  b.{inverse} {else_target}"
                        range_limit = 200000
                elif zero_branch is not None:
                    opcode, reg, then_target = zero_branch
                    inverse_opcode = "cbnz" if opcode == "cbz" else "cbz"
                    replacement = f"  {inverse_opcode} {reg}, {else_target}"
                    range_limit = 200000
                j = index + 2
                while j < len(lines) and not lines[j]:
                    j += 1
                # Range guard: this rewrites a +/-128MB `b` into an imm19
                # b.cond or cbz/cbnz (both +/-1MB).  Line distance
                # conservatively over-approximates instruction distance.
                else_index = label_index.get(else_target)
                else_in_range = (
                    else_index is not None
                    and range_limit > 0
                    and abs(else_index - index) <= range_limit
                )
                if (
                    replacement
                    and then_target.startswith("L_")
                    and else_target.startswith("L_")
                    and then_target == current_success_target
                    and else_target == current_error_target
                    and else_in_range
                    and j < len(lines)
                    and _local_label_name(lines[j]) == then_target
                ):
                    out.append(replacement)
                    index += 2
                    continue
        out.append(lines[index])
        index += 1
    return out


def _drop_unreferenced_empty_local_labels(lines: list[str]) -> list[str]:
    referenced = {
        target
        for line in lines
        for target in [_branch_target(line)]
        if target is not None
    }
    out: list[str] = []
    index = 0
    while index < len(lines):
        label = _local_label_name(lines[index])
        # Precise-stackmap labels are consumed after all peepholes when final
        # PCs are calculated; they intentionally have no branch reference.
        # Treat them as externally referenced metadata anchors even when two
        # safepoints collapse to the same target-final instruction boundary.
        is_stack_map_anchor = label is not None and label.startswith(
            "L_pcc_smap"
        )
        if (
            label is not None
            and label not in referenced
            and not is_stack_map_anchor
        ):
            j = index + 1
            while j < len(lines) and not lines[j]:
                j += 1
            if j < len(lines) and (
                _local_label_name(lines[j]) is not None or _is_function_label(lines[j])
            ):
                index += 1
                continue
        out.append(lines[index])
        index += 1
    return out


def _emit_function(
    func: ParsedFunction,
    stack_map_plan: FunctionStackMapPlan,
) -> list[str]:
    lines = _prologue_emit_function_prologue(func, _MODULE_SYMBOLS)
    blocks = func.aarch64_block_layout or func.blocks
    lines.extend(
        emit_function_blocks(
            func,
            block_label=_block_label,
            emit_instruction=_emit_instruction,
            emit_terminator=_emit_terminator,
            blocks=blocks,
            stack_map_plan=stack_map_plan,
        )
    )
    lines.append(stack_map_plan.end_label + ":")
    return lines


def _emit_instruction(
    func: ParsedFunction, block: ParsedBlock, instr: ParsedInstr
) -> list[str]:
    lines = emit_instruction_dispatch(
        func,
        block,
        instr,
        emit_memory=_emit_memory_with_symbols,
        emit_compute=_emit_compute_with_symbols,
    )
    if instr.is_volatile or instr.kind in _MEMORY_PAIR_BARRIER_KINDS:
        return [
            AARCH64_MEMORY_PAIR_BARRIER_BEGIN,
            *lines,
            AARCH64_MEMORY_PAIR_BARRIER_END,
        ]
    return lines


def _emit_memory_with_symbols(func: ParsedFunction, kind: str, data) -> list[str]:
    return _memory_emit_instruction(func, kind, data, _MODULE_SYMBOLS)


def _emit_compute_with_symbols(func: ParsedFunction, kind: str, data) -> list[str]:
    return _compute_emit_instruction(func, kind, data, _MODULE_SYMBOLS)


def _emit_return_with_symbols(
    func: ParsedFunction,
    ret_type,
    value,
) -> list[str]:
    return _rets_emit_return_terminator(
        func,
        ret_type=ret_type,
        value=value,
        module_symbols=_MODULE_SYMBOLS,
    )


def _emit_branch_with_symbols(
    func: ParsedFunction,
    source_block: str,
    target: str,
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


def _emit_terminator(
    func: ParsedFunction, block: ParsedBlock, term: ParsedInstr
) -> list[str]:
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
