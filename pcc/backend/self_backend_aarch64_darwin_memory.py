from __future__ import annotations

from . import BackendUnavailable
from .self_backend_analysis import value_has_uses
from .self_backend_aarch64_darwin_abi import aggregate_fits_reg_abi, reg_name
from .self_backend_aarch64_darwin_abi import reg_name_indexed
from .self_backend_aarch64_darwin_mem import (
    emitted_branch_line,
    emitted_compare_register_line,
    emitted_cset_line,
    emitted_memory_instruction_line,
    emitted_move_register_line,
)
from .self_backend_aarch64_darwin_materialize import (
    copy_large_aggregate_value_to_slot,
    materialize_pointer,
    materialize_value,
    materialize_scalar_value_indexed,
    store_large_aggregate_literal_to_address,
)
from .self_backend_aarch64_darwin_regalloc import (
    commit_allocated_scalar_result,
    commit_allocated_scalar_result_indexed,
)
from .self_backend_aarch64_darwin_slots import (
    copy_address_to_slot,
    copy_address_to_value_slot,
    copy_slot_to_address,
    copy_slot_to_address_parts,
    copy_slot_to_slot_parts,
    copy_slot_to_slot,
    emit_slot_base_address,
    emit_slot_base_address_parts,
    emit_value_slot_base_address,
    load_slot_to_value_regs_parts,
    load_slot_to_value_regs,
    load_value_from_address,
    store_value_regs_to_slot,
    store_value_regs_to_slot_parts,
    store_value_regs_to_value_slot,
    store_value_to_address,
    zero_address,
    zero_slot,
    zero_slot_parts,
)
from .self_backend_aarch64_darwin_symbols import sanitize_label
from .self_backend_ir import (
    PARSED_INSTRUCTION_KIND_ALLOCA,
    PARSED_INSTRUCTION_KIND_ATOMICRMW,
    PARSED_INSTRUCTION_KIND_CMPXCHG,
    PARSED_INSTRUCTION_KIND_FENCE,
    PARSED_INSTRUCTION_KIND_LOAD,
    PARSED_INSTRUCTION_KIND_LOAD_ATOMIC,
    PARSED_INSTRUCTION_KIND_STORE,
    PARSED_INSTRUCTION_KIND_STORE_ATOMIC,
    ParsedFunction,
    SlotInfo,
    TypeDesc,
    aggregate_member_info,
    parsed_function_has_alloca_slot,
    parsed_function_has_value_slot,
    parsed_function_alloca_slot_offset,
    parsed_function_alloca_slot_type,
    parsed_function_value_slot_offset,
    parsed_function_value_slot_type,
    _PARSED_INSTRUCTION_KIND_IDS,
)
from .self_backend_module_symbols import PreparedModuleSymbols
from .self_backend_parse import is_aggregate_literal_value
from .self_backend_value_arena import CompilerInt4
from .self_backend_kernel import (
    TYPE_KIND_FP,
    TYPE_KIND_INT,
    TYPE_KIND_PTR,
    IndexedFunctionKernel,
)


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


def _indexed_scalar_mem_load_op(type_header: CompilerInt4) -> str:
    if type_header.first == TYPE_KIND_INT and type_header.second <= 8:
        return "ldrb"
    if type_header.first == TYPE_KIND_INT and type_header.second <= 16:
        return "ldrh"
    return "ldr"


def _indexed_scalar_mem_store_op(type_header: CompilerInt4) -> str:
    if type_header.first == TYPE_KIND_INT and type_header.second <= 8:
        return "strb"
    if type_header.first == TYPE_KIND_INT and type_header.second <= 16:
        return "strh"
    return "str"


def _indexed_scalar_stack_load_op(type_header: CompilerInt4) -> str:
    if type_header.first == TYPE_KIND_INT and type_header.second <= 8:
        return "ldurb"
    if type_header.first == TYPE_KIND_INT and type_header.second <= 16:
        return "ldurh"
    return "ldur"


def _indexed_scalar_stack_store_op(type_header: CompilerInt4) -> str:
    if type_header.first == TYPE_KIND_INT and type_header.second <= 8:
        return "sturb"
    if type_header.first == TYPE_KIND_INT and type_header.second <= 16:
        return "sturh"
    return "stur"


def _indexed_scalar_type(type_header: CompilerInt4) -> bool:
    return type_header.first in (TYPE_KIND_INT, TYPE_KIND_FP, TYPE_KIND_PTR)


def _indexed_scalar_slot_access(
    kernel: IndexedFunctionKernel,
    type_id: int,
    offset: int,
    reg_index: int,
    *,
    store: bool,
) -> list[str]:
    type_header: CompilerInt4 = kernel.type_header(type_id)
    reg = reg_name_indexed(kernel, type_id, reg_index)
    op = (
        _indexed_scalar_stack_store_op(type_header)
        if store
        else _indexed_scalar_stack_load_op(type_header)
    )
    if offset > 255:
        lines = emit_slot_base_address_parts(offset, "x15")
        lines.append(emitted_memory_instruction_line(op, reg, "x15"))
        return lines
    return [emitted_memory_instruction_line(op, reg, "x29", -offset)]


def emit_memory_instruction_by_id(
    func: ParsedFunction,
    kind_id: int,
    data: tuple,
    module_symbols: PreparedModuleSymbols,
    *,
    indexed_kernel: IndexedFunctionKernel | None = None,
    block_id: int = -1,
    instruction_index: int = -1,
    indexed_dest_id: int = -1,
) -> list[str] | None:
    indexed_dest_has_slot = bool(
        indexed_kernel is not None
        and indexed_dest_id >= 0
        and indexed_kernel.value_slot_id(indexed_dest_id) >= 0
    )
    if kind_id == PARSED_INSTRUCTION_KIND_ALLOCA:
        return []

    if kind_id == PARSED_INSTRUCTION_KIND_STORE:
        if indexed_kernel is not None:
            store_record: CompilerInt4 = indexed_kernel.instruction_record(data)
            value_type_id = store_record.first
            value_type_header: CompilerInt4 = indexed_kernel.type_header(
                value_type_id
            )
            value = (
                indexed_kernel.value_name(store_record.second)
                if store_record.second >= 0
                else indexed_kernel.call_texts[-store_record.second - 1]
            )
            ptr_name = (
                indexed_kernel.value_name(store_record.fourth)
                if store_record.fourth >= 0
                else indexed_kernel.call_texts[-store_record.fourth - 1]
            )
            if _indexed_scalar_type(value_type_header):
                alloca_type_id = (
                    indexed_kernel.alloca_type_id(store_record.fourth)
                    if store_record.fourth >= 0
                    else -1
                )
                storage_matches = alloca_type_id == value_type_id
                if alloca_type_id >= 0 and not storage_matches:
                    alloca_type_header: CompilerInt4 = (
                        indexed_kernel.type_header(alloca_type_id)
                    )
                    storage_matches = (
                        alloca_type_header.first == TYPE_KIND_PTR
                        and value_type_header.first == TYPE_KIND_PTR
                    )
                if storage_matches:
                    lines = materialize_scalar_value_indexed(
                        func,
                        indexed_kernel,
                        value,
                        value_type_id,
                        9,
                        module_symbols,
                        value_id=store_record.second,
                    )
                    lines.extend(
                        _indexed_scalar_slot_access(
                            indexed_kernel,
                            value_type_id,
                            indexed_kernel.alloca_offset(store_record.fourth),
                            9,
                            store=True,
                        )
                    )
                    return lines
                lines = materialize_scalar_value_indexed(
                    func,
                    indexed_kernel,
                    ptr_name,
                    store_record.third,
                    9,
                    module_symbols,
                    value_id=store_record.fourth,
                )
                lines.extend(
                    materialize_scalar_value_indexed(
                        func,
                        indexed_kernel,
                        value,
                        value_type_id,
                        10,
                        module_symbols,
                        value_id=store_record.second,
                    )
                )
                lines.append(
                    emitted_memory_instruction_line(
                        _indexed_scalar_mem_store_op(value_type_header),
                        reg_name_indexed(indexed_kernel, value_type_id, 10),
                        "x9",
                    )
                )
                return lines
            value_type = indexed_kernel.type_desc(value_type_id)
        else:
            value_type, value, _ptr_type, ptr_name = data
        if value == "zeroinitializer" and (value_type.is_array or value_type.is_struct):
            if (
                parsed_function_has_alloca_slot(func, ptr_name)
                and parsed_function_alloca_slot_type(func, ptr_name).describe()
                == value_type.describe()
            ):
                return zero_slot_parts(
                    parsed_function_alloca_slot_offset(func, ptr_name), value_type
                )
            lines = materialize_pointer(func, ptr_name, 9, module_symbols)
            lines.extend(zero_address("x9", value_type.slot_size))
            return lines
        if (value_type.is_array or value_type.is_struct) and not aggregate_fits_reg_abi(value_type):
            if is_aggregate_literal_value(value):
                if (
                    parsed_function_has_alloca_slot(func, ptr_name)
                    and parsed_function_alloca_slot_type(func, ptr_name).describe()
                    == value_type.describe()
                ):
                    lines = emit_slot_base_address_parts(
                        parsed_function_alloca_slot_offset(func, ptr_name), "x15"
                    )
                    lines.extend(
                        store_large_aggregate_literal_to_address(
                            value_type,
                            value,
                            "x15",
                            module_symbols=module_symbols,
                        )
                    )
                    return lines
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
            if not parsed_function_has_value_slot(func, value):
                raise BackendUnavailable(
                    f"self backend can only store aggregate SSA values from stack slots right now in {func.name!r}: {value}"
                )
            if (
                parsed_function_has_alloca_slot(func, ptr_name)
                and parsed_function_alloca_slot_type(func, ptr_name).describe()
                == value_type.describe()
            ):
                return copy_slot_to_slot_parts(
                    parsed_function_value_slot_offset(func, value),
                    parsed_function_value_slot_type(func, value),
                    parsed_function_alloca_slot_offset(func, ptr_name),
                    value_type,
                )
            lines = materialize_pointer(func, ptr_name, 9, module_symbols)
            lines.extend(
                copy_slot_to_address_parts(
                    parsed_function_value_slot_offset(func, value),
                    parsed_function_value_slot_type(func, value),
                    "x9",
                )
            )
            return lines
        if (
            parsed_function_has_alloca_slot(func, ptr_name)
            and parsed_function_alloca_slot_type(func, ptr_name).describe()
            == value_type.describe()
        ):
            lines = materialize_value(func, value, value_type, 9, module_symbols)
            lines.extend(
                store_value_regs_to_slot_parts(
                    parsed_function_alloca_slot_offset(func, ptr_name),
                    value_type,
                    9,
                )
            )
            return lines
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        lines.extend(materialize_value(func, value, value_type, 10, module_symbols))
        lines.extend(store_value_to_address("x9", value_type, 10))
        return lines

    if kind_id == PARSED_INSTRUCTION_KIND_LOAD_ATOMIC:
        dest, value_type, _ptr_type, ptr_name, ordering = data
        if not (indexed_dest_has_slot if indexed_kernel is not None else parsed_function_has_value_slot(func, dest)):
            return []
        _atomic_width_check(func, "load_atomic", value_type)
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        load_op = "ldar" if ordering in ("acquire", "seq_cst") else "ldr"
        lines.append(
            emitted_memory_instruction_line(
                load_op,
                reg_name(value_type, 10),
                "x9",
            )
        )
        lines.extend(store_value_regs_to_value_slot(func, dest, 10))
        return lines

    if kind_id == PARSED_INSTRUCTION_KIND_STORE_ATOMIC:
        value_type, value, _ptr_type, ptr_name, ordering = data
        _atomic_width_check(
            func,
            "store_atomic",
            value_type,
            allowed=("i8", "i32", "i64"),
        )
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        lines.extend(materialize_value(func, value, value_type, 10, module_symbols))
        store_op = "stlr" if ordering in ("release", "seq_cst") else "str"
        if value_type.describe() == "i8":
            store_op += "b"
        lines.append(
            emitted_memory_instruction_line(
                store_op,
                reg_name(value_type, 10),
                "x9",
            )
        )
        return lines

    if kind_id == PARSED_INSTRUCTION_KIND_ATOMICRMW:
        dest, op, _ptr_type, ptr_name, value_type, value, _ordering = data
        if value_type.describe() == "i8" and op != "xchg":
            raise BackendUnavailable(
                f"self backend atomicrmw i8 supports only xchg (byte flags) in "
                f"{func.name!r}: {op}"
            )
        _atomic_width_check(
            func,
            "atomicrmw",
            value_type,
            allowed=("i8", "i32", "i64"),
        )
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
            lines.append(emitted_move_register_line(r_new, r_val))
        else:
            lines.append(f"  {_ATOMIC_RMW_COMPUTE[op]} {r_new}, {r_old}, {r_val}")
        lines.append(f"  stlxr{ex_suffix} w13, {r_new}, [x9]")
        lines.append(emitted_branch_line("cbnz", label, "w13"))
        if (indexed_dest_has_slot if indexed_kernel is not None else parsed_function_has_value_slot(func, dest)):
            lines.extend(store_value_regs_to_value_slot(func, dest, 11))
        return lines

    if kind_id == PARSED_INSTRUCTION_KIND_CMPXCHG:
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
        _atomic_width_check(func, "cmpxchg", value_type)
        lines = materialize_pointer(func, ptr_name, 9, module_symbols)
        lines.extend(materialize_value(func, expected, value_type, 10, module_symbols))
        lines.extend(materialize_value(func, desired, value_type, 11, module_symbols))
        r_exp = reg_name(value_type, 10)
        r_des = reg_name(value_type, 11)
        r_old = reg_name(value_type, 12)
        base = "Lat_" + sanitize_label(func.name) + "_" + sanitize_label(dest)
        lines.append(f"{base}_retry:")
        lines.append(f"  ldaxr {r_old}, [x9]")
        lines.append(emitted_compare_register_line(r_old, r_exp))
        lines.append(emitted_branch_line("b.ne", base + "_fail"))
        lines.append(f"  stlxr w13, {r_des}, [x9]")
        lines.append(emitted_branch_line("cbnz", base + "_retry", "w13"))
        lines.append(emitted_branch_line("b", base + "_done"))
        lines.append(f"{base}_fail:")
        lines.append("  clrex")
        lines.append(f"{base}_done:")
        lines.append(emitted_cset_line("w14", "eq"))
        if (indexed_dest_has_slot if indexed_kernel is not None else parsed_function_has_value_slot(func, dest)):
            lines.extend(emit_value_slot_base_address(func, dest, "x15"))
            lines.append(emitted_memory_instruction_line("str", r_old, "x15"))
            _flag_type, flag_offset = aggregate_member_info(pair_type, (1,))
            lines.append(
                emitted_memory_instruction_line(
                    "strb",
                    "w14",
                    "x15",
                    flag_offset,
                )
            )
        return lines

    if kind_id == PARSED_INSTRUCTION_KIND_FENCE:
        return ["  dmb ish"]

    if kind_id == PARSED_INSTRUCTION_KIND_LOAD:
        if indexed_kernel is not None:
            load_record: CompilerInt4 = indexed_kernel.instruction_record(data)
            dest = indexed_kernel.value_name(indexed_dest_id)
            value_type_id = load_record.first
            value_type_header: CompilerInt4 = indexed_kernel.type_header(
                value_type_id
            )
            ptr_name = (
                indexed_kernel.value_name(load_record.third)
                if load_record.third >= 0
                else indexed_kernel.call_texts[-load_record.third - 1]
            )
            if not indexed_dest_has_slot:
                return []
            if _indexed_scalar_type(value_type_header):
                alloca_type_id = (
                    indexed_kernel.alloca_type_id(load_record.third)
                    if load_record.third >= 0
                    else -1
                )
                storage_matches = alloca_type_id == value_type_id
                if alloca_type_id >= 0 and not storage_matches:
                    alloca_type_header: CompilerInt4 = (
                        indexed_kernel.type_header(alloca_type_id)
                    )
                    storage_matches = (
                        alloca_type_header.first == TYPE_KIND_PTR
                        and value_type_header.first == TYPE_KIND_PTR
                    )
                if storage_matches:
                    lines = _indexed_scalar_slot_access(
                        indexed_kernel,
                        value_type_id,
                        indexed_kernel.alloca_offset(load_record.third),
                        10,
                        store=False,
                    )
                else:
                    lines = materialize_scalar_value_indexed(
                        func,
                        indexed_kernel,
                        ptr_name,
                        load_record.second,
                        9,
                        module_symbols,
                        value_id=load_record.third,
                    )
                    lines.append(
                        emitted_memory_instruction_line(
                            _indexed_scalar_mem_load_op(value_type_header),
                            reg_name_indexed(indexed_kernel, value_type_id, 10),
                            "x9",
                        )
                    )
                allocated_lines = commit_allocated_scalar_result_indexed(
                    func,
                    indexed_dest_id,
                    value_type_id,
                    reg_name_indexed(indexed_kernel, value_type_id, 10),
                )
                if allocated_lines is not None:
                    lines.extend(allocated_lines)
                else:
                    slot_id = indexed_kernel.value_slot_id(indexed_dest_id)
                    lines.extend(
                        _indexed_scalar_slot_access(
                            indexed_kernel,
                            value_type_id,
                            indexed_kernel.slot_offset(slot_id),
                            10,
                            store=True,
                        )
                    )
                return lines
            value_type = indexed_kernel.type_desc(value_type_id)
        else:
            dest, value_type, _ptr_type, ptr_name = data
        if not (indexed_dest_has_slot if indexed_kernel is not None else parsed_function_has_value_slot(func, dest)):
            return []
        if (value_type.is_array or value_type.is_struct) and not value_has_uses(func, dest):
            return []
        if (value_type.is_array or value_type.is_struct) and not aggregate_fits_reg_abi(value_type):
            if (
                parsed_function_has_alloca_slot(func, ptr_name)
                and parsed_function_alloca_slot_type(func, ptr_name).describe()
                == value_type.describe()
            ):
                return copy_slot_to_slot_parts(
                    parsed_function_alloca_slot_offset(func, ptr_name),
                    value_type,
                    parsed_function_value_slot_offset(func, dest),
                    parsed_function_value_slot_type(func, dest),
                )
            lines = materialize_pointer(func, ptr_name, 9, module_symbols)
            lines.extend(copy_address_to_value_slot("x9", func, dest))
            return lines
        lines: list[str] = []
        if (
            parsed_function_has_alloca_slot(func, ptr_name)
            and parsed_function_alloca_slot_type(func, ptr_name).describe()
            == value_type.describe()
        ):
            lines.extend(
                load_slot_to_value_regs_parts(
                    parsed_function_alloca_slot_offset(func, ptr_name),
                    value_type,
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
            lines.extend(store_value_regs_to_value_slot(func, dest, 10))
        return lines

    return None


def emit_memory_instruction(
    func: ParsedFunction,
    kind: str,
    data: tuple,
    module_symbols: PreparedModuleSymbols,
    *,
    indexed_kernel: IndexedFunctionKernel | None = None,
    block_id: int = -1,
    instruction_index: int = -1,
    indexed_dest_id: int = -1,
) -> list[str] | None:
    """Legacy/text API; indexed consumers call the integer-ID entry."""
    kind_id = _PARSED_INSTRUCTION_KIND_IDS.get(kind)
    if kind_id is None:
        return None
    return emit_memory_instruction_by_id(
        func,
        kind_id,
        data,
        module_symbols,
        indexed_kernel=indexed_kernel,
        block_id=block_id,
        instruction_index=instruction_index,
        indexed_dest_id=indexed_dest_id,
    )
