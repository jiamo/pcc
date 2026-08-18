from __future__ import annotations

from . import BackendUnavailable
from .self_backend_aarch64_darwin_mem import (
    emitted_addsub_register_line,
    emitted_global_address_lines,
    emitted_move_register_line,
)
from .self_backend_aarch64_darwin_regs import emit_add_offset, emit_const_to_reg
from .self_backend_aarch64_darwin_regalloc import allocated_register_name
from .self_backend_aarch64_darwin_slots import load_value_slot_to_reg
from .self_backend_aarch64_darwin_symbols import asm_symbol
from .self_backend_aarch64_darwin_abi import reg_name
from .self_backend_ir import (
    ParsedFunction,
    TypeDesc,
    parsed_function_has_value_slot,
    parsed_function_value_slot_type,
)
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import const_int_from_value
from .self_backend_value_arena import CompilerInt2, CompilerInt4
from .self_backend_kernel import (
    TYPE_KIND_ARRAY,
    TYPE_KIND_STRUCT,
    IndexedFunctionKernel,
)


def materialize_global_address(
    name: str,
    reg: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    symbol = asm_symbol(name, module_symbols)
    if name not in module_symbols.defined_symbols:
        return emitted_global_address_lines(reg, symbol, True)
    return emitted_global_address_lines(reg, symbol, False)


def materialize_index_to_x10(
    func: ParsedFunction,
    index_value: str,
    module_symbols: PreparedModuleSymbols | None = None,
) -> list[str]:
    const_index = const_int_from_value(index_value)
    if const_index is not None:
        return emit_const_to_reg(TypeDesc("int", 64), "x10", const_index)
    if index_value.startswith("@"):
        # A global symbol used as a getelementptr index carries LLVM's implicit
        # ``ptrtoint`` semantics: the index equals the *address* of the symbol,
        # so materialize the symbol address (64-bit) straight into x10 rather
        # than dereferencing it. This mirrors materialize_global_address.
        if module_symbols is None:
            raise BackendUnavailable(
                "self backend cannot resolve symbol-valued getelementptr index "
                f"{index_value!r} without module symbols"
            )
        return materialize_global_address(index_value[1:], "x10", module_symbols)
    if not parsed_function_has_value_slot(func, index_value):
        raise BackendUnavailable(f"self backend does not know getelementptr index value {index_value!r}")
    index_type = parsed_function_value_slot_type(func, index_value)
    index_reg = reg_name(index_type, 10)
    allocated_reg = allocated_register_name(func, index_value, index_type)
    if allocated_reg is None:
        lines = load_value_slot_to_reg(func, index_value, index_reg)
    elif allocated_reg == index_reg:
        lines = []
    else:
        lines = [emitted_move_register_line(index_reg, allocated_reg)]
    if index_type.is_ptr:
        # A pointer-typed index SSA value carries LLVM's implicit ``ptrtoint``
        # semantics: its 64-bit bit pattern is the index. load_slot_to_reg
        # already loaded the full pointer into x10 (reg_name gives an x-reg for
        # pointer types), so no sign extension is needed.
        return lines
    if index_type.bits < 64:
        lines.append("  sxtw x10, w10")
    return lines


def emit_indexed_pointer_add(
    func: ParsedFunction,
    index_value: str,
    elem_size: int,
    module_symbols: PreparedModuleSymbols | None = None,
) -> list[str]:
    if elem_size == 0:
        const_index = const_int_from_value(index_value)
        if const_index == 0:
            return [emitted_move_register_line("x11", "x9")]
        raise BackendUnavailable("self backend cannot index into zero-sized element type")
    const_index = const_int_from_value(index_value)
    if const_index is not None:
        offset = const_index * elem_size
        return emit_add_offset("x11", "x9", offset)

    lines = materialize_index_to_x10(func, index_value, module_symbols)
    if elem_size == 1:
        lines.append(emitted_addsub_register_line("add", "x11", "x9", "x10"))
        return lines
    if elem_size in (2, 4, 8, 16):
        shift = {2: 1, 4: 2, 8: 3, 16: 4}[elem_size]
        lines.append(f"  add x11, x9, x10, lsl #{shift}")
        return lines
    lines.extend(emit_const_to_reg(TypeDesc("int", 64), "x12", elem_size))
    lines.extend(
        [
            "  mul x10, x10, x12",
            emitted_addsub_register_line("add", "x11", "x9", "x10"),
        ]
    )
    return lines


def emit_gep_offset(
    func: ParsedFunction,
    base_type: TypeDesc,
    indices: tuple[tuple[TypeDesc, str], ...],
    module_symbols: PreparedModuleSymbols | None = None,
) -> list[str]:
    if not indices:
        raise BackendUnavailable("self backend getelementptr requires at least one index")
    lines = emit_indexed_pointer_add(
        func, indices[0][1], base_type.slot_size, module_symbols
    )
    current = base_type
    for _index_type, index_value in indices[1:]:
        lines.append(emitted_move_register_line("x9", "x11"))
        if current.is_array:
            assert current.elem is not None
            current = current.elem
            lines.extend(
                emit_indexed_pointer_add(
                    func, index_value, current.slot_size, module_symbols
                )
            )
            continue
        if current.is_struct:
            struct_type = current
            field_index = const_int_from_value(index_value)
            if field_index is None:
                raise BackendUnavailable(
                    "self backend struct getelementptr currently requires constant field indices"
                )
            current = struct_type.field_type(field_index)
            offset = struct_type.field_offset(field_index)
            lines.extend(emit_add_offset("x11", "x9", offset))
            continue
        raise BackendUnavailable(
            f"self backend cannot index into scalar pointee {current.describe()} with more getelementptr indices"
        )
    return lines


def emit_gep_offset_indexed(
    func: ParsedFunction,
    kernel: IndexedFunctionKernel,
    record_id: int,
    module_symbols: PreparedModuleSymbols | None = None,
) -> list[str]:
    header: CompilerInt4 = kernel.gep_header(record_id)
    span: CompilerInt4 = kernel.gep_span(record_id)
    if span.first <= 0:
        raise BackendUnavailable(
            "self backend getelementptr requires at least one index"
        )
    base_type_id = header.first
    base_span: CompilerInt4 = kernel.type_span(base_type_id)
    first: CompilerInt2 = kernel.gep_index(header.fourth)
    first_value = (
        kernel.value_name(first.second)
        if first.second >= 0
        else kernel.call_texts[-first.second - 1]
    )
    lines = emit_indexed_pointer_add(
        func,
        first_value,
        base_span.third,
        module_symbols,
    )
    current_type_id = base_type_id
    index = 1
    while index < span.first:
        raw: CompilerInt2 = kernel.gep_index(header.fourth + index)
        index_value = (
            kernel.value_name(raw.second)
            if raw.second >= 0
            else kernel.call_texts[-raw.second - 1]
        )
        lines.append(emitted_move_register_line("x9", "x11"))
        current_header: CompilerInt4 = kernel.type_header(current_type_id)
        if current_header.first == TYPE_KIND_ARRAY:
            if current_header.fourth < 0:
                raise BackendUnavailable(
                    "self backend getelementptr array element type is unavailable"
                )
            current_type_id = current_header.fourth
            child_span: CompilerInt4 = kernel.type_span(current_type_id)
            lines.extend(
                emit_indexed_pointer_add(
                    func,
                    index_value,
                    child_span.third,
                    module_symbols,
                )
            )
        elif current_header.first == TYPE_KIND_STRUCT:
            field_index = const_int_from_value(index_value)
            if field_index is None:
                raise BackendUnavailable(
                    "self backend struct getelementptr currently requires constant field indices"
                )
            current_span: CompilerInt4 = kernel.type_span(current_type_id)
            if field_index < 0 or field_index >= current_span.second:
                raise BackendUnavailable(
                    "self backend struct getelementptr field index is out of range"
                )
            field_offset = 0
            cursor = 0
            field_type_id = -1
            while cursor <= field_index:
                candidate_type_id = kernel.type_field_ids.get_unchecked(
                    current_span.first + cursor
                )
                candidate_span: CompilerInt4 = kernel.type_span(
                    candidate_type_id
                )
                field_offset = (
                    (field_offset + candidate_span.fourth - 1)
                    // candidate_span.fourth
                    * candidate_span.fourth
                )
                if cursor == field_index:
                    field_type_id = candidate_type_id
                    break
                field_offset += candidate_span.third
                cursor += 1
            current_type_id = field_type_id
            lines.extend(
                emit_add_offset(
                    "x11",
                    "x9",
                    field_offset,
                )
            )
        else:
            raise BackendUnavailable(
                "self backend cannot index into scalar pointee with more getelementptr indices"
            )
        index += 1
    return lines
