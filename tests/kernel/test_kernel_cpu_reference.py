"""CPU reference oracle for the current TileLang/TIRx GEMM subset."""

import pytest

from pcc.kernel_ir.cpu_reference import (
    KernelCpuReferenceError,
    execute_scalar_tiled_gemm_reference,
)
from pcc.kernel_ir.ir import KernelFunc, KernelModule, KernelOp
from pcc.kernel_ir.tilelang_import import import_tilelang_source
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir


TILELANG_METAL_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_simdgroup(M, N, K, block_M=64, block_N=64, block_K=32, dtype=T.float16, accum_dtype=T.float32):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=128) as (bx, by):
            A_shared = T.alloc_shared((block_M, block_K), dtype, scope="shared")
            B_shared = T.alloc_shared((block_K, block_N), dtype, scope="shared")
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.Pipelined(T.ceildiv(K, block_K), num_stages=0):
                T.copy(A[by * block_M, ko * block_K], A_shared)
                T.copy(B[ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


def _matmul_module(*, m: int = 70, n: int = 130, k: int = 33) -> KernelModule:
    return import_tilelang_source(
        TILELANG_METAL_MATMUL,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_metal_matmul_cpu_reference",
    )


def _inputs(m: int, n: int, k: int):
    a = [[float(((i * 7) + kk) % 13 - 6) for kk in range(k)] for i in range(m)]
    b = [[float(((kk * 5) - (j * 3)) % 17 - 8) for j in range(n)] for kk in range(k)]
    return a, b


def _expected(a, b):
    m = len(a)
    k = len(a[0])
    n = len(b[0])
    return tuple(
        tuple(sum(a[i][kk] * b[kk][j] for kk in range(k)) for j in range(n))
        for i in range(m)
    )


def test_cpu_reference_executes_imported_tilelang_gemm_after_tirx_freeze():
    m, n, k = 70, 130, 33
    a, b = _inputs(m, n, k)
    plain = lower_to_plain_tir(_matmul_module(m=m, n=n, k=k), target="metal")

    result = execute_scalar_tiled_gemm_reference(plain, {"A": a, "B": b})
    data = result.to_dict()

    assert data["claim_mode"] == "CPU reference oracle, not GPU execution"
    assert data["runtime_launch_executed"] is False
    assert data["whole_program_gpu"] is False
    assert result.tiles_executed == 6
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b)


def test_cpu_reference_rejects_wrong_input_shape():
    module = _matmul_module(m=5, n=7, k=3)
    a, b = _inputs(5, 7, 3)

    with pytest.raises(KernelCpuReferenceError, match="expected 5 rows"):
        execute_scalar_tiled_gemm_reference(module, {"A": a[:4], "B": b})


def test_cpu_reference_rejects_transposed_b_until_semantics_exist():
    module = _matmul_module(m=5, n=7, k=3)
    func = module.funcs[0]
    body = tuple(
        KernelOp(op.op, op.args, {**op.attrs, "transpose_B": True})
        if op.op == "gemm"
        else op
        for op in func.body
    )
    bad = KernelModule(
        module.name,
        funcs=(
            KernelFunc(
                name=func.name,
                params=func.params,
                locals=func.locals,
                body=body,
                grid=func.grid,
                threads=func.threads,
            ),
        ),
    )
    a, b = _inputs(5, 7, 3)

    with pytest.raises(KernelCpuReferenceError, match="transpose_B"):
        execute_scalar_tiled_gemm_reference(bad, {"A": a, "B": b})


def test_cpu_reference_rejects_bad_pipeline_extent():
    module = _matmul_module(m=5, n=7, k=33)
    func = module.funcs[0]
    body = tuple(
        KernelOp(op.op, op.args, {**op.attrs, "pipeline_extent": 99})
        if op.op in {"copy", "gemm"}
        else op
        for op in func.body
    )
    bad = KernelModule(
        module.name,
        funcs=(
            KernelFunc(
                name=func.name,
                params=func.params,
                locals=func.locals,
                body=body,
                grid=func.grid,
                threads=func.threads,
            ),
        ),
    )
    a, b = _inputs(5, 7, 33)

    with pytest.raises(KernelCpuReferenceError, match="pipeline_extent"):
        execute_scalar_tiled_gemm_reference(bad, {"A": a, "B": b})
