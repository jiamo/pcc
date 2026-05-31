from __future__ import annotations

from . import BackendUnavailable
from .self_backend_analysis import value_has_uses
from .self_backend_aarch64_darwin_abi import aggregate_fits_reg_abi
from .self_backend_aarch64_darwin_materialize import (
    copy_large_aggregate_value_to_slot,
    materialize_pointer,
    materialize_value,
    store_large_aggregate_literal_to_address,
)
from .self_backend_aarch64_darwin_slots import (
    copy_address_to_slot,
    copy_slot_to_address,
    copy_slot_to_slot,
    load_slot_to_value_regs,
    load_value_from_address,
    store_value_regs_to_slot,
    store_value_to_address,
    zero_address,
    zero_slot,
)
from .self_backend_ir import ParsedFunction, SlotInfo, TypeDesc
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import is_aggregate_literal_value


def emit_memory_instruction(
    func: ParsedFunction,
    kind: str,
    data: tuple,
    module_symbols: PreparedModuleSymbols,
) -> list[str] | None:
    if kind == "alloca":
        return []

    if kind == "store":
        value_type, value, _ptr_type, ptr_name = data
        if value == "zeroinitializer" and (value_type.is_array or value_type.is_struct):
            if (
                ptr_name in func.alloca_slots
                and func.alloca_slots[ptr_name].allocated_type.describe() == value_type.describe()
            ):
                dest_slot = SlotInfo(func.alloca_slots[ptr_name].offset, value_type)
                return zero_slot(dest_slot)
            lines = materialize_pointer(func, ptr_name, 9, module_symbols)
            lines.extend(zero_address("x9", value_type.slot_size))
            return lines
        if (value_type.is_array or value_type.is_struct) and not aggregate_fits_reg_abi(value_type):
            if is_aggregate_literal_value(value):
                if (
                    ptr_name in func.alloca_slots
                    and func.alloca_slots[ptr_name].allocated_type.describe() == value_type.describe()
                ):
                    dest_slot = SlotInfo(func.alloca_slots[ptr_name].offset, value_type)
                    return copy_large_aggregate_value_to_slot(
                        func, value, value_type, dest_slot, module_symbols=module_symbols
                    )
                lines = materialize_pointer(func, ptr_name, 9, module_symbols)
                lines.extend(
                    store_large_aggregate_literal_to_address(
                        value_type,
                        value,
                        "x9",
                        module_symbols=module_symbols,
                    )
                )
                return lines
            if value not in func.value_slots:
                raise BackendUnavailable(
                    f"self backend can only store aggregate SSA values from stack slots right now in {func.name!r}: {value}"
                )
            source_slot = func.value_slots[value]
            if (
                ptr_name in func.alloca_slots
                and func.alloca_slots[ptr_name].allocated_type.describe() == value_type.describe()
            ):
                dest_slot = SlotInfo(func.alloca_slots[ptr_name].offset, value_type)
                return copy_slot_to_slot(source_slot, dest_slot)
            lines = materialize_pointer(func, ptr_name, 9, module_symbols)
            lines.extend(copy_slot_to_address(source_slot, "x9"))
            return lines
        if (
            ptr_name in func.alloca_slots
            and func.alloca_slots[ptr_name].allocated_type.describe() == value_type.describe()
        ):
            lines = materialize_value(func, value, value_type, 9, module_symbols)
            lines.extend(
                store_value_regs_to_slot(
                    SlotInfo(func.alloca_slots[ptr_name].offset, value_type),
                    9,
                )
            )
            return lines
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        lines.extend(materialize_value(func, value, value_type, 10, module_symbols))
        lines.extend(store_value_to_address("x9", value_type, 10))
        return lines

    if kind == "load":
        dest, value_type, _ptr_type, ptr_name = data
        if dest not in func.value_slots:
            return []
        if (value_type.is_array or value_type.is_struct) and not value_has_uses(func, dest):
            return []
        if (value_type.is_array or value_type.is_struct) and not aggregate_fits_reg_abi(value_type):
            dest_slot = func.value_slots[dest]
            if (
                ptr_name in func.alloca_slots
                and func.alloca_slots[ptr_name].allocated_type.describe() == value_type.describe()
            ):
                source_slot = SlotInfo(func.alloca_slots[ptr_name].offset, value_type)
                return copy_slot_to_slot(source_slot, dest_slot)
            lines = materialize_pointer(func, ptr_name, 9, module_symbols)
            lines.extend(copy_address_to_slot("x9", dest_slot))
            return lines
        lines: list[str] = []
        if (
            ptr_name in func.alloca_slots
            and func.alloca_slots[ptr_name].allocated_type.describe() == value_type.describe()
        ):
            lines.extend(
                load_slot_to_value_regs(
                    SlotInfo(func.alloca_slots[ptr_name].offset, value_type),
                    10,
                )
            )
        else:
            lines.extend(materialize_pointer(func, ptr_name, 9, module_symbols))
            lines.extend(load_value_from_address("x9", value_type, 10))
        lines.extend(store_value_regs_to_slot(func.value_slots[dest], 10))
        return lines

    return None
