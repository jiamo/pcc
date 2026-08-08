"""CPU reference execution for narrow Kernel IR subsets.

This module is an oracle, not a GPU runtime. It executes the same frozen
plain-TIR scalar tiled GEMM subset that the current Metal source emitter
accepts, so the Metal/TIRx route has a numeric CPU baseline before claiming
device execution.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pcc.kernel_ir.ir import KernelModule, MemoryScope
from pcc.kernel_ir.scalar_semantics import KernelScalarError, coerce_pod_scalar
from pcc.kernel_ir.tirx_adapter import PlainTirModule, lower_to_plain_tir


class KernelCpuReferenceError(ValueError):
    """The module or supplied data is outside the CPU reference subset."""


Number = int | float
Matrix = tuple[tuple[float, ...], ...]
IntMatrix = tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class CpuReferenceResult:
    """Numeric CPU oracle result for one Kernel IR function."""

    entry: str
    outputs: dict[str, Matrix]
    tiles_executed: int
    k_tiles: int
    claim_mode: str = "CPU reference oracle, not GPU execution"
    runtime_launch_executed: bool = False
    whole_program_gpu: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_mode": self.claim_mode,
            "entry": self.entry,
            "outputs": {
                name: [list(row) for row in matrix]
                for name, matrix in self.outputs.items()
            },
            "tiles_executed": self.tiles_executed,
            "k_tiles": self.k_tiles,
            "runtime_launch_executed": self.runtime_launch_executed,
            "whole_program_gpu": self.whole_program_gpu,
        }


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def execute_static_fill_reference(
    module: KernelModule | PlainTirModule,
) -> CpuReferenceResult:
    """Execute one static rank-2 global-buffer fill as an independent oracle."""
    plain = module if isinstance(module, PlainTirModule) else lower_to_plain_tir(module, target="metal")
    if len(plain.funcs) != 1:
        raise KernelCpuReferenceError("static fill oracle requires exactly one function")
    func = plain.funcs[0]
    fills = [op for op in func.get("ops", []) if op.get("tir_op") == "tir.fill_loop"]
    unsupported = [
        op.get("tir_op")
        for op in func.get("ops", [])
        if op.get("tir_op") not in {"tir.fill_loop", "tir.parallel_for"}
    ]
    if len(fills) != 1 or unsupported:
        raise KernelCpuReferenceError(
            f"static fill oracle requires one fill and optional parallel metadata; "
            f"fills={len(fills)}, unsupported={unsupported}"
        )
    args = fills[0].get("args") or []
    if len(args) != 1:
        raise KernelCpuReferenceError("static fill op requires one destination")
    name = str(args[0])
    record = next(
        (
            param
            for param in func.get("params", [])
            if param.get("kind") == "buffer" and param.get("name") == name
        ),
        None,
    )
    if record is None or record.get("scope") != MemoryScope.GLOBAL.value:
        raise KernelCpuReferenceError("static fill destination must be a global buffer")
    shape = record.get("shape")
    dtype = record.get("dtype")
    if (
        not isinstance(shape, list)
        or len(shape) != 2
        or not all(isinstance(dim, int) and dim > 0 for dim in shape)
        or not isinstance(dtype, str)
    ):
        raise KernelCpuReferenceError("static fill requires a shaped rank-2 buffer")
    try:
        value = coerce_pod_scalar(dtype, (fills[0].get("attrs") or {}).get("value", 0))
    except KernelScalarError as exc:
        raise KernelCpuReferenceError(f"invalid static fill literal: {exc}") from exc
    rows, cols = shape
    matrix = tuple(tuple(value for _ in range(cols)) for _ in range(rows))
    return CpuReferenceResult(
        entry=str(func.get("name")),
        outputs={name: matrix},
        tiles_executed=1,
        k_tiles=0,
        claim_mode="Static POD fill CPU oracle, not GPU execution",
    )


def execute_static_indexed_reference(
    module: KernelModule | PlainTirModule,
    inputs: Mapping[str, Sequence[Sequence[Number]]],
) -> CpuReferenceResult:
    """Execute one canonical flat indexed store for scheduled-loop oracles."""
    plain = module if isinstance(module, PlainTirModule) else lower_to_plain_tir(module, target="metal")
    if len(plain.funcs) != 1:
        raise KernelCpuReferenceError("indexed oracle requires exactly one function")
    func = plain.funcs[0]
    stores = [op for op in func.get("ops", []) if op.get("tir_op") == "tir.indexed_store"]
    unsupported = [
        op.get("tir_op")
        for op in func.get("ops", [])
        if op.get("tir_op") not in {"tir.parallel_for", "tir.indexed_store"}
    ]
    if len(stores) != 1 or unsupported:
        raise KernelCpuReferenceError(
            f"indexed oracle requires one store and parallel metadata; "
            f"stores={len(stores)}, unsupported={unsupported}"
        )
    records = {
        str(param.get("name")): param
        for param in func.get("params", [])
        if param.get("kind") == "buffer"
    }
    store = stores[0]
    args = store.get("args") or []
    if not args or str(args[0]) not in records:
        raise KernelCpuReferenceError("indexed store has no shaped output buffer")
    output_name = str(args[0])
    output_record = records[output_name]
    shape = output_record.get("shape")
    dtype = output_record.get("dtype")
    if (
        not isinstance(shape, list)
        or len(shape) != 2
        or not all(isinstance(dim, int) and dim > 0 for dim in shape)
        or not isinstance(dtype, str)
    ):
        raise KernelCpuReferenceError("indexed oracle requires static rank-2 output")
    rows, cols = shape
    count = rows * cols
    flat_inputs: dict[str, list[Number]] = {}
    for name, matrix in inputs.items():
        record = records.get(name)
        if record is None or record.get("shape") != shape:
            raise KernelCpuReferenceError(
                f"indexed input {name!r} must match output shape {shape}"
            )
        if len(matrix) != rows or any(len(row) != cols for row in matrix):
            raise KernelCpuReferenceError(
                f"indexed input {name!r} data must have shape {shape}"
            )
        flat_inputs[name] = [value for row in matrix for value in row]

    def evaluate(expr: Any, gid: int) -> Number:
        if not isinstance(expr, dict):
            raise KernelCpuReferenceError("indexed expression is not a record")
        kind = expr.get("kind")
        if kind == "thread_id_x":
            return gid
        if kind == "literal":
            return expr.get("value")
        if kind == "load":
            name = expr.get("buffer")
            data = flat_inputs.get(name)
            if data is None:
                raise KernelCpuReferenceError(f"indexed oracle has no input {name!r}")
            index = evaluate(expr.get("index"), gid)
            if type(index) is not int or index < 0 or index >= len(data):
                raise KernelCpuReferenceError(f"indexed load index {index!r} is out of range")
            return data[index]
        if kind == "binary":
            left = evaluate(expr.get("left"), gid)
            right = evaluate(expr.get("right"), gid)
            op = expr.get("op")
            if op == "add":
                return left + right
            if op == "sub":
                return left - right
            if op == "mul":
                return left * right
            if op == "div":
                return left / right
        raise KernelCpuReferenceError(f"unsupported indexed expression {expr!r}")

    attrs = store.get("attrs") or {}
    index_expr = attrs.get("index")
    value_expr = attrs.get("value")
    flat_output: list[Number] = [0] * count
    for gid in range(count):
        index = evaluate(index_expr, gid)
        if type(index) is not int or index < 0 or index >= count:
            raise KernelCpuReferenceError(f"indexed store index {index!r} is out of range")
        try:
            flat_output[index] = coerce_pod_scalar(dtype, evaluate(value_expr, gid))
        except KernelScalarError as exc:
            raise KernelCpuReferenceError(f"indexed output conversion failed: {exc}") from exc
    output = tuple(
        tuple(flat_output[row * cols + col] for col in range(cols))
        for row in range(rows)
    )
    return CpuReferenceResult(
        entry=str(func.get("name")),
        outputs={output_name: output},
        tiles_executed=1,
        k_tiles=0,
        claim_mode="Static scheduled indexed-loop CPU oracle, not GPU execution",
    )


def _quantize_output_value(value: float, *, dtype: str) -> float:
    if dtype == "f16":
        return struct.unpack("<e", struct.pack("<e", float(value)))[0]
    if dtype == "f32":
        return float(value)
    raise KernelCpuReferenceError(f"unsupported GEMM output dtype {dtype!r}")


def _validate_gemm_policy_metadata(policy: object) -> None:
    if policy is None:
        return
    if isinstance(policy, str) and policy.startswith("GemmWarpPolicy."):
        return
    if isinstance(policy, (list, tuple)) and len(policy) == 2:
        rows, cols = policy
        if (
            not isinstance(rows, bool)
            and not isinstance(cols, bool)
            and isinstance(rows, int)
            and isinstance(cols, int)
            and rows > 0
            and cols > 0
        ):
            return
    raise KernelCpuReferenceError(
        f"unsupported T.gemm policy metadata {policy!r}; policy is metadata-only in the CPU oracle"
    )


def _coerce_plain(module: KernelModule | PlainTirModule) -> PlainTirModule:
    return module if isinstance(module, PlainTirModule) else lower_to_plain_tir(module, target="metal")


def _select_func(plain: PlainTirModule, entry: str | None) -> dict[str, Any]:
    funcs = plain.funcs
    if entry is None:
        if len(funcs) != 1:
            names = [func.get("name") for func in funcs]
            raise KernelCpuReferenceError(f"multiple funcs {names}; choose entry=")
        return funcs[0]
    for func in funcs:
        if func.get("name") == entry:
            return func
    names = [func.get("name") for func in funcs]
    raise KernelCpuReferenceError(f"entry {entry!r} not found; available {names}")


def _records(records: object, *, kind: str) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise KernelCpuReferenceError(f"plain-TIR {kind} must be a list")
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise KernelCpuReferenceError(f"bad {kind} record {record!r}")
        name = record.get("name")
        if not isinstance(name, str):
            raise KernelCpuReferenceError(f"bad {kind} record {record!r}")
        out[name] = record
    return out


def _shape(record: dict[str, Any], *, name: str) -> tuple[int, ...]:
    shape = record.get("shape")
    if not isinstance(shape, list) or not shape:
        raise KernelCpuReferenceError(f"{name}: CPU reference requires static shape metadata")
    dims: list[int] = []
    for dim in shape:
        if not isinstance(dim, int) or dim <= 0:
            raise KernelCpuReferenceError(f"{name}: bad shape {shape!r}")
        dims.append(dim)
    return tuple(dims)


def _matrix(value: object, *, name: str, shape: tuple[int, int]) -> Matrix:
    rows, cols = shape
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise KernelCpuReferenceError(f"{name}: expected a rank-2 numeric sequence")
    if len(value) != rows:
        raise KernelCpuReferenceError(f"{name}: expected {rows} rows, got {len(value)}")
    out: list[tuple[float, ...]] = []
    for row_index, row in enumerate(value):
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            raise KernelCpuReferenceError(f"{name}: row {row_index} is not a sequence")
        if len(row) != cols:
            raise KernelCpuReferenceError(
                f"{name}: row {row_index} expected {cols} columns, got {len(row)}"
            )
        converted: list[float] = []
        for col_index, item in enumerate(row):
            if not isinstance(item, (int, float)):
                raise KernelCpuReferenceError(
                    f"{name}: element ({row_index}, {col_index}) is not numeric"
                )
            converted.append(float(item))
        out.append(tuple(converted))
    return tuple(out)


def execute_static_row_reduce_sum_reference(
    module: KernelModule | PlainTirModule,
    inputs: Mapping[str, Sequence[Sequence[Number]]],
    *,
    entry: str | None = None,
) -> CpuReferenceResult:
    """Execute the bounded TileLang static row ``reduce_sum`` subset.

    This is a CPU correctness oracle for the importer-owned
    ``tilelang.reduce_sum.static_row.v1`` shape, not a GPU execution claim.  It
    deliberately validates the same finite contract as the Metal source path:
    one static rank-2 f16/f32 input, last-dimension reduction, one f32
    ``(rows, 1)`` output, and explicit shared scratch in frozen Kernel IR.
    """

    plain = _coerce_plain(module)
    func = _select_func(plain, entry)
    params = _records(func.get("params"), kind="params")
    locals_ = _records(func.get("locals"), kind="locals")
    ops = func.get("ops")
    if not isinstance(ops, list):
        raise KernelCpuReferenceError("row reduce_sum CPU reference requires an ops list")
    reductions = [
        op
        for op in ops
        if isinstance(op, dict) and op.get("tir_op") == "tir.reduce_loop"
    ]
    copies = [
        op
        for op in ops
        if isinstance(op, dict) and op.get("tir_op") == "tir.copy_loop"
    ]
    unsupported = [
        op.get("tir_op") if isinstance(op, dict) else type(op).__name__
        for op in ops
        if not isinstance(op, dict)
        or op.get("tir_op") not in {"tir.reduce_loop", "tir.copy_loop"}
    ]
    if len(reductions) != 1 or len(copies) != 1 or unsupported:
        raise KernelCpuReferenceError(
            "row reduce_sum CPU reference requires exactly one reduction and "
            f"one output copy; reductions={len(reductions)}, copies={len(copies)}, "
            f"unsupported={unsupported}"
        )

    reduction = reductions[0]
    reduction_args = reduction.get("args")
    attrs = reduction.get("attrs")
    copy_args = copies[0].get("args")
    copy_attrs = copies[0].get("attrs")
    if (
        not isinstance(reduction_args, list)
        or len(reduction_args) != 2
        or not isinstance(copy_args, list)
        or len(copy_args) != 2
        or not isinstance(attrs, dict)
        or not isinstance(copy_attrs, dict)
    ):
        raise KernelCpuReferenceError("row reduce_sum frozen ops are malformed")
    source_name, scratch_name = map(str, reduction_args)
    copy_source, output_name = map(str, copy_args)
    if copy_source != scratch_name or copy_attrs.get("reduction_output") is not True:
        raise KernelCpuReferenceError(
            "row reduce_sum output copy must consume the explicit reduction scratch"
        )
    if attrs.get("import_kind") != "tilelang.reduce_sum.static_row.v1":
        raise KernelCpuReferenceError(
            "row reduce_sum CPU reference requires importer-owned v1 metadata"
        )
    if (
        attrs.get("reduction") != "sum"
        or attrs.get("dim") != 1
        or attrs.get("clear") is not True
        or attrs.get("batch") != 1
    ):
        raise KernelCpuReferenceError(
            "row reduce_sum CPU reference supports only sum/dim=1/clear=True/batch=1"
        )

    source = params.get(source_name)
    output = params.get(output_name)
    scratch = locals_.get(scratch_name)
    if source is None or output is None or scratch is None:
        raise KernelCpuReferenceError(
            "row reduce_sum source, output, and scratch records must all be present"
        )
    source_shape = _shape(source, name=source_name)
    output_shape = _shape(output, name=output_name)
    scratch_shape = _shape(scratch, name=scratch_name)
    if len(source_shape) != 2:
        raise KernelCpuReferenceError("row reduce_sum input must be rank 2")
    rows, width = source_shape
    if output_shape != (rows, 1):
        raise KernelCpuReferenceError(
            f"row reduce_sum output shape must be {(rows, 1)!r}, got {output_shape!r}"
        )
    if scratch_shape != (width,):
        raise KernelCpuReferenceError(
            f"row reduce_sum scratch shape must be {(width,)!r}, got {scratch_shape!r}"
        )
    source_dtype = source.get("dtype")
    output_dtype = output.get("dtype")
    scratch_dtype = scratch.get("dtype")
    if source_dtype not in {"f16", "f32"} or output_dtype != "f32" or scratch_dtype != "f32":
        raise KernelCpuReferenceError(
            "row reduce_sum requires f16/f32 input and f32 output/scratch"
        )
    if func.get("grid") != [rows] or func.get("threads") != width:
        raise KernelCpuReferenceError(
            "row reduce_sum launch must use one threadgroup per row and one thread per column"
        )
    if attrs.get("extent") != rows * width or attrs.get("row_width") != width:
        raise KernelCpuReferenceError("row reduce_sum extent metadata does not match shapes")
    if set(inputs) != {source_name}:
        raise KernelCpuReferenceError(
            f"row reduce_sum expects only input {source_name!r}, got {sorted(inputs)}"
        )

    matrix = _matrix(inputs[source_name], name=source_name, shape=(rows, width))
    output_rows: list[tuple[float, ...]] = []
    for row in matrix:
        try:
            scratch = [
                float(
                    coerce_pod_scalar(
                        "f32",
                        coerce_pod_scalar(source_dtype, value),
                    )
                )
                for value in row
            ]
            active = width
            while active > 1:
                partner_offset = (active + 1) >> 1
                for tid in range(active >> 1):
                    partner = tid + partner_offset
                    if partner < active:
                        scratch[tid] = float(
                            coerce_pod_scalar(
                                "f32",
                                scratch[tid] + scratch[partner],
                            )
                        )
                active = partner_offset
            result = scratch[0]
        except KernelScalarError as exc:
            raise KernelCpuReferenceError(
                f"row reduce_sum scalar conversion failed: {exc}"
            ) from exc
        output_rows.append((result,))
    return CpuReferenceResult(
        entry=str(func.get("name")),
        outputs={output_name: tuple(output_rows)},
        tiles_executed=rows,
        k_tiles=0,
        claim_mode=(
            "Static TileLang-shaped last-dimension row reduce_sum CPU oracle; "
            "not GPU execution"
        ),
    )


def _int_matrix(value: object, *, name: str, shape: tuple[int, int]) -> IntMatrix:
    rows, cols = shape
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise KernelCpuReferenceError(f"{name}: expected a rank-2 integer sequence")
    if len(value) != rows:
        raise KernelCpuReferenceError(f"{name}: expected {rows} rows, got {len(value)}")
    out: list[tuple[int, ...]] = []
    for row_index, row in enumerate(value):
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            raise KernelCpuReferenceError(f"{name}: row {row_index} is not a sequence")
        if len(row) != cols:
            raise KernelCpuReferenceError(
                f"{name}: row {row_index} expected {cols} columns, got {len(row)}"
            )
        converted: list[int] = []
        for col_index, item in enumerate(row):
            if isinstance(item, bool) or not isinstance(item, int):
                raise KernelCpuReferenceError(
                    f"{name}: element ({row_index}, {col_index}) is not an integer"
                )
            converted.append(int(item))
        out.append(tuple(converted))
    return tuple(out)


def _find_gemm_pattern(
    func: dict[str, Any],
    params: dict[str, dict[str, Any]],
    locals_: dict[str, dict[str, Any]],
) -> tuple[str, str, str, str, str, str, dict[str, Any], str, dict[str, Any]]:
    ops = func.get("ops")
    if not isinstance(ops, list):
        raise KernelCpuReferenceError("plain-TIR function has no ops list")
    gemm_ops = [op for op in ops if isinstance(op, dict) and op.get("tir_op") == "tir.gemm_expand"]
    if len(gemm_ops) != 1:
        raise KernelCpuReferenceError("CPU reference requires exactly one GEMM op")
    gemm = gemm_ops[0]
    gemm_args = list(gemm.get("args", []))
    if len(gemm_args) < 3:
        raise KernelCpuReferenceError("GEMM op must have A, B, and C operands")
    a_shared, b_shared, c_local = (str(gemm_args[0]), str(gemm_args[1]), str(gemm_args[2]))
    attrs = gemm.get("attrs") or {}
    if not isinstance(attrs, dict):
        raise KernelCpuReferenceError("GEMM attrs must be a dict")
    if attrs.get("transpose_A") not in (None, False, True):
        raise KernelCpuReferenceError("GEMM transpose_A must be boolean")
    if attrs.get("transpose_B") not in (None, False, True):
        raise KernelCpuReferenceError("GEMM transpose_B must be boolean")
    _validate_gemm_policy_metadata(attrs.get("policy"))

    a_global = b_global = c_global = None
    c_staging = None
    a_copy_attrs: dict[str, Any] | None = None
    b_copy_attrs: dict[str, Any] | None = None
    c_staging_copy_attrs: dict[str, Any] | None = None
    c_staging_output_attrs: dict[str, Any] | None = None
    output_mode: str | None = None
    fill_seen = False
    for op in ops:
        if not isinstance(op, dict):
            raise KernelCpuReferenceError(f"bad op record {op!r}")
        tir_op = op.get("tir_op")
        args = list(op.get("args", []))
        op_attrs = op.get("attrs") or {}
        if not isinstance(op_attrs, dict):
            raise KernelCpuReferenceError("op attrs must be a dict")
        if tir_op == "tir.fill_loop" and args and str(args[0]) == c_local:
            if "parallel_extents" in op_attrs or "vectorized_extent" in op_attrs:
                raise KernelCpuReferenceError(
                    "CPU reference supports T.Parallel/T.vectorized only on A/B/C tile copy staging"
                )
            if op_attrs.get("value", 0) != 0:
                raise KernelCpuReferenceError("GEMM accumulator fill must be zero")
            fill_seen = True
        elif tir_op == "tir.copy_loop" and len(args) >= 2:
            src, dst = str(args[0]), str(args[1])
            if dst == a_shared:
                a_global = src
                a_copy_attrs = op_attrs
            elif dst == b_shared:
                b_global = src
                b_copy_attrs = op_attrs
            elif src == c_local:
                if dst in locals_:
                    c_staging = dst
                    c_staging_copy_attrs = op_attrs
                else:
                    c_global = dst
                output_mode = "copy"
            elif c_staging is not None and src == c_staging:
                c_global = dst
                c_staging_output_attrs = op_attrs
                output_mode = "copy"
        elif tir_op == "tir.atomic_add" and len(args) >= 2:
            dst, src = str(args[0]), str(args[1])
            if src == c_local:
                c_global = dst
                output_mode = "atomic_add"
        elif tir_op == "tir.gemm_expand":
            if "parallel_extents" in op_attrs or "vectorized_extent" in op_attrs:
                raise KernelCpuReferenceError(
                    "CPU reference supports T.Parallel/T.vectorized only on A/B/C tile copy staging"
                )
            continue
        elif tir_op == "tir.use_swizzle":
            _validate_swizzle_metadata(op)
            continue
        elif tir_op == "tir.annotate_layout":
            _validate_layout_annotation_noop(op)
            continue
        else:
            raise KernelCpuReferenceError(
                f"unsupported op {tir_op!r} in scalar tiled GEMM CPU reference"
            )
    if not fill_seen or a_global is None or b_global is None or c_global is None:
        raise KernelCpuReferenceError(
            "CPU reference requires zero fill, A/B global copies, and C copy-back or atomic add"
        )
    if output_mode is None:
        raise KernelCpuReferenceError("CPU reference requires a C output op")
    for name in (a_global, b_global, c_global):
        if name not in params or params[name].get("scope") != MemoryScope.GLOBAL.value:
            raise KernelCpuReferenceError(f"GEMM operand {name!r} must be a global param")
    for name in (a_shared, b_shared, c_local):
        if name not in locals_:
            raise KernelCpuReferenceError(f"GEMM operand {name!r} must be a local buffer")
    copy_attrs: dict[str, Any] = {}
    if a_copy_attrs is not None:
        copy_attrs["a_copy"] = dict(a_copy_attrs)
    if b_copy_attrs is not None:
        copy_attrs["b_copy"] = dict(b_copy_attrs)
    if c_staging is not None:
        copy_attrs["c_staging"] = c_staging
    if c_staging_copy_attrs is not None:
        copy_attrs["c_staging_copy"] = dict(c_staging_copy_attrs)
    if c_staging_output_attrs is not None:
        copy_attrs["c_staging_output"] = dict(c_staging_output_attrs)
    return a_global, b_global, c_global, a_shared, b_shared, c_local, attrs, output_mode, copy_attrs


def _find_gemm_sp_pattern(
    func: dict[str, Any],
    params: dict[str, dict[str, Any]],
    locals_: dict[str, dict[str, Any]],
) -> tuple[str, str, str, str, str, str, str, str, dict[str, Any], dict[str, Any]]:
    ops = func.get("ops")
    if not isinstance(ops, list):
        raise KernelCpuReferenceError("plain-TIR function has no ops list")
    sparse_ops = [
        op
        for op in ops
        if isinstance(op, dict) and op.get("tir_op") == "tir.gemm_sp_expand"
    ]
    if len(sparse_ops) != 1:
        raise KernelCpuReferenceError("sparse CPU reference requires exactly one GEMM_SP op")
    gemm = sparse_ops[0]
    gemm_args = list(gemm.get("args", []))
    if len(gemm_args) < 4:
        raise KernelCpuReferenceError("GEMM_SP op must have A_sparse, E, B, and C operands")
    a_shared, e_shared, b_shared, c_local = (
        str(gemm_args[0]),
        str(gemm_args[1]),
        str(gemm_args[2]),
        str(gemm_args[3]),
    )
    attrs = gemm.get("attrs") or {}
    if not isinstance(attrs, dict):
        raise KernelCpuReferenceError("GEMM_SP attrs must be a dict")
    for key in ("transpose_A", "transpose_E", "transpose_B"):
        if attrs.get(key) not in (None, False, True):
            raise KernelCpuReferenceError(f"GEMM_SP {key} must be boolean")
    _validate_gemm_policy_metadata(attrs.get("policy"))

    a_global = e_global = b_global = c_global = None
    c_staging = None
    c_staging_copy_attrs: dict[str, Any] | None = None
    c_staging_output_attrs: dict[str, Any] | None = None
    fill_seen = False
    for op in ops:
        if not isinstance(op, dict):
            raise KernelCpuReferenceError(f"bad op record {op!r}")
        tir_op = op.get("tir_op")
        args = list(op.get("args", []))
        op_attrs = op.get("attrs") or {}
        if not isinstance(op_attrs, dict):
            raise KernelCpuReferenceError("op attrs must be a dict")
        if "parallel_extents" in op_attrs or "vectorized_extent" in op_attrs:
            raise KernelCpuReferenceError(
                "sparse GEMM_SP CPU reference does not yet support scheduled copy/vectorized bodies"
            )
        if tir_op == "tir.fill_loop" and args and str(args[0]) == c_local:
            if op_attrs.get("value", 0) != 0:
                raise KernelCpuReferenceError("GEMM_SP accumulator fill must be zero")
            fill_seen = True
        elif tir_op == "tir.copy_loop" and len(args) >= 2:
            src, dst = str(args[0]), str(args[1])
            if dst == a_shared:
                a_global = src
            elif dst == e_shared:
                e_global = src
            elif dst == b_shared:
                b_global = src
            elif src == c_local:
                if dst in locals_:
                    c_staging = dst
                    c_staging_copy_attrs = op_attrs
                else:
                    c_global = dst
            elif c_staging is not None and src == c_staging:
                c_global = dst
                c_staging_output_attrs = op_attrs
        elif tir_op == "tir.gemm_sp_expand":
            continue
        elif tir_op == "tir.use_swizzle":
            _validate_swizzle_metadata(op)
            continue
        elif tir_op == "tir.annotate_layout":
            _validate_layout_annotation_noop(op)
            continue
        else:
            raise KernelCpuReferenceError(
                f"unsupported op {tir_op!r} in sparse GEMM_SP CPU reference"
            )
    if not fill_seen or a_global is None or e_global is None or b_global is None or c_global is None:
        raise KernelCpuReferenceError(
            "sparse CPU reference requires zero fill, A_sparse/E/B global copies, and C copy-back"
        )
    for name in (a_global, e_global, b_global, c_global):
        if name not in params or params[name].get("scope") != MemoryScope.GLOBAL.value:
            raise KernelCpuReferenceError(f"GEMM_SP operand {name!r} must be a global param")
    for name in (a_shared, e_shared, b_shared, c_local):
        if name not in locals_:
            raise KernelCpuReferenceError(f"GEMM_SP operand {name!r} must be a local buffer")
    copy_attrs: dict[str, Any] = {}
    if c_staging is not None:
        copy_attrs["c_staging"] = c_staging
    if c_staging_copy_attrs is not None:
        copy_attrs["c_staging_copy"] = dict(c_staging_copy_attrs)
    if c_staging_output_attrs is not None:
        copy_attrs["c_staging_output"] = dict(c_staging_output_attrs)
    return (
        a_global,
        e_global,
        b_global,
        c_global,
        a_shared,
        e_shared,
        b_shared,
        c_local,
        attrs,
        copy_attrs,
    )


def _split_k_span_from_copy_attrs(
    copy_attrs: dict[str, Any],
    *,
    k: int,
    split_k: int,
) -> tuple[int, bool]:
    modes: set[str] = set()
    spans: set[int] = set()
    for key in ("a_copy", "b_copy"):
        attrs = copy_attrs.get(key)
        if not isinstance(attrs, dict):
            continue
        mode = attrs.get("split_k_span_mode")
        span = attrs.get("split_k_span")
        if mode is None and span is None:
            continue
        if mode not in {"floor_div", "ceildiv"} or not isinstance(span, int) or span <= 0:
            raise KernelCpuReferenceError("split-k copy span metadata is malformed")
        modes.add(mode)
        spans.add(span)
    if len(modes) > 1 or len(spans) > 1:
        raise KernelCpuReferenceError("split-k A/B copy span metadata must match")
    if not modes:
        if k % split_k != 0:
            raise KernelCpuReferenceError(
                "split-k atomic GEMM without explicit ceildiv copy span requires "
                f"K divisible by split_k, got K={k}, split_k={split_k}"
            )
        return k // split_k, False
    mode = next(iter(modes))
    span = next(iter(spans))
    if mode == "ceildiv":
        expected = _ceil_div(k, split_k)
        if span != expected:
            raise KernelCpuReferenceError(
                f"split-k ceildiv span {span} does not match ceildiv(K, split_k) {expected}"
            )
        return span, True
    expected_floor = k // split_k
    if span != expected_floor:
        raise KernelCpuReferenceError(
            f"split-k floor-div span {span} does not match K // split_k {expected_floor}"
        )
    if k % split_k != 0:
        raise KernelCpuReferenceError(
            "split-k atomic GEMM with floor-div copy span requires K divisible "
            f"by split_k, got K={k}, split_k={split_k}"
        )
    return span, False


def _validate_swizzle_metadata(op: dict[str, Any]) -> dict[str, Any]:
    attrs = op.get("attrs") or {}
    if not isinstance(attrs, dict):
        raise KernelCpuReferenceError("T.use_swizzle attrs must be a dict")
    enable = attrs.get("enable", True)
    if not isinstance(enable, bool):
        raise KernelCpuReferenceError("T.use_swizzle enable must be boolean")
    panel_size = attrs.get("panel_size")
    if panel_size is not None and (not isinstance(panel_size, int) or panel_size <= 0):
        raise KernelCpuReferenceError("T.use_swizzle panel_size must be a positive integer")
    if enable:
        if panel_size is None:
            raise KernelCpuReferenceError("T.use_swizzle enable=True requires panel_size")
        order = attrs.get("order", "row")
        if order not in {"row", "col"}:
            raise KernelCpuReferenceError("T.use_swizzle order must be 'row' or 'col'")
    return attrs


def _validate_layout_annotation_noop(op: dict[str, Any]) -> None:
    attrs = op.get("attrs") or {}
    if not isinstance(attrs, dict):
        raise KernelCpuReferenceError("T.annotate_layout attrs must be a dict")
    if attrs.get("entries") != 0 or len(attrs) != 1:
        raise KernelCpuReferenceError(
            "CPU reference supports T.annotate_layout only for an empty no-op annotation"
        )


def _gemm_k_loop_range(attrs: dict[str, Any], total_k_tiles: int) -> tuple[int, int, int]:
    has_pipeline = "pipeline_extent" in attrs
    has_serial = "serial_extent" in attrs
    if has_pipeline and has_serial:
        raise KernelCpuReferenceError("GEMM cannot carry both pipeline_extent and serial_extent")
    if has_pipeline:
        extent = attrs.get("pipeline_extent")
        start = attrs.get("pipeline_start", 0)
        unexpected_start = attrs.get("serial_start")
        unexpected_step = attrs.get("serial_step")
        step = attrs.get("pipeline_step", 1)
        label = "pipeline"
        extent_key = "pipeline_extent"
    elif has_serial:
        extent = attrs.get("serial_extent")
        start = attrs.get("serial_start", 0)
        unexpected_start = attrs.get("pipeline_start")
        unexpected_step = attrs.get("pipeline_step")
        step = attrs.get("serial_step", 1)
        label = "serial"
        extent_key = "serial_extent"
    else:
        extent = total_k_tiles
        start = 0
        unexpected_start = None
        unexpected_step = attrs.get("serial_step") or attrs.get("pipeline_step")
        step = 1
        label = "implicit"
        extent_key = "implicit_extent"
    if unexpected_start is not None:
        raise KernelCpuReferenceError("GEMM loop start metadata does not match loop kind")
    if unexpected_step is not None:
        raise KernelCpuReferenceError("GEMM loop step metadata does not match loop kind")
    if not isinstance(start, int) or start < 0:
        raise KernelCpuReferenceError(f"GEMM {label}_start must be a non-negative integer")
    if not isinstance(extent, int) or extent <= 0:
        raise KernelCpuReferenceError(f"GEMM {label}_extent must be a positive integer")
    if isinstance(step, bool) or not isinstance(step, int) or step <= 0:
        raise KernelCpuReferenceError(f"GEMM {label}_step must be a positive integer")
    if start + extent > total_k_tiles:
        raise KernelCpuReferenceError(
            f"GEMM {extent_key} range start={start} extent={extent} exceeds "
            f"ceildiv(K, block_K) {total_k_tiles}"
        )
    return start, extent, step


def execute_scalar_tiled_gemm_reference(
    module: KernelModule | PlainTirModule,
    inputs: Mapping[str, object],
    *,
    entry: str | None = None,
) -> CpuReferenceResult:
    """Execute the current scalar tiled GEMM subset against CPU data.

    ``inputs`` supplies rank-2 numeric matrices for the read global operands.
    The output matrix is produced by the oracle and returned under the global C
    operand name. This function does not execute Metal or claim a launch.
    """
    plain = _coerce_plain(module)
    func = _select_func(plain, entry)
    entry_name = func.get("name")
    if not isinstance(entry_name, str):
        raise KernelCpuReferenceError("plain-TIR function has no name")

    params = _records(func.get("params"), kind="params")
    locals_ = _records(func.get("locals"), kind="locals")
    (
        a_name,
        b_name,
        c_name,
        a_shared,
        b_shared,
        c_local,
        attrs,
        output_mode,
        copy_attrs,
    ) = _find_gemm_pattern(func, params, locals_)

    a_shape = _shape(params[a_name], name=a_name)
    b_shape = _shape(params[b_name], name=b_name)
    c_shape = _shape(params[c_name], name=c_name)
    a_tile = _shape(locals_[a_shared], name=a_shared)
    b_tile = _shape(locals_[b_shared], name=b_shared)
    c_tile = _shape(locals_[c_local], name=c_local)
    c_staging = copy_attrs.get("c_staging")
    c_dtype = params[c_name].get("dtype")
    if not isinstance(c_dtype, str):
        raise KernelCpuReferenceError(f"{c_name}: missing output dtype")
    if output_mode == "copy" and c_dtype not in {"f16", "f32"}:
        raise KernelCpuReferenceError("GEMM copy output currently supports f16/f32 C dtype")
    if output_mode == "atomic_add" and c_dtype != "f32":
        raise KernelCpuReferenceError("split-k atomic GEMM currently supports f32 C dtype only")
    if len(a_shape) != 2 or len(b_shape) != 2 or len(c_shape) != 2:
        raise KernelCpuReferenceError("GEMM CPU reference requires rank-2 A/B/C tensors")
    if len(a_tile) != 2 or len(b_tile) != 2 or len(c_tile) != 2:
        raise KernelCpuReferenceError("GEMM CPU reference requires rank-2 local tiles")
    if c_staging is not None:
        if not isinstance(c_staging, str) or c_staging not in locals_:
            raise KernelCpuReferenceError("GEMM staged C output must use a local buffer")
        c_staging_tile = _shape(locals_[c_staging], name=c_staging)
        if c_staging_tile != c_tile:
            raise KernelCpuReferenceError("GEMM staged C output tile shape must match C_local")
        if locals_[c_staging].get("scope") != MemoryScope.SHARED.value:
            raise KernelCpuReferenceError(f"{c_staging!r} must be shared")
        c_staging_dtype = locals_[c_staging].get("dtype")
        c_local_dtype = locals_[c_local].get("dtype")
        if c_staging_dtype != c_dtype or c_local_dtype not in {c_dtype, "f32"}:
            raise KernelCpuReferenceError(
                "GEMM staged C output currently requires C_shared/C dtype to match "
                "and C_local to be matching or f32 accumulator"
            )

    transpose_a = bool(attrs.get("transpose_A", False))
    transpose_b = bool(attrs.get("transpose_B", False))
    c_m, c_n = c_shape
    c_block_m, c_block_n = c_tile
    m, n = c_m, c_n
    if transpose_a:
        a_k, a_m = a_shape
        a_block_k, block_m = a_tile
        if a_m != m:
            raise KernelCpuReferenceError("GEMM transpose_A expects A(K,M)")
        k = a_k
        block_k = a_block_k
    else:
        a_m, a_k = a_shape
        block_m, block_k = a_tile
        if a_m != m:
            raise KernelCpuReferenceError("GEMM tensors must be A(M,K), B(K,N), C(M,N)")
        k = a_k
    if transpose_b:
        b_n, b_k = b_shape
        b_block_n, b_block_k = b_tile
        if b_n != n or b_k != k:
            raise KernelCpuReferenceError("GEMM transpose_B expects B(N,K)")
        block_n = b_block_n
        if b_block_k != block_k:
            raise KernelCpuReferenceError("GEMM local tile shapes are inconsistent")
    else:
        b_k, b_n = b_shape
        b_block_k, block_n = b_tile
        if b_k != k or b_n != n:
            raise KernelCpuReferenceError("GEMM tensors must be A(M,K), B(K,N), C(M,N)")
        if b_block_k != block_k:
            raise KernelCpuReferenceError("GEMM local tile shapes are inconsistent")
    c_block_m, c_block_n = c_tile
    if c_block_m != block_m or c_block_n != block_n:
        raise KernelCpuReferenceError("GEMM local tile shapes are inconsistent")
    if locals_[a_shared].get("scope") != MemoryScope.SHARED.value:
        raise KernelCpuReferenceError(f"{a_shared!r} must be shared")
    if locals_[b_shared].get("scope") != MemoryScope.SHARED.value:
        raise KernelCpuReferenceError(f"{b_shared!r} must be shared")
    if locals_[c_local].get("scope") not in {MemoryScope.FRAGMENT.value, MemoryScope.LOCAL.value}:
        raise KernelCpuReferenceError(f"{c_local!r} must be fragment/local")

    expected_grid = [_ceil_div(n, block_n), _ceil_div(m, block_m)]
    grid = func.get("grid")
    if not isinstance(grid, list) or len(grid) < 2 or grid[:2] != expected_grid:
        raise KernelCpuReferenceError(
            f"GEMM grid {grid!r} does not match expected {expected_grid}"
        )
    split_k = 1
    k_span = k
    split_k_tail_safe = False
    if output_mode == "atomic_add":
        if len(grid) < 3 or not isinstance(grid[2], int) or grid[2] <= 0:
            raise KernelCpuReferenceError("split-k atomic GEMM requires a positive 3-D grid z")
        split_k = grid[2]
        k_span, split_k_tail_safe = _split_k_span_from_copy_attrs(
            copy_attrs,
            k=k,
            split_k=split_k,
        )
    total_k_tiles = _ceil_div(k_span, block_k)
    k_loop_start, k_loop_extent, k_loop_step = _gemm_k_loop_range(attrs, total_k_tiles)
    _validate_scheduled_tile_copy_metadata(
        func,
        a_global=a_name,
        b_global=b_name,
        c_global=c_name,
        c_staging=c_staging,
        a_shared=a_shared,
        b_shared=b_shared,
        c_local=c_local,
        a_tile=a_tile,
        b_tile=b_tile,
        c_tile=c_tile,
    )

    if a_name not in inputs or b_name not in inputs:
        raise KernelCpuReferenceError(f"inputs must include {a_name!r} and {b_name!r}")
    a_input_shape = (k, m) if transpose_a else (m, k)
    b_input_shape = (n, k) if transpose_b else (k, n)
    a = _matrix(inputs[a_name], name=a_name, shape=a_input_shape)
    b = _matrix(inputs[b_name], name=b_name, shape=b_input_shape)

    out = [[0.0 for _ in range(n)] for _ in range(m)]
    for tile_y in range(expected_grid[1]):
        row0 = tile_y * block_m
        for tile_x in range(expected_grid[0]):
            col0 = tile_x * block_n
            for local_m in range(block_m):
                row = row0 + local_m
                if row >= m:
                    continue
                for local_n in range(block_n):
                    col = col0 + local_n
                    if col >= n:
                        continue
                    for split_index in range(split_k):
                        split_base = split_index * k_span
                        split_end = (
                            min(split_base + k_span, k)
                            if split_k_tail_safe
                            else split_base + k_span
                        )
                        acc = 0.0
                        for ko in range(
                            k_loop_start,
                            k_loop_start + k_loop_extent,
                            k_loop_step,
                        ):
                            k0 = ko * block_k
                            for kk in range(block_k):
                                split_offset = k0 + kk
                                if split_offset < k_span:
                                    k_index = split_base + split_offset
                                    if k_index < split_end:
                                        aval = a[k_index][row] if transpose_a else a[row][k_index]
                                        bval = b[col][k_index] if transpose_b else b[k_index][col]
                                        acc += aval * bval
                        if output_mode == "atomic_add":
                            out[row][col] += acc
                        else:
                            out[row][col] = _quantize_output_value(acc, dtype=c_dtype)

    return CpuReferenceResult(
        entry=entry_name,
        outputs={c_name: tuple(tuple(row) for row in out)},
        tiles_executed=expected_grid[0] * expected_grid[1] * split_k,
        k_tiles=len(range(k_loop_start, k_loop_start + k_loop_extent, k_loop_step)) * split_k,
    )


def _decode_sparse_2_to_4_value(
    a_sparse: Matrix,
    metadata: IntMatrix,
    *,
    row: int,
    k_index: int,
    e_factor: int,
) -> float:
    group = k_index // 4
    offset = k_index % 4
    groups_per_meta = e_factor // 4
    meta_col = group // groups_per_meta
    meta_shift = 4 * (group % groups_per_meta)
    word = metadata[row][meta_col] & 0xFFFF
    code = (word >> meta_shift) & 0xF
    idx0 = code & 0x3
    idx1 = (code >> 2) & 0x3
    sparse_col = group * 2
    if offset == idx0:
        return a_sparse[row][sparse_col]
    if offset == idx1:
        return a_sparse[row][sparse_col + 1]
    return 0.0


def execute_sparse_tiled_gemm_sp_reference(
    module: KernelModule | PlainTirModule,
    inputs: Mapping[str, object],
    *,
    entry: str | None = None,
) -> CpuReferenceResult:
    """Execute the current TileLang ``T.gemm_sp`` 2:4 sparse GEMM subset.

    This is a CPU oracle for the Metal/TIRx route, not sparse Metal execution.
    The supported slice intentionally matches the local TileLang sparse matmul
    benchmark: f16 A/B sparse payloads, int16 metadata, no transposes, and
    copy-back output.
    """
    plain = _coerce_plain(module)
    func = _select_func(plain, entry)
    entry_name = func.get("name")
    if not isinstance(entry_name, str):
        raise KernelCpuReferenceError("plain-TIR function has no name")

    params = _records(func.get("params"), kind="params")
    locals_ = _records(func.get("locals"), kind="locals")
    (
        a_name,
        e_name,
        b_name,
        c_name,
        a_shared,
        e_shared,
        b_shared,
        c_local,
        attrs,
        copy_attrs,
    ) = _find_gemm_sp_pattern(func, params, locals_)

    if any(bool(attrs.get(key, False)) for key in ("transpose_A", "transpose_E", "transpose_B")):
        raise KernelCpuReferenceError("sparse GEMM_SP CPU reference currently supports no transposes")

    a_dtype = params[a_name].get("dtype")
    e_dtype = params[e_name].get("dtype")
    b_dtype = params[b_name].get("dtype")
    c_dtype = params[c_name].get("dtype")
    if a_dtype != "f16" or b_dtype != "f16":
        raise KernelCpuReferenceError("sparse GEMM_SP CPU reference currently supports f16 A/B only")
    if e_dtype not in {"i16", "u16"}:
        raise KernelCpuReferenceError("sparse GEMM_SP CPU reference currently supports int16 metadata only")
    if c_dtype not in {"f16", "f32"}:
        raise KernelCpuReferenceError("sparse GEMM_SP copy output currently supports f16/f32 C dtype")
    c_local_dtype = locals_[c_local].get("dtype")
    if c_local_dtype not in {c_dtype, "f32"}:
        raise KernelCpuReferenceError(
            "sparse GEMM_SP C_local must match C dtype or use an f32 accumulator"
        )

    a_shape = _shape(params[a_name], name=a_name)
    e_shape = _shape(params[e_name], name=e_name)
    b_shape = _shape(params[b_name], name=b_name)
    c_shape = _shape(params[c_name], name=c_name)
    a_tile = _shape(locals_[a_shared], name=a_shared)
    e_tile = _shape(locals_[e_shared], name=e_shared)
    b_tile = _shape(locals_[b_shared], name=b_shared)
    c_tile = _shape(locals_[c_local], name=c_local)
    if len(a_shape) != 2 or len(e_shape) != 2 or len(b_shape) != 2 or len(c_shape) != 2:
        raise KernelCpuReferenceError("sparse GEMM_SP CPU reference requires rank-2 tensors")
    if len(a_tile) != 2 or len(e_tile) != 2 or len(b_tile) != 2 or len(c_tile) != 2:
        raise KernelCpuReferenceError("sparse GEMM_SP CPU reference requires rank-2 local tiles")

    m, n = c_shape
    b_k, b_n = b_shape
    if b_n != n:
        raise KernelCpuReferenceError("sparse GEMM_SP expects B(K,N), C(M,N)")
    k = b_k
    if k % 16 != 0:
        raise KernelCpuReferenceError("sparse GEMM_SP int16 metadata slice requires K divisible by 16")
    if a_shape != (m, k // 2):
        raise KernelCpuReferenceError("sparse GEMM_SP expects A_sparse(M,K//2)")
    if e_shape[0] != m or k % e_shape[1] != 0:
        raise KernelCpuReferenceError("sparse GEMM_SP expects E(M,K//e_factor)")
    e_factor = k // e_shape[1]
    if e_factor != 16:
        raise KernelCpuReferenceError("sparse GEMM_SP int16 metadata slice requires e_factor=16")

    block_m, a_half_block_k = a_tile
    block_k = a_half_block_k * 2
    if block_k % 16 != 0:
        raise KernelCpuReferenceError("sparse GEMM_SP block_K must be divisible by 16")
    if e_tile != (block_m, block_k // e_factor):
        raise KernelCpuReferenceError("sparse GEMM_SP E_shared shape must be (block_M, block_K//e_factor)")
    if b_tile[0] != block_k:
        raise KernelCpuReferenceError("sparse GEMM_SP B_shared block_K does not match A_shared")
    block_n = b_tile[1]
    if c_tile != (block_m, block_n):
        raise KernelCpuReferenceError("sparse GEMM_SP C local tile shape is inconsistent")
    if locals_[a_shared].get("scope") != MemoryScope.SHARED.value:
        raise KernelCpuReferenceError(f"{a_shared!r} must be shared")
    if locals_[e_shared].get("scope") != MemoryScope.SHARED.value:
        raise KernelCpuReferenceError(f"{e_shared!r} must be shared")
    if locals_[b_shared].get("scope") != MemoryScope.SHARED.value:
        raise KernelCpuReferenceError(f"{b_shared!r} must be shared")
    if locals_[c_local].get("scope") not in {MemoryScope.FRAGMENT.value, MemoryScope.LOCAL.value}:
        raise KernelCpuReferenceError(f"{c_local!r} must be fragment/local")
    c_staging = copy_attrs.get("c_staging")
    if c_staging is not None:
        if not isinstance(c_staging, str) or c_staging not in locals_:
            raise KernelCpuReferenceError("sparse GEMM_SP staged C output must use a local buffer")
        if _shape(locals_[c_staging], name=c_staging) != c_tile:
            raise KernelCpuReferenceError("sparse GEMM_SP staged C output tile shape must match C_local")
        if locals_[c_staging].get("scope") != MemoryScope.SHARED.value:
            raise KernelCpuReferenceError(f"{c_staging!r} must be shared")
        if locals_[c_staging].get("dtype") != c_dtype:
            raise KernelCpuReferenceError("sparse GEMM_SP staged C output dtype must match C dtype")

    expected_grid = [_ceil_div(n, block_n), _ceil_div(m, block_m)]
    grid = func.get("grid")
    if not isinstance(grid, list) or grid != expected_grid:
        raise KernelCpuReferenceError(
            f"sparse GEMM_SP grid {grid!r} does not match expected {expected_grid}"
        )
    total_k_tiles = _ceil_div(k, block_k)
    k_loop_start, k_loop_extent, k_loop_step = _gemm_k_loop_range(attrs, total_k_tiles)

    if a_name not in inputs or e_name not in inputs or b_name not in inputs:
        raise KernelCpuReferenceError(
            f"inputs must include {a_name!r}, {e_name!r}, and {b_name!r}"
        )
    a_sparse = _matrix(inputs[a_name], name=a_name, shape=(m, k // 2))
    metadata = _int_matrix(inputs[e_name], name=e_name, shape=(m, k // e_factor))
    b = _matrix(inputs[b_name], name=b_name, shape=(k, n))

    out = [[0.0 for _ in range(n)] for _ in range(m)]
    for tile_y in range(expected_grid[1]):
        row0 = tile_y * block_m
        for tile_x in range(expected_grid[0]):
            col0 = tile_x * block_n
            for local_m in range(block_m):
                row = row0 + local_m
                if row >= m:
                    continue
                for local_n in range(block_n):
                    col = col0 + local_n
                    if col >= n:
                        continue
                    acc = 0.0
                    for ko in range(k_loop_start, k_loop_start + k_loop_extent, k_loop_step):
                        k0 = ko * block_k
                        for kk in range(block_k):
                            k_index = k0 + kk
                            if k_index >= k:
                                continue
                            aval = _decode_sparse_2_to_4_value(
                                a_sparse,
                                metadata,
                                row=row,
                                k_index=k_index,
                                e_factor=e_factor,
                            )
                            if aval:
                                acc += aval * b[k_index][col]
                    out[row][col] = _quantize_output_value(acc, dtype=str(c_dtype))

    return CpuReferenceResult(
        entry=entry_name,
        outputs={c_name: tuple(tuple(row) for row in out)},
        tiles_executed=expected_grid[0] * expected_grid[1],
        k_tiles=len(range(k_loop_start, k_loop_start + k_loop_extent, k_loop_step)),
    )


def _validate_scheduled_tile_copy_metadata(
    func: dict[str, Any],
    *,
    a_global: str,
    b_global: str,
    c_global: str,
    c_staging: str | None,
    a_shared: str,
    b_shared: str,
    c_local: str,
    a_tile: tuple[int, ...],
    b_tile: tuple[int, ...],
    c_tile: tuple[int, ...],
) -> None:
    ops = func.get("ops")
    if not isinstance(ops, list):
        raise KernelCpuReferenceError("plain-TIR function has no ops list")
    allowed = {
        (a_global, a_shared): list(a_tile),
        (b_global, b_shared): list(b_tile),
        (c_local, c_global): list(c_tile),
        (c_global, c_local): list(c_tile),
    }
    if c_staging is not None:
        allowed[(c_local, c_staging)] = list(c_tile)
        allowed[(c_staging, c_global)] = list(c_tile)
    for op in ops:
        if not isinstance(op, dict):
            raise KernelCpuReferenceError(f"bad op record {op!r}")
        attrs = op.get("attrs") or {}
        if not isinstance(attrs, dict):
            raise KernelCpuReferenceError("op attrs must be a dict")
        if "parallel_extents" not in attrs and "vectorized_extent" not in attrs:
            continue
        tir_op = op.get("tir_op")
        if tir_op not in {"tir.copy_loop", "tir.atomic_add"}:
            raise KernelCpuReferenceError(
                "CPU reference supports T.Parallel/T.vectorized only on A/B/C tile copy or atomic-output staging"
            )
        args = list(op.get("args", []))
        if len(args) < 2:
            raise KernelCpuReferenceError("scheduled op must have source and destination")
        if tir_op == "tir.atomic_add":
            key = (str(args[0]), str(args[1]))
        else:
            key = (str(args[0]), str(args[1]))
        if key not in allowed:
            raise KernelCpuReferenceError(
                "CPU reference supports T.Parallel/T.vectorized only on A/B/C tile copy or atomic-output staging"
            )
        tile_shape = allowed[key]
        shape_label = key[1]
        if "parallel_extents" in attrs:
            extents = attrs["parallel_extents"]
            vars_ = attrs.get("parallel_vars")
            if (
                not isinstance(vars_, list)
                or len(vars_) != len(extents)
                or any(not isinstance(var, str) for var in vars_)
            ):
                raise KernelCpuReferenceError("T.Parallel vars must match extents")
            coalesced_width = attrs.get("parallel_coalesced_width")
            if (
                coalesced_width is not None
                and (
                    isinstance(coalesced_width, bool)
                    or not isinstance(coalesced_width, int)
                    or coalesced_width <= 0
                )
            ):
                raise KernelCpuReferenceError("T.Parallel coalesced_width must be a positive integer")
            prefer_async = attrs.get("parallel_prefer_async")
            if prefer_async is True:
                raise KernelCpuReferenceError("T.Parallel prefer_async=True is not supported for Metal")
            if prefer_async is not None and not isinstance(prefer_async, bool):
                raise KernelCpuReferenceError("T.Parallel prefer_async must be a boolean")
            annotations = attrs.get("parallel_annotations")
            if annotations is not None and not isinstance(annotations, dict):
                raise KernelCpuReferenceError("T.Parallel annotations must be a dict")
        else:
            extents = []
        if "vectorized_extent" in attrs:
            extent = attrs["vectorized_extent"]
            var = attrs.get("vectorized_var")
            if not isinstance(var, str):
                raise KernelCpuReferenceError("T.vectorized var must be a string")
            annotations = attrs.get("vectorized_annotations")
            if annotations is not None and not isinstance(annotations, dict):
                raise KernelCpuReferenceError("T.vectorized annotations must be a dict")
            scheduled_extents = [*extents, extent]
        else:
            scheduled_extents = list(extents)
        if scheduled_extents != tile_shape:
            if "parallel_extents" in attrs and "vectorized_extent" not in attrs:
                raise KernelCpuReferenceError(
                    f"T.Parallel extents {extents!r} do not match {shape_label!r} tile shape "
                    f"{tile_shape!r}"
                )
            if "vectorized_extent" in attrs and "parallel_extents" not in attrs:
                raise KernelCpuReferenceError(
                    f"T.vectorized extent {scheduled_extents[-1]!r} does not match "
                    f"{shape_label!r} tile shape {tile_shape!r}"
                )
            raise KernelCpuReferenceError(
                f"scheduled tile-copy extents {scheduled_extents!r} do not match "
                f"{shape_label!r} tile shape {tile_shape!r}"
            )


__all__ = [
    "CpuReferenceResult",
    "KernelCpuReferenceError",
    "execute_static_fill_reference",
    "execute_static_indexed_reference",
    "execute_static_row_reduce_sum_reference",
    "execute_sparse_tiled_gemm_sp_reference",
    "execute_scalar_tiled_gemm_reference",
]
