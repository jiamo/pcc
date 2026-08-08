from __future__ import annotations

"""Conservative block-local scalar register allocation for AArch64 Darwin.

The existing stack slot remains allocated for every value.  This pass only
adds an optional register projection for integer/pointer SSA values whose
entire lifetime is proven to stay inside one supported basic block.  Any shape
outside that proof keeps using its slot.

``x1`` through ``x8`` are caller-saved by AAPCS64 and are deliberately unused
by the scalar lowering covered here.  The established instruction emitters use
``x9`` through ``x17`` as scratch registers, while returns use ``x0``.
Float/vector SSA values never become candidates.  Calls are interval barriers:
call results, call operands, and values live across a call stay spilled, while
values whose complete lifetime is before or after the call remain eligible.
PHI values and PHI inputs stay spilled, but unrelated local definitions later
in a PHI block may still use this register projection.

This is a deliberately finite block-local subset of LLVM 20.1.8's
``RegAllocFast.cpp`` model: every value keeps its preassigned spill slot, and a
mapping is installed only when the whole interval is proven safe for x1-x8.
"""

from .self_backend_analysis import (
    collect_block_local_last_uses,
    is_local_value_ref,
    instruction_defined_value,
)
from .self_backend_ir import (
    I1,
    ParsedBlock,
    ParsedFunction,
    ParsedInstr,
    SlotInfo,
    TypeDesc,
    parsed_function_value_slot,
    text_key_mapping_get,
    text_key_names_equal,
)
from .self_backend_target_passes import (
    AArch64MaddFusion,
    aarch64_madd_fusion_for_product,
)


_REGISTER_POOL = (1, 2, 3, 4, 5, 6, 7, 8)

# Every instruction in an allocated block must be known not to clobber x1-x8.
# Non-candidate floating/vector/aggregate and atomic values still use their
# mandatory slots, but their current AArch64 emitters use only v-registers and
# x9-x17 scratch GPRs, so unrelated scalar intervals may cross them.  Calls
# (including intrinsic calls) are admitted only as clobber positions;
# intervals touching them are rejected below.  An unclassified future kind,
# and the x86_64-only syscall6 shape, reject the whole block.
_POOL_PRESERVING_INSTRUCTION_KINDS = {
    "alloca",
    "store",
    "load",
    "load_atomic",
    "store_atomic",
    "atomicrmw",
    "cmpxchg",
    "fence",
    "binop",
    "fbinop",
    "fneg",
    "icmp",
    "fcmp",
    "cast",
    "select",
    "freeze",
    "insertelement",
    "extractelement",
    "shufflevector",
    "extractvalue",
    "insertvalue",
    "va_arg",
    "gep",
    "call",
}


def _is_scalar_register_type(value_type: TypeDesc) -> bool:
    return value_type.is_int or value_type.is_ptr


def _register_types_match(left: TypeDesc, right: TypeDesc) -> bool:
    # All pointer projections are one 64-bit GPR even when typed-pointer IR
    # spells different pointees at the definition and use sites.
    if left.is_ptr and right.is_ptr:
        return True
    return left.describe() == right.describe()


def _type_mapping_get(
    mapping: dict[str, TypeDesc], key: str
) -> TypeDesc | None:
    # Recover via the shared incremental text-key index instead of walking the
    # whole mapping: the index is O(1) amortised, keeps the mapping pinned so
    # an id() cannot be recycled under it, and covers both recovery cases the
    # scan covered (`by_id` for dot-numeric spellings, `by_bucket` for plain
    # equality after an inconsistent native hash).  The scan was 12.3 s of a
    # 120 s emit profile on one 10.4 MB module.
    return text_key_mapping_get(mapping, key)


def _int_mapping_get(mapping: dict[str, int], key: str) -> int | None:
    return text_key_mapping_get(mapping, key)


def _int_mapping_set(mapping: dict[str, int], key: str, value: int) -> None:
    for existing_key in mapping:
        if text_key_names_equal(existing_key, key):
            mapping[existing_key] = value
            return
    mapping[key] = value


def _int_mapping_delete(mapping: dict[str, int], key: str) -> None:
    for existing_key in list(mapping):
        if text_key_names_equal(existing_key, key):
            del mapping[existing_key]
            return


def _last_use_mapping_get(
    mapping: dict[str, dict[str, int]], key: str
) -> dict[str, int] | None:
    return text_key_mapping_get(mapping, key)


def _candidate_definition_type(
    func: ParsedFunction, instr: ParsedInstr
) -> TypeDesc | None:
    kind = instr.kind
    data = instr.data
    if kind == "load":
        _dest, value_type, _ptr_type, _ptr = data
        return value_type if _is_scalar_register_type(value_type) else None
    if kind == "binop":
        _op, _dest, value_type, _lhs, _rhs = data
        return value_type if value_type.is_int else None
    if kind == "icmp":
        _cond, _dest, value_type, _lhs, _rhs = data
        return I1 if _is_scalar_register_type(value_type) else None
    if kind == "cast":
        _op, _dest, src_type, _value, dst_type = data
        if _is_scalar_register_type(src_type) and _is_scalar_register_type(dst_type):
            return dst_type
        return None
    if kind in {"select", "freeze"}:
        dest = data[0]
        value_type = data[1]
        if not _is_scalar_register_type(value_type):
            return None
        recorded_type = _type_mapping_get(func.value_types, dest)
        if recorded_type is None or not _register_types_match(
            recorded_type, value_type
        ):
            return None
        return value_type
    if kind == "gep":
        dest = data[0]
        value_type = _type_mapping_get(func.value_types, dest)
        if value_type is not None and value_type.is_ptr:
            return value_type
    return None


def _text_list_contains(values: list[str], value: str) -> bool:
    for existing in values:
        if text_key_names_equal(existing, value):
            return True
    return False


def _interval_touches_call(
    start: int,
    end: int,
    call_positions: list[int],
) -> bool:
    # A value used as a call operand ends at the call position and must remain
    # spilled: ABI argument setup may overwrite x1-x8 before every operand has
    # been copied.  Strictly pre-call and strictly post-call intervals are safe.
    for call_position in call_positions:
        if start <= call_position <= end:
            return True
    return False


def _extend_aarch64_madd_operand_liveness(
    func: ParsedFunction,
    block: ParsedBlock,
    last_uses: dict[str, int],
) -> None:
    """Keep delayed multiply inputs live until their fused consumer."""

    for fusion in func.aarch64_madd_fusions:
        if not text_key_names_equal(fusion.block_name, block.name):
            continue
        for operand in (fusion.mul_lhs, fusion.mul_rhs):
            last_use = _int_mapping_get(last_uses, operand)
            if last_use is not None and last_use < fusion.consumer_index:
                _int_mapping_set(last_uses, operand, fusion.consumer_index)


def _slots_overlap(left: SlotInfo, right: SlotInfo) -> bool:
    left_start = left.offset - left.type.value_slot_size
    right_start = right.offset - right.type.value_slot_size
    return left_start < right.offset and right_start < left.offset


def _aarch64_madd_operand_survives_to_consumer(
    func: ParsedFunction,
    block: ParsedBlock,
    fusion: AArch64MaddFusion,
    operand: str,
) -> bool:
    # An allocated x1-x8 projection has had its interval extended above and is
    # therefore authoritative even if stack-slot preparation reused its old
    # spill slot after the original multiply.
    if _int_mapping_get(func.value_registers, operand) is not None:
        return True

    operand_slot = parsed_function_value_slot(func, operand)
    if operand_slot is None:
        # Constants do not need storage.  An unresolved local-looking value is
        # not a constant proof and must fail closed.
        return not is_local_value_ref(operand)

    index = fusion.producer_index
    while index < fusion.consumer_index:
        instr = block.instructions[index]
        dest = instruction_defined_value(instr)
        if (
            dest is not None
            and not text_key_names_equal(dest, operand)
            and not (
                index == fusion.producer_index
                and text_key_names_equal(dest, fusion.product)
            )
        ):
            dest_slot = parsed_function_value_slot(func, dest)
            if dest_slot is not None and _slots_overlap(operand_slot, dest_slot):
                return False
        index += 1
    return True


def _aarch64_madd_fusion_storage_is_safe(
    func: ParsedFunction,
    fusion: AArch64MaddFusion,
) -> bool:
    block = None
    for candidate in func.blocks:
        if text_key_names_equal(candidate.name, fusion.block_name):
            block = candidate
            break
    if block is None:
        return False
    return _aarch64_madd_operand_survives_to_consumer(
        func, block, fusion, fusion.mul_lhs
    ) and _aarch64_madd_operand_survives_to_consumer(
        func, block, fusion, fusion.mul_rhs
    )


def allocate_aarch64_block_registers(func: ParsedFunction) -> None:
    """Populate ``func.value_registers`` with conservative linear-scan picks."""

    func.value_registers = {}
    if func.is_vararg:
        return
    block_last_uses = collect_block_local_last_uses(func)
    phi_inputs: list[str] = []
    for block in func.blocks:
        for phi in block.phis:
            for incoming in phi.incoming:
                phi_inputs.append(incoming.value)

    for block in func.blocks:
        block_is_safe = True
        for instr in block.instructions:
            if instr.kind not in _POOL_PRESERVING_INSTRUCTION_KINDS:
                block_is_safe = False
                break
        if not block_is_safe:
            continue

        call_positions = [
            position
            for position, instr in enumerate(block.instructions)
            if instr.kind == "call"
        ]

        last_uses = _last_use_mapping_get(block_last_uses, block.name)
        if last_uses is None:
            continue
        _extend_aarch64_madd_operand_liveness(func, block, last_uses)
        intervals: list[tuple[int, int, str]] = []
        for position, instr in enumerate(block.instructions):
            dest = instruction_defined_value(instr)
            if dest is None or _text_list_contains(phi_inputs, dest):
                continue
            if aarch64_madd_fusion_for_product(func, dest) is not None:
                # The product is never materialized separately, so assigning
                # it a register would only steal capacity from real values.
                continue
            value_type = _candidate_definition_type(func, instr)
            if value_type is None:
                continue
            recorded_type = _type_mapping_get(func.value_types, dest)
            if (
                recorded_type is None
                or not _register_types_match(recorded_type, value_type)
            ):
                continue
            if parsed_function_value_slot(func, dest) is None:
                # Allocation is only an optional projection.  Never create a
                # register-only value: every candidate must retain the
                # preassigned slot needed by pressure/type fallback paths.
                continue
            # Preserve the established compare/cset/byte-slot/branch peephole
            # for a boolean consumed directly by br_cond.  Other integer SSA
            # values, including booleans used by scalar instructions, remain
            # eligible for this block-local slice.
            if (
                value_type.is_int
                and value_type.width == 1
                and block.terminator is not None
                and block.terminator.kind == "br_cond"
                and text_key_names_equal(block.terminator.data[0], dest)
            ):
                continue
            last_use = _int_mapping_get(last_uses, dest)
            if last_use is None or last_use < position:
                continue
            if _interval_touches_call(position, last_use, call_positions):
                continue
            intervals.append((position, last_use, dest))

        intervals.sort(key=lambda interval: (interval[0], interval[1], interval[2]))
        active: list[tuple[int, str, int]] = []
        free_registers = list(_REGISTER_POOL)
        for start, end, value_name in intervals:
            still_active: list[tuple[int, str, int]] = []
            for active_end, active_name, register_index in active:
                # Uses are materialized before the current definition is
                # committed, so an interval ending at this instruction can
                # safely donate its register to the new result.
                if active_end <= start:
                    free_registers.append(register_index)
                else:
                    still_active.append((active_end, active_name, register_index))
            active = still_active
            free_registers.sort()

            register_index: int | None = None
            if free_registers:
                register_index = free_registers.pop(0)
            elif active:
                spill_index = 0
                index = 1
                while index < len(active):
                    if active[index][0] > active[spill_index][0]:
                        spill_index = index
                    index += 1
                spill_end, spill_name, spill_register = active[spill_index]
                if spill_end > end:
                    # Allocation is finalized before emission.  Removing this
                    # mapping makes the displaced value's definition and uses
                    # take the pre-existing stack-slot path; no runtime spill
                    # instruction has to be synthesized here.
                    _int_mapping_delete(func.value_registers, spill_name)
                    active.pop(spill_index)
                    register_index = spill_register
            if register_index is None:
                continue
            func.value_registers[value_name] = register_index
            active.append((end, value_name, register_index))

    # Stack slots were assigned before this target combine was planned.  When
    # an extended operand did not win a register, retain the plan only if no
    # intervening definition reuses/overlaps that operand's spill slot.
    func.aarch64_madd_fusions = [
        fusion
        for fusion in func.aarch64_madd_fusions
        if _aarch64_madd_fusion_storage_is_safe(func, fusion)
    ]


def allocated_register_name(
    func: ParsedFunction, value_name: str, value_type: TypeDesc
) -> str | None:
    """Return the width-correct register alias for a proven assignment."""

    if not _is_scalar_register_type(value_type):
        return None
    register_index = _int_mapping_get(func.value_registers, value_name)
    if register_index is None or register_index not in _REGISTER_POOL:
        return None
    recorded_type = _type_mapping_get(func.value_types, value_name)
    if recorded_type is None or not _register_types_match(recorded_type, value_type):
        return None
    prefix = "x" if value_type.is_ptr or value_type.width > 32 else "w"
    return f"{prefix}{register_index}"


def commit_allocated_scalar_result(
    func: ParsedFunction,
    value_name: str,
    value_type: TypeDesc,
    source_reg: str,
) -> list[str] | None:
    """Move a result into its assigned register, or request slot fallback."""

    dest_reg = allocated_register_name(func, value_name, value_type)
    if dest_reg is None:
        return None
    if value_type.is_int and value_type.width <= 16:
        # Mirror the pre-existing slot round trip: i1/i8 use a byte slot and
        # i16 a halfword slot.  Wider sub-i32 values already use a word slot.
        mask = 0xFF if value_type.width <= 8 else 0xFFFF
        return [f"  and {dest_reg}, {source_reg}, #0x{mask:x}"]
    if dest_reg == source_reg:
        return []
    return [f"  mov {dest_reg}, {source_reg}"]


__all__ = [
    "allocate_aarch64_block_registers",
    "allocated_register_name",
    "commit_allocated_scalar_result",
]
