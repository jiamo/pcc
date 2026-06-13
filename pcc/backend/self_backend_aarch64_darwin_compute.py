from __future__ import annotations

from . import BackendUnavailable
from .self_backend_aarch64_darwin_abi import aggregate_fits_reg_abi, reg_name
from .self_backend_aarch64_darwin_addr import emit_gep_offset
from .self_backend_aarch64_darwin_calls import emit_call_instruction, emit_va_arg
from .self_backend_aarch64_darwin_materialize import (
    copy_large_aggregate_value_to_slot,
    materialize_aggregate_storage_address,
    materialize_pointer,
    materialize_value,
    store_large_aggregate_literal_to_address,
)
from .self_backend_aarch64_darwin_ops import (
    aarch64_cc,
    emit_aggregate_bitwise_binop,
    emit_binop,
    emit_cast,
    emit_fbinop,
    emit_fcmp_result,
    sign_extend_int_reg,
)
from .self_backend_aarch64_darwin_regs import emit_add_offset, emit_const_to_reg
from .self_backend_aarch64_darwin_slots import (
    copy_address_to_address,
    copy_address_to_slot,
    emit_slot_base_address,
    load_value_from_address,
    store_reg_to_slot,
    store_value_to_address,
    store_value_regs_to_slot,
    zero_address,
    zero_slot,
)
from .self_backend_ir import I1, ParsedFunction
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import (
    aggregate_literal_to_bytes,
    const_int_from_value,
    decode_value_token,
    is_aggregate_literal_value,
    split_top_level,
    strip_typed_initializer,
)


def _vector_lane_stride(vector_type: TypeDesc) -> tuple[TypeDesc, int]:
    if not vector_type.is_array or vector_type.elem is None:
        raise BackendUnavailable(
            f"self backend vector lowering currently expects array vector lanes, got {vector_type.describe()}"
        )
    lane_type = vector_type.elem
    # Pointer-lane vectors are not arithmetic vectors for the self backend, but
    # they are valid aggregate values in LLVM IR.  Lua/NumPy-shaped C lowering
    # uses forms such as [2 x void*] for table/function dispatch data.  Treat
    # each lane as an 8-byte scalar for extract/insert/shuffle/select and
    # slot/memory copying.  Arithmetic/reduction intrinsics are still guarded by
    # their individual callers and remain int/fp-only.
    if not (lane_type.is_int or lane_type.is_fp or lane_type.is_ptr):
        raise BackendUnavailable(
            f"self backend vector lowering currently expects integer/fp/ptr vector lanes, got {vector_type.describe()}"
        )
    return lane_type, lane_type.slot_size


def _emit_vector_storage_address(
    func: ParsedFunction,
    value: str,
    value_type,
    reg: str,
    *,
    scratch_reg: str,
) -> list[str]:
    if value in func.value_slots:
        return emit_add_offset(
            reg, "x29", -func.value_slots[value].offset, scratch_reg=scratch_reg
        )
    if (
        value in func.alloca_slots
        and func.alloca_slots[value].allocated_type.describe() == value_type.describe()
    ):
        return emit_add_offset(
            reg, "x29", -func.alloca_slots[value].offset, scratch_reg=scratch_reg
        )
    return materialize_aggregate_storage_address(func, value, value_type, reg)


def _emit_slot_base_address_nonclobbering(
    slot, reg: str, *, scratch_reg: str
) -> list[str]:
    return emit_add_offset(reg, "x29", -slot.offset, scratch_reg=scratch_reg)


def _emit_vector_int_binop(
    func: ParsedFunction,
    op: str,
    dest: str,
    value_type,
    lhs: str,
    rhs: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    lane_type, lane_stride = _vector_lane_stride(value_type)
    dest_slot = func.value_slots[dest]
    lines: list[str] = []
    lhs_addr: str | None = None
    rhs_addr: str | None = None
    if lhs in func.value_slots or lhs in func.alloca_slots:
        lines.extend(
            _emit_vector_storage_address(
                func, lhs, value_type, "x15", scratch_reg="x14"
            )
        )
        lhs_addr = "x15"
    if rhs in func.value_slots or rhs in func.alloca_slots:
        lines.extend(
            _emit_vector_storage_address(
                func, rhs, value_type, "x16", scratch_reg="x14"
            )
        )
        rhs_addr = "x16"
    lines.extend(
        _emit_slot_base_address_nonclobbering(dest_slot, "x17", scratch_reg="x14")
    )
    for lane_index in range(value_type.count):
        dst_addr = "x17"
        if lane_index:
            offset = lane_index * lane_stride
            lines.extend(emit_add_offset("x14", "x17", offset))
            dst_addr = "x14"
        lines.extend(
            _emit_vector_lane_value(
                func,
                lhs,
                value_type,
                lane_type,
                lane_index,
                9,
                lhs_addr,
                module_symbols,
            )
        )
        lines.extend(
            _emit_vector_lane_value(
                func,
                rhs,
                value_type,
                lane_type,
                lane_index,
                10,
                rhs_addr,
                module_symbols,
            )
        )
        lines.extend(emit_binop(op, lane_type))
        lines.extend(store_value_to_address(dst_addr, lane_type, 11))
    return lines


def _emit_vector_lane_value(
    func: ParsedFunction,
    value: str,
    vector_type: TypeDesc,
    lane_type: TypeDesc,
    lane_index: int,
    dest_reg_index: int,
    addr_reg: str | None,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    lane_reg = reg_name(lane_type, dest_reg_index)
    if addr_reg is not None:
        stride = _vector_lane_stride(vector_type)[1]
        source_addr = addr_reg
        if lane_index:
            temp_reg = "x12" if addr_reg != "x12" else "x13"
            lines = emit_add_offset(temp_reg, addr_reg, lane_index * stride)
            lines.extend(load_value_from_address(temp_reg, lane_type, dest_reg_index))
            return lines
        return load_value_from_address(source_addr, lane_type, dest_reg_index)
    if value == "zeroinitializer":
        return emit_const_to_reg(lane_type, lane_reg, 0)
    const_value = const_int_from_value(value)
    if const_value is not None:
        return emit_const_to_reg(lane_type, lane_reg, const_value)
    if is_aggregate_literal_value(value):
        stride = _vector_lane_stride(vector_type)[1]
        try:
            literal_bytes = aggregate_literal_to_bytes(vector_type, value)
            lane_bytes = literal_bytes[
                lane_index * stride : lane_index * stride + lane_type.slot_size
            ]
            lane_value = int.from_bytes(lane_bytes, "little")
            return emit_const_to_reg(lane_type, lane_reg, lane_value)
        except BackendUnavailable:
            lane_value = _aggregate_literal_lane_value(vector_type, value, lane_index)
            return materialize_value(
                func,
                decode_value_token(lane_value),
                lane_type,
                dest_reg_index,
                module_symbols,
            )
    raise BackendUnavailable(
        f"self backend cannot materialize vector lane value in {func.name!r}: {value}"
    )


def _aggregate_literal_lane_value(vector_type, value: str, lane_index: int) -> str:
    text = value.strip()
    if text.startswith("<") and text.endswith(">"):
        text = "[" + text[1:-1].strip() + "]"
    if not (text.startswith("[") and text.endswith("]")):
        raise BackendUnavailable(
            f"self backend expected vector literal for {vector_type.describe()}, got {value!r}"
        )
    items = split_top_level(text[1:-1].strip())
    if lane_index < 0 or lane_index >= len(items):
        raise BackendUnavailable(
            f"self backend vector literal lane {lane_index} out of range for {vector_type.describe()}: {value!r}"
        )
    return strip_typed_initializer(items[lane_index])


def _emit_insertelement(
    func: ParsedFunction,
    dest: str,
    vector_type,
    vector_value: str,
    elem_type,
    elem_value: str,
    index_value: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    lane_type, lane_stride = _vector_lane_stride(vector_type)
    if elem_type.describe() != lane_type.describe():
        raise BackendUnavailable(
            f"self backend insertelement expects matching lane type in {func.name!r}: "
            f"{elem_type.describe()} into {vector_type.describe()}"
        )
    if dest not in func.value_slots:
        return []
    lane_index = int(index_value)
    if lane_index < 0 or lane_index >= vector_type.count:
        raise BackendUnavailable(
            f"self backend insertelement lane index out of range in {func.name!r}: {index_value}"
        )
    dest_slot = func.value_slots[dest]
    if vector_value in {"poison", "zeroinitializer"}:
        lines = zero_slot(dest_slot)
    else:
        lines = copy_large_aggregate_value_to_slot(
            func, vector_value, vector_type, dest_slot, module_symbols=module_symbols
        )
    # Materialize the new element BEFORE computing the dest address.
    # ``materialize_value`` may pick ``x15`` as a scratch GPR via
    # ``pick_scratch_gpr`` (e.g. when reading an SSA slot whose offset
    # exceeds 255 in a large frame like lua_newstate's). If we set
    # ``x15`` to the dest address first, that scratch use clobbers it
    # and the lane store ends up writing back into the *source* slot,
    # silently dropping the inserted element.
    lines.extend(materialize_value(func, elem_value, elem_type, 9, module_symbols))
    lines.extend(emit_slot_base_address(dest_slot, "x15"))
    if lane_index:
        lines.extend(emit_add_offset("x15", "x15", lane_index * lane_stride))
    lines.extend(store_value_to_address("x15", elem_type, 9))
    return lines


def _emit_extractelement(
    func: ParsedFunction,
    dest: str,
    vector_type,
    vector_value: str,
    index_value: str,
    elem_type,
) -> list[str]:
    lane_type, lane_stride = _vector_lane_stride(vector_type)
    if elem_type.describe() != lane_type.describe():
        raise BackendUnavailable(
            f"self backend extractelement expects result type to match lane type in {func.name!r}: "
            f"{elem_type.describe()} from {vector_type.describe()}"
        )
    lane_index = int(index_value)
    if lane_index < 0 or lane_index >= vector_type.count:
        raise BackendUnavailable(
            f"self backend extractelement lane index out of range in {func.name!r}: {index_value}"
        )
    dest_slot = func.value_slots[dest]
    lines = materialize_aggregate_storage_address(
        func, vector_value, vector_type, "x15"
    )
    if lane_index:
        lines.extend(emit_add_offset("x15", "x15", lane_index * lane_stride))
    lines.extend(load_value_from_address("x15", elem_type, 10))
    lines.extend(store_reg_to_slot(reg_name(elem_type, 10), dest_slot))
    return lines


def _emit_shufflevector(
    func: ParsedFunction,
    dest: str,
    vector_type: TypeDesc,
    lhs: str,
    rhs: str,
    mask_type: TypeDesc,
    mask_value: str,
) -> list[str]:
    lane_type, lane_stride = _vector_lane_stride(vector_type)
    source_type = func.value_types.get(lhs)
    if source_type is None or not source_type.is_array or source_type.elem is None:
        raise BackendUnavailable(
            f"self backend shufflevector expects materialized vector lhs in {func.name!r}: {lhs}"
        )
    source_elem: TypeDesc = source_type.elem
    if source_elem.describe() != lane_type.describe():
        raise BackendUnavailable(
            f"self backend shufflevector lane type mismatch in {func.name!r}: {source_type.describe()} -> {vector_type.describe()}"
        )
    rhs_type = None
    rhs_addr: str | None = None
    if rhs != "poison":
        rhs_type = func.value_types.get(rhs)
        if rhs_type is None or not rhs_type.is_array or rhs_type.elem is None:
            raise BackendUnavailable(
                f"self backend shufflevector expects materialized vector rhs in {func.name!r}: {rhs}"
            )
        rhs_elem: TypeDesc = rhs_type.elem
        if (
            rhs_elem.describe() != lane_type.describe()
            or rhs_type.count != source_type.count
        ):
            raise BackendUnavailable(
                f"self backend shufflevector rhs lane type/count mismatch in {func.name!r}: {rhs_type.describe()}"
            )
        if rhs not in func.value_slots and rhs not in func.alloca_slots:
            raise BackendUnavailable(
                f"self backend shufflevector rhs form not translated yet in {func.name!r}: {rhs}"
            )
        rhs_addr = "x16"
    if not mask_type.is_array or mask_type.count != vector_type.count:
        raise BackendUnavailable(
            f"self backend shufflevector currently expects result lane count to match mask lane count in {func.name!r}"
        )
    if dest not in func.value_slots:
        return []
    dest_slot = func.value_slots[dest]
    lines = _emit_vector_storage_address(
        func, lhs, source_type, "x15", scratch_reg="x14"
    )
    if rhs_addr is not None:
        assert rhs_type is not None
        lines.extend(
            _emit_vector_storage_address(
                func, rhs, rhs_type, rhs_addr, scratch_reg="x14"
            )
        )
    lines.extend(
        _emit_slot_base_address_nonclobbering(dest_slot, "x17", scratch_reg="x14")
    )
    if mask_value == "zeroinitializer":
        mask_indices = [0] * vector_type.count
    elif is_aggregate_literal_value(mask_value):
        mask_text = mask_value
        if mask_text.startswith("<") and mask_text.endswith(">"):
            mask_text = mask_text[1:-1].strip()
        elif mask_text.startswith("[") and mask_text.endswith("]"):
            mask_text = mask_text[1:-1].strip()
        else:
            raise BackendUnavailable(
                f"self backend shufflevector mask literal not translated yet in {func.name!r}: {mask_value}"
            )
        mask_indices = []
        for item in split_top_level(mask_text):
            index = const_int_from_value(strip_typed_initializer(item))
            if index is None:
                raise BackendUnavailable(
                    f"self backend shufflevector mask lane is not constant in {func.name!r}: {mask_value}"
                )
            mask_indices.append(index)
    else:
        raise BackendUnavailable(
            f"self backend shufflevector mask form not translated yet in {func.name!r}: {mask_value}"
        )
    for lane_index, selected_lane in enumerate(mask_indices):
        if selected_lane < 0:
            raise BackendUnavailable(
                f"self backend shufflevector negative mask lane not translated yet in {func.name!r}: {mask_value}"
            )
        if selected_lane < source_type.count:
            source_addr = "x15"
            source_lane = selected_lane
        elif rhs == "poison":
            raise BackendUnavailable(
                f"self backend shufflevector mask lane out of range for poison rhs in {func.name!r}: {mask_value}"
            )
        else:
            assert rhs_type is not None
            source_addr = rhs_addr
            source_lane = selected_lane - source_type.count
            if source_lane >= rhs_type.count:
                raise BackendUnavailable(
                    f"self backend shufflevector mask lane out of range in {func.name!r}: {mask_value}"
                )
        dst_addr = "x17"
        if lane_index:
            lines.extend(emit_add_offset("x14", "x17", lane_index * lane_stride))
            dst_addr = "x14"
        assert source_addr is not None
        src_addr = source_addr
        if source_lane:
            lines.extend(emit_add_offset("x12", source_addr, source_lane * lane_stride))
            src_addr = "x12"
        lines.extend(load_value_from_address(src_addr, lane_type, 9))
        lines.extend(store_value_to_address(dst_addr, lane_type, 9))
    return lines


def _emit_vector_cast(
    func: ParsedFunction,
    op: str,
    dest: str,
    src_type,
    value: str,
    dst_type,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    src_lane_type, src_lane_stride = _vector_lane_stride(src_type)
    dst_lane_type, dst_lane_stride = _vector_lane_stride(dst_type)
    if src_type.count != dst_type.count:
        raise BackendUnavailable(
            f"self backend vector cast currently requires same lane count in {func.name!r}: "
            f"{src_type.describe()} -> {dst_type.describe()}"
        )
    dest_slot = func.value_slots[dest]
    lines = _emit_vector_storage_address(
        func, value, src_type, "x15", scratch_reg="x14"
    )
    lines.extend(
        _emit_slot_base_address_nonclobbering(dest_slot, "x17", scratch_reg="x14")
    )
    for lane_index in range(src_type.count):
        src_addr = "x15"
        dst_addr = "x17"
        if lane_index:
            lines.extend(emit_add_offset("x12", "x15", lane_index * src_lane_stride))
            lines.extend(emit_add_offset("x14", "x17", lane_index * dst_lane_stride))
            src_addr = "x12"
            dst_addr = "x14"
        lines.extend(load_value_from_address(src_addr, src_lane_type, 9))
        lines.extend(emit_cast(op, src_lane_type, dst_lane_type))
        lines.extend(store_value_to_address(dst_addr, dst_lane_type, 10))
    return lines


def _emit_vector_select(
    func: ParsedFunction,
    dest: str,
    value_type,
    cond: str,
    true_value: str,
    false_value: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    lane_type, lane_stride = _vector_lane_stride(value_type)
    cond_type = func.value_types.get(cond)
    if (
        cond_type is None
        or not cond_type.is_array
        or cond_type.elem is None
        or not cond_type.elem.is_int
        or cond_type.elem.width != 1
        or cond_type.count != value_type.count
    ):
        raise BackendUnavailable(
            f"self backend vector select expects matching vector-i1 condition in {func.name!r}: {cond}"
        )
    cond_stride = _vector_lane_stride(cond_type)[1]
    dest_slot = func.value_slots[dest]
    lines = _emit_vector_storage_address(
        func, cond, cond_type, "x15", scratch_reg="x12"
    )
    true_addr: str | None = None
    false_addr: str | None = None
    if true_value in func.value_slots or true_value in func.alloca_slots:
        lines.extend(
            _emit_vector_storage_address(
                func, true_value, value_type, "x16", scratch_reg="x12"
            )
        )
        true_addr = "x16"
    if false_value in func.value_slots or false_value in func.alloca_slots:
        lines.extend(
            _emit_vector_storage_address(
                func, false_value, value_type, "x17", scratch_reg="x12"
            )
        )
        false_addr = "x17"
    lines.extend(
        _emit_slot_base_address_nonclobbering(dest_slot, "x14", scratch_reg="x12")
    )
    for lane_index in range(value_type.count):
        cond_addr = "x15"
        dst_addr = "x14"
        if lane_index:
            lines.extend(emit_add_offset("x11", "x15", lane_index * cond_stride))
            cond_addr = "x11"
            lines.extend(emit_add_offset("x13", "x14", lane_index * lane_stride))
            dst_addr = "x13"
        lines.extend(load_value_from_address(cond_addr, cond_type.elem, 9))
        lines.extend(
            _emit_vector_lane_value(
                func,
                true_value,
                value_type,
                lane_type,
                lane_index,
                10,
                true_addr,
                module_symbols,
            )
        )
        lines.extend(
            _emit_vector_lane_value(
                func,
                false_value,
                value_type,
                lane_type,
                lane_index,
                11,
                false_addr,
                module_symbols,
            )
        )
        lines.append("  cmp w9, #0")
        lines.append(
            f"  csel {reg_name(lane_type, 12)}, {reg_name(lane_type, 10)}, {reg_name(lane_type, 11)}, ne"
        )
        lines.extend(store_value_to_address(dst_addr, lane_type, 12))
    return lines


def _emit_aggregate_select(
    func: ParsedFunction,
    dest: str,
    value_type,
    cond: str,
    true_value: str,
    false_value: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    dest_slot = func.value_slots[dest]
    cond_type = func.value_types.get(cond, I1)
    if not cond_type.is_int:
        raise BackendUnavailable(
            f"self backend aggregate select expects scalar integer condition in {func.name!r}: {cond}"
        )
    lines = materialize_value(func, cond, cond_type, 9, module_symbols)
    lines.extend(
        materialize_aggregate_storage_address(func, true_value, value_type, "x10")
    )
    lines.extend(
        materialize_aggregate_storage_address(func, false_value, value_type, "x11")
    )
    lines.extend(emit_slot_base_address(dest_slot, "x12"))
    lines.append("  cmp w9, #0")
    lines.append("  csel x13, x10, x11, ne")
    lines.extend(copy_address_to_address("x13", "x12", value_type.slot_size))
    return lines


def _emit_vector_icmp(
    func: ParsedFunction,
    cond: str,
    dest: str,
    value_type,
    lhs: str,
    rhs: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    lane_type, lane_stride = _vector_lane_stride(value_type)
    dest_type = func.value_types.get(dest)
    if (
        dest_type is None
        or not dest_type.is_array
        or dest_type.elem is None
        or not dest_type.elem.is_int
        or dest_type.elem.width != 1
        or dest_type.count != value_type.count
    ):
        raise BackendUnavailable(
            f"self backend vector icmp expects vector-i1 destination in {func.name!r}: {dest}"
        )
    lines: list[str] = []
    lhs_addr: str | None = None
    rhs_addr: str | None = None
    if lhs in func.value_slots or lhs in func.alloca_slots:
        lines.extend(
            _emit_vector_storage_address(
                func, lhs, value_type, "x15", scratch_reg="x14"
            )
        )
        lhs_addr = "x15"
    if rhs in func.value_slots or rhs in func.alloca_slots:
        lines.extend(
            _emit_vector_storage_address(
                func, rhs, value_type, "x16", scratch_reg="x14"
            )
        )
        rhs_addr = "x16"
    lines.extend(
        _emit_slot_base_address_nonclobbering(
            func.value_slots[dest], "x17", scratch_reg="x14"
        )
    )
    for lane_index in range(value_type.count):
        dst_addr = "x17"
        if lane_index:
            lines.extend(emit_add_offset("x14", "x17", lane_index))
            dst_addr = "x14"
        lines.extend(
            _emit_vector_lane_value(
                func,
                lhs,
                value_type,
                lane_type,
                lane_index,
                9,
                lhs_addr,
                module_symbols,
            )
        )
        lines.extend(
            _emit_vector_lane_value(
                func,
                rhs,
                value_type,
                lane_type,
                lane_index,
                10,
                rhs_addr,
                module_symbols,
            )
        )
        if cond in {"slt", "sle", "sgt", "sge"}:
            lines.extend(sign_extend_int_reg(lane_type, reg_name(lane_type, 9)))
            lines.extend(sign_extend_int_reg(lane_type, reg_name(lane_type, 10)))
        lines.append(f"  cmp {reg_name(lane_type, 9)}, {reg_name(lane_type, 10)}")
        lines.append(f"  cset w11, {aarch64_cc(cond)}")
        lines.extend(store_value_to_address(dst_addr, I1, 11))
    return lines


def _emit_vector_fcmp(
    func: ParsedFunction,
    cond: str,
    dest: str,
    value_type,
    lhs: str,
    rhs: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    lane_type, _lane_stride = _vector_lane_stride(value_type)
    if not lane_type.is_fp:
        raise BackendUnavailable(
            f"self backend vector fcmp expects fp vector lanes in {func.name!r}: {value_type.describe()}"
        )
    dest_type = func.value_types.get(dest)
    if (
        dest_type is None
        or not dest_type.is_array
        or dest_type.elem is None
        or not dest_type.elem.is_int
        or dest_type.elem.width != 1
        or dest_type.count != value_type.count
    ):
        raise BackendUnavailable(
            f"self backend vector fcmp expects vector-i1 destination in {func.name!r}: {dest}"
        )
    lines: list[str] = []
    lhs_addr: str | None = None
    rhs_addr: str | None = None
    if lhs in func.value_slots or lhs in func.alloca_slots:
        lines.extend(
            _emit_vector_storage_address(
                func, lhs, value_type, "x15", scratch_reg="x14"
            )
        )
        lhs_addr = "x15"
    if rhs in func.value_slots or rhs in func.alloca_slots:
        lines.extend(
            _emit_vector_storage_address(
                func, rhs, value_type, "x16", scratch_reg="x14"
            )
        )
        rhs_addr = "x16"
    lines.extend(
        _emit_slot_base_address_nonclobbering(
            func.value_slots[dest], "x17", scratch_reg="x14"
        )
    )
    for lane_index in range(value_type.count):
        dst_addr = "x17"
        if lane_index:
            lines.extend(emit_add_offset("x14", "x17", lane_index))
            dst_addr = "x14"
        lines.extend(
            _emit_vector_lane_value(
                func,
                lhs,
                value_type,
                lane_type,
                lane_index,
                9,
                lhs_addr,
                module_symbols,
            )
        )
        lines.extend(
            _emit_vector_lane_value(
                func,
                rhs,
                value_type,
                lane_type,
                lane_index,
                10,
                rhs_addr,
                module_symbols,
            )
        )
        lines.append(f"  fcmp {reg_name(lane_type, 9)}, {reg_name(lane_type, 10)}")
        lines.extend(emit_fcmp_result(cond))
        lines.extend(store_value_to_address(dst_addr, I1, 11))
    return lines


def _emit_insertvalue(
    func: ParsedFunction,
    dest: str,
    aggregate_type,
    aggregate_value: str,
    elem_type,
    elem_value: str,
    offset: int,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    dest_slot = func.value_slots[dest]
    if aggregate_value in {"zeroinitializer", "poison", "undef"}:
        lines = zero_slot(dest_slot)
    elif aggregate_fits_reg_abi(aggregate_type):
        lines = materialize_value(
            func, aggregate_value, aggregate_type, 9, module_symbols
        )
        lines.extend(store_value_regs_to_slot(dest_slot, 9))
    elif is_aggregate_literal_value(aggregate_value):
        lines = emit_slot_base_address(dest_slot, "x15")
        lines.extend(
            store_large_aggregate_literal_to_address(
                aggregate_type,
                aggregate_value,
                "x15",
                module_symbols=module_symbols,
            )
        )
    else:
        lines = copy_large_aggregate_value_to_slot(
            func,
            aggregate_value,
            aggregate_type,
            dest_slot,
            module_symbols=module_symbols,
        )
    if elem_type.is_array or elem_type.is_struct:
        lines.extend(emit_slot_base_address(dest_slot, "x15"))
        if offset:
            lines.extend(emit_add_offset("x15", "x15", offset))
        if elem_value in {"zeroinitializer", "poison", "undef"}:
            lines.extend(zero_address("x15", elem_type.slot_size))
            return lines
        if is_aggregate_literal_value(elem_value):
            lines.extend(
                store_large_aggregate_literal_to_address(
                    elem_type,
                    elem_value,
                    "x15",
                    module_symbols=module_symbols,
                )
            )
            return lines
        lines.extend(
            materialize_aggregate_storage_address(func, elem_value, elem_type, "x16")
        )
        lines.extend(copy_address_to_address("x16", "x15", elem_type.slot_size))
        return lines
    lines.extend(materialize_value(func, elem_value, elem_type, 9, module_symbols))
    lines.extend(emit_slot_base_address(dest_slot, "x15"))
    if offset:
        lines.extend(emit_add_offset("x15", "x15", offset))
    lines.extend(store_value_to_address("x15", elem_type, 9))
    return lines


def emit_compute_instruction(
    func: ParsedFunction,
    kind: str,
    data: tuple,
    module_symbols: PreparedModuleSymbols,
) -> list[str] | None:
    if kind == "binop":
        op, dest, value_type, lhs, rhs = data
        if dest not in func.value_slots:
            return []
        if (
            value_type.is_array
            and value_type.elem is not None
            and value_type.elem.is_int
        ):
            return _emit_vector_int_binop(
                func, op, dest, value_type, lhs, rhs, module_symbols
            )
        if value_type.is_array or value_type.is_struct:
            lines = materialize_value(func, lhs, value_type, 9, module_symbols)
            lines.extend(materialize_value(func, rhs, value_type, 11, module_symbols))
            lines.extend(
                emit_aggregate_bitwise_binop(
                    op,
                    value_type,
                    lhs_start=9,
                    rhs_start=11,
                    dest_start=13,
                )
            )
            lines.extend(store_value_regs_to_slot(func.value_slots[dest], 13))
            return lines
        lines = materialize_value(func, lhs, value_type, 9, module_symbols)
        lines.extend(materialize_value(func, rhs, value_type, 10, module_symbols))
        if value_type.is_int and op in {"sdiv", "srem", "ashr"}:
            lines.extend(sign_extend_int_reg(value_type, reg_name(value_type, 9)))
            lines.extend(sign_extend_int_reg(value_type, reg_name(value_type, 10)))
        lines.extend(emit_binop(op, value_type))
        lines.extend(
            store_reg_to_slot(reg_name(value_type, 11), func.value_slots[dest])
        )
        return lines

    if kind == "fbinop":
        op, dest, value_type, lhs, rhs = data
        if dest not in func.value_slots:
            return []
        lines = materialize_value(func, lhs, value_type, 9, module_symbols)
        lines.extend(materialize_value(func, rhs, value_type, 10, module_symbols))
        lines.extend(emit_fbinop(op, value_type))
        lines.extend(
            store_reg_to_slot(reg_name(value_type, 11), func.value_slots[dest])
        )
        return lines

    if kind == "fneg":
        dest, value_type, value = data
        if dest not in func.value_slots:
            return []
        lines = materialize_value(func, value, value_type, 9, module_symbols)
        lines.append(f"  fneg {reg_name(value_type, 11)}, {reg_name(value_type, 9)}")
        lines.extend(
            store_reg_to_slot(reg_name(value_type, 11), func.value_slots[dest])
        )
        return lines

    if kind == "icmp":
        cond, dest, value_type, lhs, rhs = data
        if dest not in func.value_slots:
            return []
        if (
            value_type.is_array
            and value_type.elem is not None
            and value_type.elem.is_int
        ):
            return _emit_vector_icmp(
                func, cond, dest, value_type, lhs, rhs, module_symbols
            )
        lines = materialize_value(func, lhs, value_type, 9, module_symbols)
        lines.extend(materialize_value(func, rhs, value_type, 10, module_symbols))
        if value_type.is_int and cond in {"slt", "sle", "sgt", "sge"}:
            lines.extend(sign_extend_int_reg(value_type, reg_name(value_type, 9)))
            lines.extend(sign_extend_int_reg(value_type, reg_name(value_type, 10)))
        lines.append(f"  cmp {reg_name(value_type, 9)}, {reg_name(value_type, 10)}")
        lines.append(f"  cset w11, {aarch64_cc(cond)}")
        lines.extend(store_reg_to_slot("w11", func.value_slots[dest]))
        return lines

    if kind == "fcmp":
        cond, dest, value_type, lhs, rhs = data
        if dest not in func.value_slots:
            return []
        if (
            value_type.is_array
            and value_type.elem is not None
            and value_type.elem.is_fp
        ):
            return _emit_vector_fcmp(
                func, cond, dest, value_type, lhs, rhs, module_symbols
            )
        lines = materialize_value(func, lhs, value_type, 9, module_symbols)
        lines.extend(materialize_value(func, rhs, value_type, 10, module_symbols))
        lines.append(f"  fcmp {reg_name(value_type, 9)}, {reg_name(value_type, 10)}")
        lines.extend(emit_fcmp_result(cond))
        lines.extend(store_reg_to_slot("w11", func.value_slots[dest]))
        return lines

    if kind == "cast":
        op, dest, src_type, value, dst_type = data
        if dest not in func.value_slots:
            return []
        if (
            src_type.is_array
            and dst_type.is_array
            and src_type.elem is not None
            and dst_type.elem is not None
            and (src_type.elem.is_int or src_type.elem.is_fp)
            and (dst_type.elem.is_int or dst_type.elem.is_fp)
        ):
            return _emit_vector_cast(
                func, op, dest, src_type, value, dst_type, module_symbols
            )
        lines = materialize_value(func, value, src_type, 9, module_symbols)
        lines.extend(emit_cast(op, src_type, dst_type))
        lines.extend(store_reg_to_slot(reg_name(dst_type, 10), func.value_slots[dest]))
        return lines

    if kind == "select":
        dest, value_type, cond, true_value, false_value = data
        if dest not in func.value_slots:
            return []
        cond_type = func.value_types.get(cond, I1)
        if (
            value_type.is_array
            and value_type.elem is not None
            and value_type.elem.is_int
            and cond_type.is_array
        ):
            return _emit_vector_select(
                func, dest, value_type, cond, true_value, false_value, module_symbols
            )
        if value_type.is_array or value_type.is_struct:
            return _emit_aggregate_select(
                func, dest, value_type, cond, true_value, false_value, module_symbols
            )
        lines = materialize_value(func, true_value, value_type, 10, module_symbols)
        lines.extend(
            materialize_value(func, false_value, value_type, 11, module_symbols)
        )
        lines.extend(materialize_value(func, cond, I1, 9, module_symbols))
        lines.append("  cmp w9, #0")
        if value_type.is_fp:
            lines.append(
                f"  fcsel {reg_name(value_type, 12)}, {reg_name(value_type, 10)}, {reg_name(value_type, 11)}, ne"
            )
        else:
            lines.append(
                f"  csel {reg_name(value_type, 12)}, {reg_name(value_type, 10)}, {reg_name(value_type, 11)}, ne"
            )
        lines.extend(
            store_reg_to_slot(reg_name(value_type, 12), func.value_slots[dest])
        )
        return lines

    if kind == "freeze":
        dest, value_type, value = data
        if dest not in func.value_slots:
            return []
        if value_type.is_array or value_type.is_struct:
            raise BackendUnavailable(
                f"self backend freeze aggregate result not translated yet in {func.name!r}: "
                f"{value_type.describe()}"
            )
        lines = materialize_value(func, value, value_type, 10, module_symbols)
        lines.extend(
            store_reg_to_slot(reg_name(value_type, 10), func.value_slots[dest])
        )
        return lines

    if kind == "insertelement":
        dest, vector_type, vector_value, elem_type, elem_value, index_value = data
        return _emit_insertelement(
            func,
            dest,
            vector_type,
            vector_value,
            elem_type,
            elem_value,
            index_value,
            module_symbols,
        )

    if kind == "shufflevector":
        dest, vector_type, lhs, rhs, mask_type, mask_value = data
        return _emit_shufflevector(
            func, dest, vector_type, lhs, rhs, mask_type, mask_value
        )

    if kind == "extractelement":
        dest, vector_type, vector_value, index_value, elem_type = data
        if dest not in func.value_slots:
            return []
        return _emit_extractelement(
            func, dest, vector_type, vector_value, index_value, elem_type
        )

    if kind == "extractvalue":
        dest, aggregate_type, value, _indices, result_type, offset = data
        if dest not in func.value_slots:
            return []
        if is_aggregate_literal_value(value):
            literal_bytes = aggregate_literal_to_bytes(aggregate_type, value)
            field_bytes = literal_bytes[offset : offset + result_type.slot_size]
            field_value = str(int.from_bytes(field_bytes, "little"))
            lines = materialize_value(
                func, field_value, result_type, 10, module_symbols
            )
            lines.extend(store_value_regs_to_slot(func.value_slots[dest], 10))
            return lines
        source_slot = func.value_slots.get(value)
        if source_slot is None:
            raise BackendUnavailable(
                f"self backend extractvalue source not materialized in {func.name!r}: {value}"
            )
        lines = emit_slot_base_address(source_slot, "x12")
        if offset:
            lines.extend(emit_add_offset("x12", "x12", offset))
        if result_type.is_array or result_type.is_struct:
            lines.extend(copy_address_to_slot("x12", func.value_slots[dest]))
            return lines
        lines.extend(load_value_from_address("x12", result_type, 10))
        lines.extend(store_value_regs_to_slot(func.value_slots[dest], 10))
        return lines

    if kind == "insertvalue":
        (
            dest,
            aggregate_type,
            aggregate_value,
            elem_type,
            elem_value,
            _indices,
            offset,
        ) = data
        if dest not in func.value_slots:
            return []
        return _emit_insertvalue(
            func,
            dest,
            aggregate_type,
            aggregate_value,
            elem_type,
            elem_value,
            offset,
            module_symbols,
        )

    if kind == "va_arg":
        dest, ap_type, ap, value_type = data
        return emit_va_arg(func, dest, ap_type, ap, value_type, module_symbols)

    if kind == "gep":
        dest, base_type, _ptr_type, ptr_value, indices = data
        if dest not in func.value_slots:
            return []
        lines = materialize_pointer(func, ptr_value, 9, module_symbols)
        lines.extend(emit_gep_offset(func, base_type, indices, module_symbols))
        lines.extend(store_reg_to_slot("x11", func.value_slots[dest]))
        return lines

    if kind == "call":
        dest, ret_type, callee, is_indirect, args, fixed_arg_count, is_vararg_call = (
            data
        )
        return emit_call_instruction(
            func,
            dest,
            ret_type,
            callee,
            is_indirect,
            args,
            fixed_arg_count,
            is_vararg_call,
            module_symbols,
        )

    return None
