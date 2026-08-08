from __future__ import annotations

from . import BackendUnavailable
from .self_backend_analysis import value_has_uses
from .self_backend_aarch64_darwin_abi import aggregate_fits_reg_abi, reg_name
from .self_backend_aarch64_darwin_materialize import (
    copy_large_aggregate_value_to_slot,
    materialize_pointer,
    materialize_value,
    store_large_aggregate_literal_to_address,
)
from .self_backend_aarch64_darwin_regalloc import commit_allocated_scalar_result
from .self_backend_aarch64_darwin_slots import (
    copy_address_to_slot,
    copy_slot_to_address,
    copy_slot_to_slot,
    emit_slot_base_address,
    load_slot_to_value_regs,
    load_value_from_address,
    store_value_regs_to_slot,
    store_value_to_address,
    zero_address,
    zero_slot,
)
from .self_backend_aarch64_darwin_symbols import sanitize_label
from .self_backend_ir import (
    ParsedFunction,
    SlotInfo,
    TypeDesc,
    aggregate_member_info,
)
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import is_aggregate_literal_value


def _atomic_width_check(
    func: ParsedFunction,
    kind: str,
    value_type: TypeDesc,
    allowed: tuple = ("i32", "i64"),
) -> None:
    if value_type.describe() not in allowed:
        raise BackendUnavailable(
            f"self backend {kind} supports only {'/'.join(allowed)} operands in "
            f"{func.name!r}: {value_type.describe()}"
        )


_ATOMIC_RMW_COMPUTE = {
    "add": "add",
    "sub": "sub",
    "and": "and",
    "or": "orr",
}


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

    if kind == "load_atomic":
        dest, value_type, _ptr_type, ptr_name, ordering = data
        if dest not in func.value_slots:
            return []
        _atomic_width_check(func, kind, value_type)
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        load_op = "ldar" if ordering in ("acquire", "seq_cst") else "ldr"
        lines.append(f"  {load_op} {reg_name(value_type, 10)}, [x9]")
        lines.extend(store_value_regs_to_slot(func.value_slots[dest], 10))
        return lines

    if kind == "store_atomic":
        value_type, value, _ptr_type, ptr_name, ordering = data
        _atomic_width_check(func, kind, value_type, allowed=("i8", "i32", "i64"))
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        lines.extend(materialize_value(func, value, value_type, 10, module_symbols))
        store_op = "stlr" if ordering in ("release", "seq_cst") else "str"
        if value_type.describe() == "i8":
            store_op += "b"
        lines.append(f"  {store_op} {reg_name(value_type, 10)}, [x9]")
        return lines

    if kind == "atomicrmw":
        dest, op, _ptr_type, ptr_name, value_type, value, _ordering = data
        if value_type.describe() == "i8" and op != "xchg":
            raise BackendUnavailable(
                f"self backend atomicrmw i8 supports only xchg (byte flags) in "
                f"{func.name!r}: {op}"
            )
        _atomic_width_check(func, kind, value_type, allowed=("i8", "i32", "i64"))
        # ldaxr/stlxr is acquire+release on every iteration — always at least
        # as strong as the requested ordering, and it is what LLVM itself
        # emits for atomicrmw at -O0 on AArch64 without LSE.
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        lines.extend(materialize_value(func, value, value_type, 10, module_symbols))
        r_val = reg_name(value_type, 10)
        r_old = reg_name(value_type, 11)
        r_new = reg_name(value_type, 12)
        ex_suffix = "b" if value_type.describe() == "i8" else ""
        label = "Lat_" + sanitize_label(func.name) + "_" + sanitize_label(dest)
        lines.append(f"{label}:")
        lines.append(f"  ldaxr{ex_suffix} {r_old}, [x9]")
        if op == "xchg":
            lines.append(f"  mov {r_new}, {r_val}")
        else:
            lines.append(f"  {_ATOMIC_RMW_COMPUTE[op]} {r_new}, {r_old}, {r_val}")
        lines.append(f"  stlxr{ex_suffix} w13, {r_new}, [x9]")
        lines.append(f"  cbnz w13, {label}")
        if dest in func.value_slots:
            lines.extend(store_value_regs_to_slot(func.value_slots[dest], 11))
        return lines

    if kind == "cmpxchg":
        (
            dest,
            pair_type,
            _ptr_type,
            ptr_name,
            value_type,
            expected,
            desired,
            _success,
            _failure,
        ) = data
        _atomic_width_check(func, kind, value_type)
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        lines.extend(materialize_value(func, expected, value_type, 10, module_symbols))
        lines.extend(materialize_value(func, desired, value_type, 11, module_symbols))
        r_exp = reg_name(value_type, 10)
        r_des = reg_name(value_type, 11)
        r_old = reg_name(value_type, 12)
        base = "Lat_" + sanitize_label(func.name) + "_" + sanitize_label(dest)
        lines.append(f"{base}_retry:")
        lines.append(f"  ldaxr {r_old}, [x9]")
        lines.append(f"  cmp {r_old}, {r_exp}")
        lines.append(f"  b.ne {base}_fail")
        lines.append(f"  stlxr w13, {r_des}, [x9]")
        lines.append(f"  cbnz w13, {base}_retry")
        lines.append(f"  b {base}_done")
        lines.append(f"{base}_fail:")
        lines.append("  clrex")
        lines.append(f"{base}_done:")
        lines.append("  cset w14, eq")
        if dest in func.value_slots:
            slot = func.value_slots[dest]
            lines.extend(emit_slot_base_address(slot, "x15"))
            lines.append(f"  str {r_old}, [x15]")
            _flag_type, flag_offset = aggregate_member_info(pair_type, (1,))
            lines.append(f"  strb w14, [x15, #{flag_offset}]")
        return lines

    if kind == "fence":
        return ["  dmb ish"]

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
        allocated_lines = None
        if value_type.is_int or value_type.is_ptr:
            allocated_lines = commit_allocated_scalar_result(
                func, dest, value_type, reg_name(value_type, 10)
            )
        if allocated_lines is not None:
            lines.extend(allocated_lines)
        else:
            lines.extend(store_value_regs_to_slot(func.value_slots[dest], 10))
        return lines

    return None
