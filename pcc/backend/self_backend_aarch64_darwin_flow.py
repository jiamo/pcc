from __future__ import annotations

from . import BackendUnavailable
from .self_backend_aarch64_darwin_abi import aggregate_fits_reg_abi, reg_name
from .self_backend_aarch64_darwin_materialize import (
    materialize_aggregate_storage_address,
    materialize_value,
    store_large_aggregate_literal_to_address,
)
from .self_backend_aarch64_darwin_regs import emit_add_offset, emit_stack_adjust
from .self_backend_aarch64_darwin_slots import (
    copy_address_to_address,
    copy_address_to_slot,
    load_value_from_address,
    store_reg_to_slot,
    store_value_regs_to_slot,
    store_value_to_address,
    zero_address,
)
from .self_backend_ir import (
    ParsedFunction,
    TypeDesc,
    _align_to,
    text_key_mapping_get,
)
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import is_aggregate_literal_value


def emit_bit_count_intrinsic_call(
    func: ParsedFunction,
    dest: str | None,
    ret_type: TypeDesc,
    callee: str,
    args: tuple[tuple[TypeDesc, str], ...],
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    if callee.startswith("llvm.ctpop."):
        if len(args) != 1:
            raise BackendUnavailable(
                f"self backend expects one arg for {callee!r} in {func.name!r}"
            )
    elif len(args) != 2:
        raise BackendUnavailable(
            f"self backend expects two args for {callee!r} in {func.name!r}"
        )
    if dest is None or dest not in func.value_slots:
        return []
    arg_type, value = args[0]
    if not arg_type.is_int or not ret_type.is_int or arg_type.width != ret_type.width:
        raise BackendUnavailable(
            f"self backend expects matching integer arg/ret types for {callee!r}, got {arg_type.describe()} -> {ret_type.describe()}"
        )
    if arg_type.width not in (32, 64):
        raise BackendUnavailable(
            f"self backend only supports ctlz/cttz on i32/i64 for now, got {arg_type.describe()}"
        )
    lines = materialize_value(func, value, arg_type, 9, module_symbols)
    src = reg_name(arg_type, 9)
    dst = reg_name(ret_type, 11)
    if callee.startswith("llvm.ctlz."):
        lines.append(f"  clz {dst}, {src}")
    elif callee.startswith("llvm.cttz."):
        lines.append(f"  rbit {dst}, {src}")
        lines.append(f"  clz {dst}, {dst}")
    elif callee.startswith("llvm.ctpop."):
        if arg_type.width == 32:
            lines.append("  mov w10, w9")
            lines.append("  fmov d10, x10")
        else:
            lines.append("  fmov d10, x9")
        lines.append("  cnt v10.8b, v10.8b")
        lines.append("  addv b10, v10.8b")
        lines.append("  umov w11, v10.b[0]")
    else:
        raise BackendUnavailable(f"self backend does not support intrinsic {callee!r}")
    lines.extend(store_reg_to_slot(dst, func.value_slots[dest]))
    return lines


def _order_scalar_phi_copies(func, assignments):
    """Order scalar phi copies for safe direct slot-to-slot stores.

    A copy ``dest_i <- src_i`` must read ``slot(src_i)`` before any other copy
    overwrites that slot by writing its own destination. We emit a copy only
    once no *other* pending copy still reads the slot it is about to write;
    coalesced self-copies (source already in the destination slot) are dropped
    as no-ops. Returns the ordered ``(phi, match)`` list, or ``None`` if the
    copies form a cycle (a true swap) that no ordering can satisfy — the caller
    then falls back to the temp-buffered path.
    """
    copies = []
    for phi, match, _offset in assignments:
        dest_off = func.value_slots[phi.dest].offset
        src_slot = text_key_mapping_get(func.value_slots, match.value)
        src_off = src_slot.offset if src_slot is not None else None
        if src_off == dest_off:
            continue  # coalesced self-copy: no instruction needed
        copies.append((phi, match, dest_off, src_off))

    ordered = []
    pending = list(copies)
    while pending:
        ready = [
            c
            for c in pending
            if not any(other is not c and other[3] == c[2] for other in pending)
        ]
        if not ready:
            return None  # cycle: caller uses the temp-buffered path
        for c in ready:
            ordered.append((c[0], c[1]))
            pending.remove(c)
    return ordered


def emit_phi_assignments(
    func: ParsedFunction,
    *,
    source_block: str,
    target_block: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    target = text_key_mapping_get(func.block_map, target_block)
    if target is None:
        for block in func.blocks:
            if block.name == target_block:
                target = block
                break
    if target is None:
        raise BackendUnavailable(
            f"self backend branch targets unknown block {target_block!r} in {func.name!r}"
        )

    assignments = []
    temp_offset = 0
    for phi in target.phis:
        if phi.dest not in func.value_slots:
            continue
        match = None
        for incoming in phi.incoming:
            if incoming.label == source_block:
                match = incoming
                break
        if match is None:
            raise BackendUnavailable(
                f"self backend could not resolve phi incoming for {phi.dest!r} from {source_block!r}"
            )
        temp_align = max(1, min(phi.type.align, 8))
        temp_offset = _align_to(temp_offset, temp_align)
        assignments.append((phi, match, temp_offset))
        temp_offset += phi.type.slot_size

    if not assignments:
        return []

    # Scalar phi copies can be lowered as direct slot-to-slot stores when they
    # can be *ordered* so that no copy overwrites a slot another copy still
    # needs to read. Slot coalescing can put a phi source in the same physical
    # slot as a *different* phi's destination, so the safe order is computed
    # from slot offsets, not SSA names. When the copies form a cycle (a true
    # swap), no ordering is safe and we fall through to the temp-buffered path
    # below, which stages every source before writing any destination.
    if all(
        not phi.type.is_array and not phi.type.is_struct
        for phi, _match, _offset in assignments
    ):
        ordered = _order_scalar_phi_copies(func, assignments)
        if ordered is not None:
            lines: list[str] = []
            for phi, match in ordered:
                lines.extend(
                    materialize_value(func, match.value, phi.type, 9, module_symbols)
                )
                lines.extend(store_value_regs_to_slot(func.value_slots[phi.dest], 9))
            return lines

    total_temp = _align_to(temp_offset, 16)
    lines: list[str] = []
    if total_temp:
        lines.extend(emit_stack_adjust(-total_temp))

    temp_addr_reg = "x13"

    def _emit_temp_addr(offset: int) -> list[str]:
        return emit_add_offset(temp_addr_reg, "sp", offset)

    for phi, match, offset in assignments:
        lines.extend(_emit_temp_addr(offset))
        if (phi.type.is_array or phi.type.is_struct) and not aggregate_fits_reg_abi(
            phi.type
        ):
            if match.value == "zeroinitializer":
                lines.extend(zero_address(temp_addr_reg, phi.type.slot_size))
            elif is_aggregate_literal_value(match.value):
                lines.extend(
                    store_large_aggregate_literal_to_address(
                        phi.type,
                        match.value,
                        temp_addr_reg,
                        module_symbols=module_symbols,
                    )
                )
            else:
                lines.extend(
                    materialize_aggregate_storage_address(
                        func, match.value, phi.type, "x14"
                    )
                )
                lines.extend(
                    copy_address_to_address("x14", temp_addr_reg, phi.type.slot_size)
                )
            continue
        lines.extend(materialize_value(func, match.value, phi.type, 9, module_symbols))
        lines.extend(store_value_to_address(temp_addr_reg, phi.type, 9))

    for phi, _match, offset in assignments:
        if (phi.type.is_array or phi.type.is_struct) and not aggregate_fits_reg_abi(
            phi.type
        ):
            lines.extend(_emit_temp_addr(offset))
            lines.extend(
                copy_address_to_slot(temp_addr_reg, func.value_slots[phi.dest])
            )
            continue
        lines.extend(_emit_temp_addr(offset))
        lines.extend(load_value_from_address(temp_addr_reg, phi.type, 9))
        lines.extend(store_value_regs_to_slot(func.value_slots[phi.dest], 9))

    if total_temp:
        lines.extend(emit_stack_adjust(total_temp))
    return lines
