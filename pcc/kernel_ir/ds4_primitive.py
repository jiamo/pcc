"""First bounded ds4 primitive migration: f32-to-f32 tensor copy.

The pinned ds4 Metal source is an oracle for the selected operation only.  pcc
owns the Kernel IR, TIRx freeze, emitted Metal source, packed arguments, launch,
fence, and readback comparison.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from pcc.kernel_ir.cpu_reference import CpuReferenceResult
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    MemoryScope,
    ScalarParam,
    ScalarType,
    validate_kernel,
)


DS4_COPY_REFERENCE_COMMIT = "80ebbc396aee40eedc1d829222f3362d10fa4c6c"
DS4_COPY_REFERENCE_PATH = "metal/cpy.metal"
DS4_COPY_REFERENCE_SHA256 = (
    "c55ac67377adf3f38b5e40f0dee3008e56901854c41f97640c4b1712bf33f77c"
)
DS4_COPY_REFERENCE_SYMBOL = "kernel_cpy_f32_f32"
PCC_DS4_COPY_ENTRY = "pcc_ds4_copy_f32"


class Ds4PrimitiveError(ValueError):
    """The selected primitive or input is outside the bounded migration."""


@dataclass(frozen=True)
class Ds4CopyReference:
    commit: str
    path: str
    sha256: str
    source_symbol: str
    dtype_in: str = "f32"
    dtype_out: str = "f32"
    semantics: str = "typed row-major element copy"
    source_is_oracle_only: bool = True


def validate_ds4_f32_copy_reference(source: str) -> Ds4CopyReference:
    """Validate the exact pinned ds4 source and selected template instance."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if digest != DS4_COPY_REFERENCE_SHA256:
        raise Ds4PrimitiveError(
            f"ds4 copy oracle hash changed: expected {DS4_COPY_REFERENCE_SHA256}, "
            f"got {digest}"
        )
    required = (
        'host_name("kernel_cpy_f32_f32")',
        "kernel_cpy_t kernel_cpy_t_t<float, float>",
        "dst_data[i00] = (T1) src[0]",
    )
    missing = [needle for needle in required if needle not in source]
    if missing:
        raise Ds4PrimitiveError(f"ds4 f32 copy oracle shape changed: missing={missing}")
    return Ds4CopyReference(
        commit=DS4_COPY_REFERENCE_COMMIT,
        path=DS4_COPY_REFERENCE_PATH,
        sha256=digest,
        source_symbol=DS4_COPY_REFERENCE_SYMBOL,
    )


def build_ds4_f32_copy_module(*, rows: int, cols: int) -> KernelModule:
    """Build pcc-owned Kernel IR matching the selected ds4 copy semantics."""
    elements = _checked_shape(rows, cols)
    threads = min(256, elements)
    grid = (elements + threads - 1) // threads
    func = KernelFunc(
        name=PCC_DS4_COPY_ENTRY,
        params=(
            BufferParam(
                "src",
                ScalarType.F32,
                rank=2,
                shape=(rows, cols),
                scope=MemoryScope.GLOBAL,
            ),
            BufferParam(
                "dst",
                ScalarType.F32,
                rank=2,
                shape=(rows, cols),
                scope=MemoryScope.GLOBAL,
            ),
            ScalarParam("n", ScalarType.U32),
        ),
        body=(
            KernelOp("parallel", ("src", "dst", "n"), {"extent": elements}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(grid,),
        threads=threads,
    )
    return validate_kernel(KernelModule("pcc_ds4_copy_f32_mod", funcs=(func,)))


def build_ds4_f32_copy_args(*, rows: int, cols: int) -> PccPackedArgs:
    elements = _checked_shape(rows, cols)
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(
        PccBufferHandle(nbytes=elements * 4, dtype="f32", device="metal:0")
    )
    args.add_buffer(
        PccBufferHandle(nbytes=elements * 4, dtype="f32", device="metal:0")
    )
    args.add_scalar("u32", elements)
    return args.validate()


def ds4_f32_copy_cpu_oracle(
    matrix: Sequence[Sequence[float]], *, rows: int, cols: int
) -> CpuReferenceResult:
    """Independent CPU value oracle for the selected typed-copy semantics."""
    _checked_shape(rows, cols)
    if len(matrix) != rows:
        raise Ds4PrimitiveError(f"copy oracle expected {rows} rows, got {len(matrix)}")
    normalized: list[tuple[float, ...]] = []
    for row_index, row in enumerate(matrix):
        if len(row) != cols:
            raise Ds4PrimitiveError(
                f"copy oracle row {row_index} expected {cols} columns, got {len(row)}"
            )
        normalized.append(tuple(float(value) for value in row))
    output = tuple(normalized)
    return CpuReferenceResult(
        entry=PCC_DS4_COPY_ENTRY,
        outputs={"dst": output},
        tiles_executed=1,
        k_tiles=1,
        claim_mode=(
            "CPU oracle for pinned ds4 kernel_cpy_f32_f32 semantics; "
            "not ds4 execution and not GPU execution"
        ),
    )


def _checked_shape(rows: int, cols: int) -> int:
    if type(rows) is not int or type(cols) is not int or rows <= 0 or cols <= 0:
        raise Ds4PrimitiveError("copy shape must contain positive int dimensions")
    elements = rows * cols
    if elements > (1 << 32) - 1:
        raise Ds4PrimitiveError("copy element count exceeds the u32 launch ABI")
    return elements


__all__ = [
    "DS4_COPY_REFERENCE_COMMIT",
    "DS4_COPY_REFERENCE_PATH",
    "DS4_COPY_REFERENCE_SHA256",
    "DS4_COPY_REFERENCE_SYMBOL",
    "PCC_DS4_COPY_ENTRY",
    "Ds4CopyReference",
    "Ds4PrimitiveError",
    "build_ds4_f32_copy_args",
    "build_ds4_f32_copy_module",
    "ds4_f32_copy_cpu_oracle",
    "validate_ds4_f32_copy_reference",
]
