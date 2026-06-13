from __future__ import annotations

from . import BackendUnavailable
from .self_backend_aarch64_darwin_regs import emit_add_offset, emit_const_to_reg
from .self_backend_aarch64_darwin_slots import load_slot_to_reg
from .self_backend_aarch64_darwin_symbols import asm_symbol
from .self_backend_aarch64_darwin_abi import reg_name
from .self_backend_ir import ParsedFunction, TypeDesc
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import const_int_from_value


def materialize_global_address(
    name: str,
    reg: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    symbol = asm_symbol(name, module_symbols)
    if name not in module_symbols.defined_symbols:
        return [
            f"  adrp {reg}, {symbol}@GOTPAGE",
            f"  ldr {reg}, [{reg}, {symbol}@GOTPAGEOFF]",
        ]
    return [
        f"  adrp {reg}, {symbol}@PAGE",
        f"  add {reg}, {reg}, {symbol}@PAGEOFF",
    ]


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
    if index_value not in func.value_slots:
        raise BackendUnavailable(f"self backend does not know getelementptr index value {index_value!r}")
    index_slot = func.value_slots[index_value]
    lines = load_slot_to_reg(index_slot, reg_name(index_slot.type, 10))
    if index_slot.type.is_ptr:
        # A pointer-typed index SSA value carries LLVM's implicit ``ptrtoint``
        # semantics: its 64-bit bit pattern is the index. load_slot_to_reg
        # already loaded the full pointer into x10 (reg_name gives an x-reg for
        # pointer types), so no sign extension is needed.
        return lines
    if index_slot.type.bits < 64:
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
            return ["  mov x11, x9"]
        raise BackendUnavailable("self backend cannot index into zero-sized element type")
    const_index = const_int_from_value(index_value)
    if const_index is not None:
        offset = const_index * elem_size
        return emit_add_offset("x11", "x9", offset)

    lines = materialize_index_to_x10(func, index_value, module_symbols)
    if elem_size == 1:
        lines.append("  add x11, x9, x10")
        return lines
    if elem_size in (2, 4, 8, 16):
        shift = {2: 1, 4: 2, 8: 3, 16: 4}[elem_size]
        lines.append(f"  add x11, x9, x10, lsl #{shift}")
        return lines
    lines.extend(emit_const_to_reg(TypeDesc("int", 64), "x12", elem_size))
    lines.extend(
        [
            "  mul x10, x10, x12",
            "  add x11, x9, x10",
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
        lines.append("  mov x9, x11")
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
