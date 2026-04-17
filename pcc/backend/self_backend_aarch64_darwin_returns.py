from __future__ import annotations

from . import BackendUnavailable
from .self_backend_aarch64_darwin_abi import aggregate_returned_indirect
from .self_backend_aarch64_darwin_materialize import (
    materialize_aggregate_storage_address,
    materialize_value,
    store_large_aggregate_literal_to_address,
)
from .self_backend_aarch64_darwin_slots import (
    copy_address_to_address,
    load_slot_to_reg,
    zero_address,
)
from .self_backend_aarch64_darwin_terminators import emit_epilogue
from .self_backend_ir import ParsedFunction, TypeDesc
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import is_aggregate_literal_value


def emit_return_terminator(
    func: ParsedFunction,
    *,
    ret_type: TypeDesc,
    value: str,
    module_symbols: PreparedModuleSymbols,
) -> list[str]:
    if aggregate_returned_indirect(ret_type):
        if func.hidden_sret_slot is None:
            raise BackendUnavailable(
                f"self backend missing hidden sret slot for large aggregate return in {func.name!r}"
            )
        lines = load_slot_to_reg(func.hidden_sret_slot, "x12")
        if value == "zeroinitializer":
            lines.extend(zero_address("x12", ret_type.slot_size))
            lines.extend(emit_epilogue(func))
            return lines
        if is_aggregate_literal_value(value):
            lines.extend(
                store_large_aggregate_literal_to_address(
                    ret_type,
                    value,
                    "x12",
                    module_symbols=module_symbols,
                )
            )
            lines.extend(emit_epilogue(func))
            return lines
        lines.extend(materialize_aggregate_storage_address(func, value, ret_type, "x13"))
        lines.extend(copy_address_to_address("x13", "x12", ret_type.slot_size))
        lines.extend(emit_epilogue(func))
        return lines
    lines = materialize_value(func, value, ret_type, 0, module_symbols)
    lines.extend(emit_epilogue(func))
    return lines
