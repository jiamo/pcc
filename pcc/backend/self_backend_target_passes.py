from __future__ import annotations

"""Target-specific pass hook for the self backend.

LLVM keeps target-independent IR passes and target/codegen passes as separate
pipelines.  The local reference points are LLVM 20.1.8's
``llvm/IR/PassManager.h`` for module/function IR passes and
``llvm/Passes/CodeGenPassBuilder.h`` / ``llvm/CodeGen/TargetPassConfig.h`` for
machine/codegen passes.

This module owns the PCC-side target-pass hooks.  The general pipeline still
starts at the assembly-text boundary, while narrowly proven target combines
may attach pre-emission plans to the prepared function model when textual asm
no longer carries enough SSA/use information to prove them safely.
"""

from dataclasses import dataclass
import os
from typing import Protocol

from . import BackendUnavailable
from .self_backend_analysis import instruction_used_values, terminator_used_values
from .self_backend_ir import (
    PARSED_INSTRUCTION_KINDS,
    ParsedFunction,
    ParsedInstr,
    _dot_numeric_text_key_id,
    parsed_function_value_slot_offset,
    text_key_names_equal,
)
from .self_backend_kernel import TYPE_KIND_INT, get_indexed_function_kernel
from .self_backend_value_arena import CompilerInt4


PCC_SELF_TARGET_PASSES_ENV = "PCC_SELF_TARGET_PASSES"
PCC_SELF_TARGET_PASS_TRANSPORT_ENV = "PCC_SELF_TARGET_PASS_TRANSPORT"

_TRANSPORT_TEXT = "text"
_TRANSPORT_MEMORY = "memory"

# These private pseudo-directives live only in the AArch64 emitter's mutable
# line list.  They carry source-level memory semantics (volatile/atomic) across
# the current asm-text target-pass boundary and are removed before assembly is
# returned to a caller.  A plain ``ldr``/``str`` is otherwise insufficient to
# distinguish a relaxed atomic access from an ordinary access.
AARCH64_MEMORY_PAIR_BARRIER_BEGIN = ".pcc_memory_pair_barrier_begin"
AARCH64_MEMORY_PAIR_BARRIER_END = ".pcc_memory_pair_barrier_end"

_AARCH64_LDP_STP_MIN_OFFSET = -64 * 8
_AARCH64_LDP_STP_MAX_OFFSET = 63 * 8

_AARCH64_MADD_BARRIER_KINDS = (
    "load_atomic",
    "store_atomic",
    "atomicrmw",
    "cmpxchg",
    "fence",
    "call",
    "syscall6",
    "va_arg",
)


@dataclass(frozen=True)
class AArch64MaddFusion:
    """A proven block-local i64 multiply/add or multiply/subtract combine."""

    block_name: str
    producer_index: int
    consumer_index: int
    product: str
    result: str
    mnemonic: str
    mul_lhs: str
    mul_rhs: str
    accumulator: str


def _aarch64_madd_instruction_is_barrier(instr: ParsedInstr) -> bool:
    return instr.is_volatile or instr.kind in _AARCH64_MADD_BARRIER_KINDS


def _aarch64_madd_instruction_parts_are_barrier(
    kind: str, is_volatile: bool
) -> bool:
    return is_volatile or kind in _AARCH64_MADD_BARRIER_KINDS


def _aarch64_madd_operand_is_simple(value: str) -> bool:
    # Nested constant expressions borrow additional scratch registers while
    # materializing.  The fused emitter has three simultaneously-live inputs,
    # so keep that recursive shape on the ordinary two-instruction path.
    return not value.startswith("cexpr:")


def _aarch64_use_count_key(value_name: str):
    """Canonical key whose dict equality IS `text_key_names_equal`.

    That predicate holds exactly when the spellings are identical or both are
    dot-numeric with the same id, so keying dot-numeric names on the id itself
    makes an ordinary dict lookup exact and removes the recovery scan.  The
    scan it replaces was the dominant cost of a large emit: on one 10.4 MB
    module `_aarch64_increment_value_use_count` was called 124268 times and
    walked the whole growing dict on every first-seen name -- 65825805
    `text_key_names_equal` calls and 100972224 `_dot_numeric_text_key_id`
    calls, 69.8 s of a 120 s profile, i.e. O(distinct values squared).

    Int and str keys cannot collide in one dict, and this also collapses
    zero-padded spellings ("%.05" and "%.5"), which the ordered scan resolved
    only by whichever entry it happened to reach first.
    """
    numeric_id = _dot_numeric_text_key_id(value_name)
    if numeric_id >= 0:
        return numeric_id
    return value_name


def _aarch64_increment_value_use_count(
    counts: dict,
    value_name: str,
) -> None:
    key = _aarch64_use_count_key(value_name)
    # `in` + subscript, never `.get`: this module is compiled into the
    # self-host closure, where `dict.get` mis-lowers.
    if key in counts:
        counts[key] = counts[key] + 1
    else:
        counts[key] = 1


def _aarch64_collect_value_use_counts(func: ParsedFunction) -> dict[str, int]:
    """Collect one function's uses with equality recovery for false hash misses."""

    counts: dict = {}
    for block in func.blocks:
        for phi in block.phis:
            for incoming in phi.incoming:
                _aarch64_increment_value_use_count(counts, incoming.value)
        for instr in block.instructions:
            for used_value in instruction_used_values(instr):
                _aarch64_increment_value_use_count(counts, used_value)
        if block.terminator is not None:
            for used_value in terminator_used_values(block.terminator):
                _aarch64_increment_value_use_count(counts, used_value)
    return counts


def _aarch64_collect_indexed_value_use_counts(
    func: ParsedFunction,
) -> list[int]:
    kernel = get_indexed_function_kernel(func)
    counts = [0] * len(kernel.value_names)
    use_index = 0
    while use_index < len(kernel.used_value_ids):
        value_id = kernel.used_value_ids.get_unchecked(use_index)
        counts[value_id] += 1
        use_index += 1
    return counts


def _aarch64_value_use_count(
    counts: dict,
    value_name: str,
) -> int:
    key = _aarch64_use_count_key(value_name)
    if key in counts:
        return counts[key]
    return 0


def _aarch64_madd_consumer_shape(
    instr: ParsedInstr,
    product: str,
) -> tuple[str, str, str] | None:
    if instr.kind != "binop" or instr.arithmetic_flags:
        return None
    op, result, value_type, lhs, rhs = instr.data
    if not (value_type.is_int and value_type.width == 64):
        return None
    lhs_is_product = text_key_names_equal(lhs, product)
    rhs_is_product = text_key_names_equal(rhs, product)
    if op == "add" and lhs_is_product != rhs_is_product:
        accumulator = rhs if lhs_is_product else lhs
        return "madd", result, accumulator
    # AArch64 MSUB computes accumulator - lhs*rhs.  The opposite source form
    # (product - accumulator) is not this instruction and stays unfused.
    if op == "sub" and rhs_is_product and not lhs_is_product:
        return "msub", result, lhs
    return None


def _aarch64_madd_consumer_shape_parts(
    kind: str,
    data: tuple,
    has_arithmetic_flags: bool,
    product: str,
) -> tuple[str, str, str] | None:
    if kind != "binop" or has_arithmetic_flags:
        return None
    op, result, value_type, lhs, rhs = data
    if not (value_type.is_int and value_type.width == 64):
        return None
    lhs_is_product = text_key_names_equal(lhs, product)
    rhs_is_product = text_key_names_equal(rhs, product)
    if op == "add" and lhs_is_product != rhs_is_product:
        accumulator = rhs if lhs_is_product else lhs
        return "madd", result, accumulator
    if op == "sub" and rhs_is_product and not lhs_is_product:
        return "msub", result, lhs
    return None


def _aarch64_madd_consumer_is_planned(
    fusions: list[AArch64MaddFusion],
    block_name: str,
    consumer_index: int,
) -> bool:
    for fusion in fusions:
        if (
            text_key_names_equal(fusion.block_name, block_name)
            and fusion.consumer_index == consumer_index
        ):
            return True
    return False


def plan_aarch64_madd_fusions(
    func: ParsedFunction,
    *,
    enabled: bool = True,
) -> None:
    """Plan conservative IR-aware MADD/MSUB combines for one function.

    Only a plain i64 ``mul`` whose result has exactly one use qualifies.  The
    consumer must be a later plain i64 add, or accumulator-minus-product sub,
    in the same PHI-free block.  Calls, volatile/atomic operations, fences,
    exclusive-loop expansions, and recursive constant expressions are hard
    barriers.  Register/slot availability is validated by the allocator before
    emission; a plan that cannot keep all delayed operands alive is discarded.
    """

    func.aarch64_madd_fusions = []
    if not enabled or func.is_vararg:
        return

    kernel = get_indexed_function_kernel(func)
    fusions: list[AArch64MaddFusion] = []
    use_counts = _aarch64_collect_indexed_value_use_counts(func)
    for block_id in range(len(kernel.block_names)):
        block_name = kernel.block_names[block_id]
        block_fact: CompilerInt4 = kernel.block_fact(block_id)
        block_phi_fact: CompilerInt2 = kernel.block_phi_fact(block_id)
        if block_phi_fact.second:
            continue
        producer_index = 0
        instruction_count = block_fact.second
        while producer_index < instruction_count:
            instruction_id = block_fact.first + producer_index
            metadata: CompilerInt4 = kernel.instruction_metadata_by_id(
                instruction_id
            )
            kind = PARSED_INSTRUCTION_KINDS[metadata.first]
            has_arithmetic_flags = bool(metadata.fourth)
            if kind != "binop" or has_arithmetic_flags:
                producer_index += 1
                continue
            producer_record: CompilerInt4 = kernel.instruction_record(
                metadata.second
            )
            op = kernel.call_texts[producer_record.first]
            producer_fact: CompilerInt4 = kernel.instruction_fact_by_id(
                instruction_id
            )
            product_id = producer_fact.first
            product = (
                "" if product_id < 0 else kernel.value_name(product_id)
            )
            value_type: CompilerInt4 = kernel.type_header(
                producer_record.second
            )
            mul_lhs = (
                kernel.value_name(producer_record.third)
                if producer_record.third >= 0
                else kernel.call_texts[-producer_record.third - 1]
            )
            mul_rhs = (
                kernel.value_name(producer_record.fourth)
                if producer_record.fourth >= 0
                else kernel.call_texts[-producer_record.fourth - 1]
            )
            if (
                op != "mul"
                or value_type.first != TYPE_KIND_INT
                or value_type.second != 64
                or product_id < 0
                or use_counts[product_id] != 1
                or not _aarch64_madd_operand_is_simple(mul_lhs)
                or not _aarch64_madd_operand_is_simple(mul_rhs)
            ):
                producer_index += 1
                continue

            crossed_barrier = False
            consumer_index = producer_index + 1
            while consumer_index < instruction_count:
                consumer_instruction_id = block_fact.first + consumer_index
                consumer_metadata: CompilerInt4 = (
                    kernel.instruction_metadata_by_id(
                    consumer_instruction_id
                    )
                )
                consumer_kind = PARSED_INSTRUCTION_KINDS[
                    consumer_metadata.first
                ]
                if _aarch64_madd_instruction_parts_are_barrier(
                    consumer_kind,
                    bool(consumer_metadata.third),
                ):
                    crossed_barrier = True
                if consumer_kind != "binop":
                    consumer_index += 1
                    continue
                consumer_record: CompilerInt4 = kernel.instruction_record(
                    consumer_metadata.second
                )
                consumer_type: CompilerInt4 = kernel.type_header(
                    consumer_record.second
                )
                consumer_op = kernel.call_texts[consumer_record.first]
                lhs_is_product = consumer_record.third == product_id
                rhs_is_product = consumer_record.fourth == product_id
                mnemonic = ""
                accumulator_ref = -1
                if (
                    not consumer_metadata.fourth
                    and consumer_type.first == TYPE_KIND_INT
                    and consumer_type.second == 64
                ):
                    if consumer_op == "add" and lhs_is_product != rhs_is_product:
                        mnemonic = "madd"
                        accumulator_ref = (
                            consumer_record.fourth
                            if lhs_is_product
                            else consumer_record.third
                        )
                    elif (
                        consumer_op == "sub"
                        and rhs_is_product
                        and not lhs_is_product
                    ):
                        mnemonic = "msub"
                        accumulator_ref = consumer_record.third
                if mnemonic:
                    consumer_fact: CompilerInt4 = kernel.instruction_fact_by_id(
                        consumer_instruction_id
                    )
                    result_id = consumer_fact.first
                    result = (
                        "" if result_id < 0 else kernel.value_name(result_id)
                    )
                    accumulator = (
                        kernel.value_name(accumulator_ref)
                        if accumulator_ref >= 0
                        else kernel.call_texts[-accumulator_ref - 1]
                    )
                    if (
                        not crossed_barrier
                        and result_id >= 0
                        and not _aarch64_madd_consumer_is_planned(
                            fusions, block_name, consumer_index
                        )
                        and _aarch64_madd_operand_is_simple(accumulator)
                        and kernel.value_slot_offset(result_id) >= 0
                    ):
                        fusions.append(
                            AArch64MaddFusion(
                                block_name=block_name,
                                producer_index=producer_index,
                                consumer_index=consumer_index,
                                product=product,
                                result=result,
                                mnemonic=mnemonic,
                                mul_lhs=mul_lhs,
                                mul_rhs=mul_rhs,
                                accumulator=accumulator,
                            )
                        )
                    break
                consumer_index += 1
            producer_index += 1
    func.aarch64_madd_fusions = fusions


def aarch64_madd_fusion_for_product(
    func: ParsedFunction,
    product: str,
) -> AArch64MaddFusion | None:
    for fusion in func.aarch64_madd_fusions:
        if text_key_names_equal(fusion.product, product):
            return fusion
    return None


def aarch64_madd_fusion_for_result(
    func: ParsedFunction,
    result: str,
) -> AArch64MaddFusion | None:
    for fusion in func.aarch64_madd_fusions:
        if text_key_names_equal(fusion.result, result):
            return fusion
    return None


@dataclass(frozen=True)
class SelfTargetPassContext:
    target_id: str
    transport: str = _TRANSPORT_TEXT


class SelfTargetPass(Protocol):
    name: str

    def run(self, asm_text: str, ctx: SelfTargetPassContext) -> str:
        ...


class SelfTargetMemoryPass(Protocol):
    name: str

    def run(self, prepared, ctx: SelfTargetPassContext):
        ...


def _is_aarch64_x_register(value: str, *, allow_zero: bool) -> bool:
    if allow_zero and value == "xzr":
        return True
    if len(value) < 2 or value[0] != "x" or not value[1:].isdigit():
        return False
    index = int(value[1:])
    return 0 <= index <= 30 and value == f"x{index}"


def _is_aarch64_memory_base(value: str) -> bool:
    return value == "sp" or _is_aarch64_x_register(value, allow_zero=False)


def _parse_aarch64_64bit_offset_transfer(
    line: str,
) -> tuple[str, str, str, int] | None:
    """Parse the exact ordinary 64-bit offset form eligible for pairing."""

    if not line.startswith("  "):
        return None
    stripped = line[2:]
    opcode, separator, operands = stripped.partition(" ")
    if not separator or opcode not in ("ldr", "str"):
        return None
    value_reg, separator, address = operands.partition(", ")
    if not separator or not _is_aarch64_x_register(value_reg, allow_zero=True):
        return None
    if not address.startswith("[") or not address.endswith("]"):
        return None
    address_parts = [part.strip() for part in address[1:-1].split(",")]
    if len(address_parts) not in (1, 2):
        return None
    base_reg = address_parts[0]
    if not _is_aarch64_memory_base(base_reg):
        return None
    offset = 0
    if len(address_parts) == 2:
        raw_offset = address_parts[1]
        if len(raw_offset) < 2 or raw_offset[0] != "#":
            return None
        try:
            offset = int(raw_offset[1:], 0)
        except ValueError:
            return None
    if offset % 8 != 0:
        return None
    return opcode, value_reg, base_reg, offset


def _aarch64_opcode(line: str) -> str:
    stripped = line.strip()
    opcode, _separator, _operands = stripped.partition(" ")
    return opcode


def _opens_aarch64_exclusive_region(opcode: str) -> bool:
    return opcode.startswith(("ldaxr", "ldxr", "ldaxp", "ldxp"))


def _closes_aarch64_exclusive_region(opcode: str) -> bool:
    return opcode == "clrex" or opcode.startswith(
        ("stlxr", "stxr", "stlxp", "stxp")
    )


def _pair_aarch64_transfer_lines(first_line: str, second_line: str) -> str | None:
    first = _parse_aarch64_64bit_offset_transfer(first_line)
    second = _parse_aarch64_64bit_offset_transfer(second_line)
    if first is None or second is None:
        return None
    first_opcode, first_reg, first_base, first_offset = first
    second_opcode, second_reg, second_base, second_offset = second
    if (
        first_opcode != second_opcode
        or first_base != second_base
        or second_offset != first_offset + 8
        or first_offset < _AARCH64_LDP_STP_MIN_OFFSET
        or first_offset > _AARCH64_LDP_STP_MAX_OFFSET
    ):
        return None

    if first_opcode == "ldr":
        # The first scalar load must not redefine the base used by the second
        # scalar load.  Reject either base overlap (and duplicate destinations)
        # instead of relying on constrained-unpredictable LDP register shapes.
        if (
            first_reg == second_reg
            or first_reg == first_base
            or second_reg == first_base
        ):
            return None

    pair_opcode = "ldp" if first_opcode == "ldr" else "stp"
    address = f"[{first_base}]"
    if first_offset:
        address = f"[{first_base}, #{first_offset}]"
    return f"  {pair_opcode} {first_reg}, {second_reg}, {address}"


def advance_aarch64_memory_pair_barrier(depth: int, begin: bool) -> int:
    if begin:
        return depth + 1
    if depth <= 0:
        raise BackendUnavailable(
            "self AArch64 memory-pair pass saw an unmatched barrier end"
        )
    return depth - 1


def require_closed_aarch64_memory_pair_barrier(depth: int) -> None:
    if depth != 0:
        raise BackendUnavailable(
            "self AArch64 memory-pair pass saw an unterminated barrier"
        )


def pair_adjacent_aarch64_64bit_memory_ops(
    lines: list[str],
    *,
    enabled: bool = True,
) -> list[str]:
    """Pair two proven adjacent ordinary AArch64 64-bit loads or stores.

    This is deliberately a post-register-allocation, no-scheduling pass.  It
    consumes only consecutive lines, requires one textual base and ascending
    8-byte offsets, and never crosses source-level volatile/atomic markers or
    an exclusive-monitor interval.  The marker directives are always removed,
    including when optimization is disabled.
    """

    out: list[str] = []
    barrier_depth = 0
    exclusive_region = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == AARCH64_MEMORY_PAIR_BARRIER_BEGIN:
            barrier_depth = advance_aarch64_memory_pair_barrier(barrier_depth, True)
            index += 1
            continue
        if line == AARCH64_MEMORY_PAIR_BARRIER_END:
            barrier_depth = advance_aarch64_memory_pair_barrier(barrier_depth, False)
            index += 1
            continue

        opcode = _aarch64_opcode(line)
        if _opens_aarch64_exclusive_region(opcode):
            exclusive_region = True

        if (
            enabled
            and barrier_depth == 0
            and not exclusive_region
            and index + 1 < len(lines)
        ):
            next_line = lines[index + 1]
            if next_line not in (
                AARCH64_MEMORY_PAIR_BARRIER_BEGIN,
                AARCH64_MEMORY_PAIR_BARRIER_END,
            ):
                paired = _pair_aarch64_transfer_lines(line, next_line)
                if paired is not None:
                    out.append(paired)
                    index += 2
                    continue

        out.append(line)
        if _closes_aarch64_exclusive_region(opcode):
            exclusive_region = False
        index += 1

    require_closed_aarch64_memory_pair_barrier(barrier_depth)
    return out


@dataclass(frozen=True)
class StripTrailingWhitespacePass:
    name: str = "strip-trailing-whitespace"

    def run(self, asm_text: str, ctx: SelfTargetPassContext) -> str:
        lines = asm_text.splitlines()
        out = "\n".join(line.rstrip() for line in lines)
        if asm_text.endswith("\n"):
            out += "\n"
        return out


@dataclass(frozen=True)
class VerifyPreparedModulePass:
    name: str = "verify-prepared-module"

    def run(self, prepared, ctx: SelfTargetPassContext):
        if not getattr(prepared, "triple", ""):
            raise BackendUnavailable("self target memory pass saw module without target triple")
        for func in getattr(prepared, "functions", ()):
            kernel = getattr(func, "indexed_kernel", None)
            has_indexed_blocks = bool(
                kernel is not None
                and len(kernel.block_names) > 0
                and (
                    not func.blocks
                    or len(kernel.block_names) == len(func.blocks)
                )
            )
            if not getattr(func, "block_map", None) and not has_indexed_blocks:
                raise BackendUnavailable(
                    "self target memory pass saw unprepared function "
                    f"{getattr(func, 'name', '<unknown>')!r}"
                )
            for arg in getattr(func, "args", ()):
                has_indexed_type = False
                if kernel is not None:
                    value_id = kernel.value_id(arg.name)
                    has_indexed_type = (
                        value_id >= 0 and kernel.value_type_id(value_id) >= 0
                    )
                if (
                    arg.name not in getattr(func, "value_types", {})
                    and not has_indexed_type
                ):
                    raise BackendUnavailable(
                        "self target memory pass saw missing argument type for "
                        f"{getattr(func, 'name', '<unknown>')!r}/{arg.name!r}"
                    )
        return prepared


_PASS_REGISTRY: dict[str, SelfTargetPass] = {
    "strip-trailing-whitespace": StripTrailingWhitespacePass(),
}
_MEMORY_PASS_REGISTRY: dict[str, SelfTargetMemoryPass] = {
    "verify-prepared-module": VerifyPreparedModulePass(),
}


def resolve_self_target_pass_transport(raw: str | None = None) -> str:
    value = (
        os.environ.get(PCC_SELF_TARGET_PASS_TRANSPORT_ENV, "")
        if raw is None
        else raw
    )
    normalized = str(value or "").strip().lower()
    if normalized in ("", _TRANSPORT_TEXT):
        return _TRANSPORT_TEXT
    if normalized == _TRANSPORT_MEMORY:
        return _TRANSPORT_MEMORY
    raise BackendUnavailable(
        "unknown self target pass transport "
        f"{value!r}; expected 'text' or 'memory'"
    )


def resolve_self_target_pass_names(
    raw: str | None = None,
    *,
    transport: str | None = None,
) -> tuple[str, ...]:
    value = os.environ.get(PCC_SELF_TARGET_PASSES_ENV, "") if raw is None else raw
    normalized = str(value or "").strip()
    if normalized == "":
        return ()
    lowered = normalized.lower()
    if lowered in ("off", "none", "0", "false", "no"):
        return ()
    if lowered in ("default",):
        return ()
    if lowered in ("all",):
        selected_transport = (
            resolve_self_target_pass_transport()
            if transport is None else transport
        )
        if selected_transport == _TRANSPORT_MEMORY:
            return tuple(_MEMORY_PASS_REGISTRY)
        return tuple(_PASS_REGISTRY)

    out: list[str] = []
    selected_transport = (
        resolve_self_target_pass_transport()
        if transport is None else transport
    )
    registry = (
        _MEMORY_PASS_REGISTRY
        if selected_transport == _TRANSPORT_MEMORY else _PASS_REGISTRY
    )
    for item in normalized.split(","):
        name = item.strip()
        if not name:
            continue
        if name not in registry:
            raise BackendUnavailable(
                f"unknown self target pass {name!r}; known passes: "
                + ", ".join(sorted(registry))
            )
        out.append(name)
    return tuple(out)


def run_self_target_pass_pipeline(
    asm_text: str,
    target_id: str,
    *,
    raw_passes: str | None = None,
    raw_transport: str | None = None,
) -> str:
    transport = resolve_self_target_pass_transport(raw_transport)
    if transport == _TRANSPORT_MEMORY:
        return asm_text
    pass_names = resolve_self_target_pass_names(
        raw_passes,
        transport=transport,
    )
    if not pass_names:
        return asm_text

    ctx = SelfTargetPassContext(target_id=target_id, transport=transport)
    current = asm_text
    for name in pass_names:
        current = _PASS_REGISTRY[name].run(current, ctx)
    return current


def run_self_target_memory_pass_pipeline(
    prepared,
    target_id: str,
    *,
    raw_passes: str | None = None,
    raw_transport: str | None = None,
):
    transport = resolve_self_target_pass_transport(raw_transport)
    if transport != _TRANSPORT_MEMORY:
        return prepared
    pass_names = resolve_self_target_pass_names(
        raw_passes,
        transport=transport,
    )
    if not pass_names:
        return prepared
    ctx = SelfTargetPassContext(target_id=target_id, transport=transport)
    current = prepared
    for name in pass_names:
        current = _MEMORY_PASS_REGISTRY[name].run(current, ctx)
    return current
