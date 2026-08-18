from __future__ import annotations

"""AArch64 Darwin ABI helpers for the self backend."""

from . import BackendUnavailable
from .self_backend_ir import TypeDesc, _align_to
from .self_backend_kernel import (
    IndexedFunctionKernel,
    TYPE_KIND_ARRAY,
    TYPE_KIND_FP,
    TYPE_KIND_INT,
    TYPE_KIND_PTR,
    TYPE_KIND_STRUCT,
    TYPE_KIND_VOID,
)


def _append_hfa_members(
    value_type: TypeDesc,
    members: list[tuple[TypeDesc, int]],
) -> bool:
    if value_type.is_fp:
        member_size = 4 if value_type.width <= 32 else 8
        members.append((value_type, len(members) * member_size))
        return len(members) <= 4
    if value_type.is_array:
        if value_type.elem is None or value_type.count <= 0:
            return False
        index = 0
        while index < value_type.count:
            if not _append_hfa_members(value_type.elem, members):
                return False
            index += 1
        return True
    if value_type.is_struct:
        index = 0
        while index < len(value_type.fields):
            if not _append_hfa_members(value_type.fields[index], members):
                return False
            index += 1
        return True
    return False


def aggregate_hfa_members(value_type: TypeDesc) -> tuple[tuple[TypeDesc, int], ...]:
    """Return flattened homogeneous FP members and byte offsets, or ``()``.

    Darwin follows AAPCS64's homogeneous floating-point aggregate rule: one
    through four recursively nested, same-width FP members use consecutive
    SIMD/FP registers instead of the ordinary aggregate GPR classes.
    """
    if not (value_type.is_array or value_type.is_struct):
        return ()
    members: list[tuple[TypeDesc, int]] = []
    if not _append_hfa_members(value_type, members):
        return ()
    if not members or len(members) > 4:
        return ()
    width = members[0][0].width
    index = 1
    while index < len(members):
        if members[index][0].width != width:
            return ()
        index += 1
    return tuple(members)


def reg_name(value_type: TypeDesc, index: int) -> str:
    if value_type.is_array or value_type.is_struct:
        hfa = aggregate_hfa_members(value_type)
        if len(hfa) == 1:
            prefix = "s" if hfa[0][0].width <= 32 else "d"
            return f"{prefix}{index}"
        chunks = aggregate_reg_chunks(value_type)
        if len(chunks) != 1:
            raise BackendUnavailable(
                f"self backend cannot use aggregate type in a register directly: {value_type.describe()}"
            )
        prefix = "x" if chunks[0] > 4 else "w"
    elif value_type.is_fp:
        prefix = "s" if value_type.width <= 32 else "d"
    elif value_type.is_ptr or value_type.width > 32:
        prefix = "x"
    else:
        prefix = "w"
    return f"{prefix}{index}"


def aggregate_is_gpr_only(value_type: TypeDesc) -> bool:
    if value_type.is_int or value_type.is_ptr:
        return True
    if value_type.is_array:
        assert value_type.elem is not None
        return aggregate_is_gpr_only(value_type.elem)
    if value_type.is_struct:
        return all(aggregate_is_gpr_only(field) for field in value_type.fields)
    return False


def aggregate_reg_chunks(value_type: TypeDesc) -> tuple[int, ...]:
    if not (value_type.is_array or value_type.is_struct):
        raise BackendUnavailable(
            f"self backend aggregate register helper expected aggregate type, got {value_type.describe()}"
        )
    if not aggregate_is_gpr_only(value_type):
        raise BackendUnavailable(
            "self backend aggregate register ABI currently only supports integer/pointer-only "
            f"aggregates, got {value_type.describe()}"
        )
    size = value_type.slot_size
    if 1 <= size <= 8:
        return (size,)
    if 8 < size <= 16:
        tail = size - 8
        if 1 <= tail <= 8:
            return (8, tail)
    raise BackendUnavailable(
        "self backend aggregate register ABI currently only supports aggregate sizes "
        "<=8 or two-register 8+{1..8}-byte shapes, got "
        f"{value_type.describe()} ({size} bytes)"
    )


def abi_value_reg_names(value_type: TypeDesc, start_index: int) -> tuple[str, ...]:
    if value_type.is_void:
        return ()
    if value_type.is_array or value_type.is_struct:
        hfa = aggregate_hfa_members(value_type)
        if hfa:
            prefix = "s" if hfa[0][0].width <= 32 else "d"
            return tuple(f"{prefix}{start_index + index}" for index in range(len(hfa)))
        names: list[str] = []
        for index, chunk_size in enumerate(aggregate_reg_chunks(value_type)):
            prefix = "x" if chunk_size > 4 else "w"
            names.append(f"{prefix}{start_index + index}")
        return tuple(names)
    return (reg_name(value_type, start_index),)


def aggregate_fits_reg_abi(value_type: TypeDesc) -> bool:
    if not (value_type.is_array or value_type.is_struct):
        return True
    if aggregate_hfa_members(value_type):
        return True
    try:
        aggregate_reg_chunks(value_type)
    except BackendUnavailable:
        return False
    return True


def aggregate_passed_indirect(value_type: TypeDesc) -> bool:
    return (value_type.is_array or value_type.is_struct) and not aggregate_fits_reg_abi(
        value_type
    )


def aggregate_returned_indirect(value_type: TypeDesc) -> bool:
    return aggregate_passed_indirect(value_type)


def _indexed_hfa_code(kernel: IndexedFunctionKernel, type_id: int) -> int:
    """Encode a valid HFA as ``width * 8 + member_count``; zero is invalid."""
    kind_id = kernel.type_kind_id(type_id)
    if kind_id == TYPE_KIND_FP:
        return kernel.type_width(type_id) * 8 + 1
    if kind_id == TYPE_KIND_ARRAY:
        child_id = kernel.type_child_id(type_id)
        count = kernel.type_scalars.get_unchecked(type_id * 12 + 2)
        if child_id < 0 or count <= 0:
            return 0
        child_code = _indexed_hfa_code(kernel, child_id)
        if child_code == 0:
            return 0
        width = child_code // 8
        member_count = (child_code % 8) * count
        return 0 if member_count > 4 else width * 8 + member_count
    if kind_id != TYPE_KIND_STRUCT:
        return 0
    field_start = kernel.type_field_start(type_id)
    field_count = kernel.type_field_count(type_id)
    width = 0
    count = 0
    index = 0
    while index < field_count:
        field_id = kernel.type_field_ids.get_unchecked(field_start + index)
        child_code = _indexed_hfa_code(kernel, field_id)
        if child_code == 0:
            return 0
        child_width = child_code // 8
        if width and child_width != width:
            return 0
        width = child_width
        count += child_code % 8
        if count > 4:
            return 0
        index += 1
    return 0 if count == 0 else width * 8 + count


def _indexed_aggregate_is_gpr_only(
    kernel: IndexedFunctionKernel, type_id: int
) -> bool:
    kind_id = kernel.type_kind_id(type_id)
    if kind_id == TYPE_KIND_INT or kind_id == TYPE_KIND_PTR:
        return True
    if kind_id == TYPE_KIND_ARRAY:
        child_id = kernel.type_child_id(type_id)
        return child_id >= 0 and _indexed_aggregate_is_gpr_only(
            kernel, child_id
        )
    if kind_id != TYPE_KIND_STRUCT:
        return False
    field_start = kernel.type_field_start(type_id)
    field_count = kernel.type_field_count(type_id)
    index = 0
    while index < field_count:
        if not _indexed_aggregate_is_gpr_only(
            kernel,
            kernel.type_field_ids.get_unchecked(field_start + index),
        ):
            return False
        index += 1
    return True


def aggregate_fits_reg_abi_indexed(
    kernel: IndexedFunctionKernel, type_id: int
) -> bool:
    kind_id = kernel.type_kind_id(type_id)
    if kind_id != TYPE_KIND_ARRAY and kind_id != TYPE_KIND_STRUCT:
        return True
    if _indexed_hfa_code(kernel, type_id):
        return True
    if not _indexed_aggregate_is_gpr_only(kernel, type_id):
        return False
    size = kernel.type_slot_size(type_id)
    return 1 <= size <= 16


def aggregate_returned_indirect_indexed(
    kernel: IndexedFunctionKernel, type_id: int
) -> bool:
    kind_id = kernel.type_kind_id(type_id)
    return (
        kind_id == TYPE_KIND_ARRAY or kind_id == TYPE_KIND_STRUCT
    ) and not aggregate_fits_reg_abi_indexed(kernel, type_id)


def aggregate_passed_indirect_indexed(
    kernel: IndexedFunctionKernel, type_id: int
) -> bool:
    return aggregate_returned_indirect_indexed(kernel, type_id)


def abi_register_code_indexed(
    kernel: IndexedFunctionKernel, type_id: int
) -> int:
    """Return 8+GPR-count, 16+FPR-count, or zero for void."""
    kind_id = kernel.type_kind_id(type_id)
    if kind_id == TYPE_KIND_VOID:
        return 0
    if kind_id == TYPE_KIND_FP:
        return 17
    if kind_id == TYPE_KIND_ARRAY or kind_id == TYPE_KIND_STRUCT:
        hfa_code = _indexed_hfa_code(kernel, type_id)
        if hfa_code:
            return 16 + (hfa_code % 8)
        if aggregate_passed_indirect_indexed(kernel, type_id):
            return 9
        size = kernel.type_slot_size(type_id)
        return 9 if size <= 8 else 10
    return 9


def reg_name_indexed(
    kernel: IndexedFunctionKernel, type_id: int, index: int
) -> str:
    kind_id = kernel.type_kind_id(type_id)
    width = kernel.type_width(type_id)
    if kind_id == TYPE_KIND_FP:
        return ("s" if width <= 32 else "d") + str(index)
    if kind_id == TYPE_KIND_PTR:
        return "x" + str(index)
    if kind_id == TYPE_KIND_INT:
        return ("x" if width > 32 else "w") + str(index)
    hfa_code = _indexed_hfa_code(kernel, type_id)
    if hfa_code:
        return ("s" if hfa_code // 8 <= 32 else "d") + str(index)
    return ("x" if kernel.type_slot_size(type_id) > 4 else "w") + str(index)


def stack_arg_storage_size_indexed(
    kernel: IndexedFunctionKernel, type_id: int
) -> int:
    if aggregate_passed_indirect_indexed(kernel, type_id):
        return 8
    kind_id = kernel.type_kind_id(type_id)
    if kind_id == TYPE_KIND_ARRAY or kind_id == TYPE_KIND_STRUCT:
        return _align_to(kernel.type_slot_size(type_id), 8)
    return 8


def variadic_stack_arg_storage_size_indexed(
    kernel: IndexedFunctionKernel, type_id: int
) -> int:
    kind_id = kernel.type_kind_id(type_id)
    if kind_id == TYPE_KIND_ARRAY or kind_id == TYPE_KIND_STRUCT:
        return _align_to(kernel.type_slot_size(type_id), 8)
    return 8


def stack_arg_storage_size(arg_type: TypeDesc) -> int:
    if aggregate_passed_indirect(arg_type):
        return 8
    if arg_type.is_array or arg_type.is_struct:
        return _align_to(arg_type.slot_size, 8)
    return 8


def variadic_stack_arg_storage_size(arg_type: TypeDesc) -> int:
    if arg_type.is_array or arg_type.is_struct:
        return _align_to(arg_type.slot_size, 8)
    return 8


def assign_abi_arg_regs(arg_types: list[TypeDesc]) -> list[tuple[str, ...]]:
    gpr_index = 0
    fpr_index = 0
    assignments: list[tuple[str, ...]] = []
    for arg_type in arg_types:
        if arg_type.is_void:
            assignments.append(())
            continue
        if arg_type.is_fp:
            regs = abi_value_reg_names(arg_type, fpr_index)
            if fpr_index + len(regs) > 8:
                assignments.append(())
            else:
                fpr_index += len(regs)
                assignments.append(regs)
            continue
        hfa = aggregate_hfa_members(arg_type)
        if hfa:
            regs = abi_value_reg_names(arg_type, fpr_index)
            if fpr_index + len(regs) > 8:
                fpr_index = 8
                assignments.append(())
            else:
                fpr_index += len(regs)
                assignments.append(regs)
            continue
        if aggregate_passed_indirect(arg_type):
            if gpr_index >= 8:
                assignments.append(())
            else:
                assignments.append((f"x{gpr_index}",))
                gpr_index += 1
            continue
        regs = abi_value_reg_names(arg_type, gpr_index)
        if gpr_index + len(regs) > 8:
            assignments.append(())
        else:
            gpr_index += len(regs)
            assignments.append(regs)
    return assignments


def stack_arg_offsets(
    arg_types: list[TypeDesc],
    assignments: list[tuple[str, ...]] | None = None,
) -> list[int | None]:
    regs = assignments if assignments is not None else assign_abi_arg_regs(arg_types)
    next_offset = 16
    offsets: list[int | None] = []
    for arg_type, assigned_regs in zip(arg_types, regs):
        if arg_type.is_void or assigned_regs:
            offsets.append(None)
            continue
        offsets.append(next_offset)
        next_offset += stack_arg_storage_size(arg_type)
    return offsets
