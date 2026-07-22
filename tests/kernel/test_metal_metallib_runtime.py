"""Metallib-backed Metal package execution tests."""

from __future__ import annotations

from pathlib import Path
import struct

import pytest

from pcc.kernel_ir.cpu_reference import execute_scalar_tiled_gemm_reference
from pcc.kernel_ir.cpu_reference import CpuReferenceResult
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    Layout,
    LocalBuffer,
    MemoryScope,
    ScalarParam,
    ScalarType,
)
from pcc.kernel_ir.metal_finalize import emit_metal_simdgroup_gemm_source
from pcc.kernel_ir.metal_metallib_runtime import (
    STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED,
    STATUS_SKIPPED_WITH_REASON,
    run_metal_metallib_runtime_package,
)
from pcc.kernel_ir.tilelang_import import import_tilelang_source

_TILELANG_LOCAL_REASON = (
    None
    if (Path.home() / "tilelang" / "benchmark").is_dir()
    else "local ~/tilelang benchmark checkout not present"
)




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


TILELANG_OUTPUT_STAGED_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_output_staging(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=32) as (bx, by):
            A_shared = T.alloc_shared((block_M, block_K), dtype)
            B_shared = T.alloc_shared((block_K, block_N), dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            C_shared = T.alloc_shared((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.Pipelined(T.ceildiv(K, block_K), num_stages=0):
                T.copy(A[by * block_M, ko * block_K], A_shared)
                T.copy(B[ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            T.copy(C_local, C_shared)
            T.copy(C_shared, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_OUTPUT_STAGED_F16_TRANSPOSE_B_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_output_staging_f16_transpose_b(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    num_stages=0,
    thread_num=32,
    block_rows=2,
    block_cols=1,
    enable_rasteration=True,
    dtype=T.float16,
    accum_dtype=T.float32,
):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((N, K), dtype),
        C: T.Tensor((M, N), dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=thread_num) as (bx, by):
            A_shared = T.alloc_shared((block_M, block_K), dtype)
            B_shared = T.alloc_shared((block_N, block_K), dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            C_shared = T.alloc_shared((block_M, block_N), dtype)
            T.use_swizzle(panel_size=10, enable=enable_rasteration)
            T.clear(C_local)
            for ko in T.Pipelined(T.ceildiv(K, block_K), num_stages=num_stages):
                T.copy(A[by * block_M, ko * block_K], A_shared)
                T.copy(B[bx * block_N, ko * block_K], B_shared)
                T.gemm(
                    A_shared,
                    B_shared,
                    C_local,
                    transpose_B=True,
                    policy=T.GemmWarpPolicy.from_warp_partition(block_rows, block_cols),
                )
            T.copy(C_local, C_shared)
            T.copy(C_shared, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_SPLITK_ATOMIC_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_splitk_atomic(M, N, K, split_k=4, block_M=8, block_N=8, block_K=4, dtype=T.float16, accum_dtype=T.float32):
    splitK = (K - 1) // split_k + 1

    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), split_k, threads=32) as (bx, by, bz):
            A_shared = T.alloc_shared((block_M, block_K), dtype)
            B_shared = T.alloc_shared((block_K, block_N), dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.Pipelined(T.ceildiv(splitK, block_K), num_stages=0):
                T.copy(A[by * block_M, bz * splitK + ko * block_K], A_shared)
                T.copy(B[bz * splitK + ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            for i, j in T.Parallel(block_M, block_N):
                T.atomic_add(C[by * block_M + i, bx * block_N + j], C_local[i, j])
    return gemm_kernel
"""


def _matrix_copy_module() -> KernelModule:
    func = KernelFunc(
        name="copy_kernel",
        params=(
            BufferParam("src", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL),
            BufferParam("dst", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL),
            ScalarParam("n", ScalarType.U32),
        ),
        body=(
            KernelOp("parallel", ("src", "dst", "n"), {"extent": 4}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("matrix_copy_mod", funcs=(func,))


def _matrix_copy_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_scalar("u32", 4)
    return args


def _copy_cpu_reference() -> CpuReferenceResult:
    return CpuReferenceResult(
        entry="copy_kernel",
        outputs={"dst": ((1.0, 2.0), (3.0, 4.0))},
        tiles_executed=1,
        k_tiles=1,
    )


def _tilelang_gemm_module(*, m: int = 5, n: int = 7, k: int = 3) -> KernelModule:
    return import_tilelang_source(
        TILELANG_METAL_MATMUL,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_metal_matmul_metallib_runtime",
    )


def _tilelang_local_metal_benchmark_module(
    *,
    m: int = 5,
    n: int = 7,
    k: int = 3,
) -> KernelModule:
    benchmark_path = (
        Path.home() / "tilelang" / "benchmark" / "matmul_metal" / "benchmark_matmul_metal.py"
    )
    if not benchmark_path.exists():
        pytest.fail(f"local TileLang Metal benchmark reference not found: {benchmark_path}")
    return import_tilelang_source(
        benchmark_path.read_text(encoding="utf-8"),
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={
            "M": m,
            "N": n,
            "K": k,
            "block_M": 8,
            "block_N": 8,
            "block_K": 8,
        },
        module_name="tilelang_local_metal_benchmark_matmul_metallib_runtime",
    )


def _tilelang_local_matmul_nonroller_module(
    *,
    m: int = 5,
    n: int = 7,
    k: int = 3,
) -> KernelModule:
    benchmark_path = Path.home() / "tilelang" / "benchmark" / "matmul" / "benchmark_matmul.py"
    if not benchmark_path.exists():
        pytest.fail(f"local TileLang benchmark reference not found: {benchmark_path}")
    return import_tilelang_source(
        benchmark_path.read_text(encoding="utf-8"),
        outer_function="matmul",
        prim_func="main",
        constants={
            "M": m,
            "N": n,
            "K": k,
            "with_roller": False,
            "block_M": 8,
            "block_N": 8,
            "block_K": 8,
            "num_stages": 1,
            "thread_num": 128,
            "policy": "GemmWarpPolicy.Square",
            "enable_rasteration": False,
        },
        module_name="tilelang_local_matmul_nonroller_metallib_runtime",
    )


def _tilelang_local_matmul_static_roller_module(
    *,
    m: int = 5,
    n: int = 7,
    k: int = 3,
) -> KernelModule:
    benchmark_path = Path.home() / "tilelang" / "benchmark" / "matmul" / "benchmark_matmul.py"
    if not benchmark_path.exists():
        pytest.fail(f"local TileLang benchmark reference not found: {benchmark_path}")
    return import_tilelang_source(
        benchmark_path.read_text(encoding="utf-8"),
        outer_function="matmul",
        prim_func="main",
        constants={
            "M": m,
            "N": n,
            "K": k,
            "with_roller": True,
            "block_M": 8,
            "block_N": 8,
            "block_K": 8,
            "num_stages": 0,
            "thread_num": 32,
            "policy": (2, 1),
            "enable_rasteration": True,
        },
        module_name="tilelang_local_matmul_static_roller_metallib_runtime",
    )


def _tilelang_splitk_floor_plus_one_module(
    *,
    m: int = 5,
    n: int = 7,
    k: int = 17,
) -> KernelModule:
    return import_tilelang_source(
        TILELANG_SPLITK_ATOMIC_MATMUL,
        outer_function="matmul_splitk_atomic",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "split_k": 4},
        module_name="tilelang_splitk_floor_plus_one_metallib_runtime",
    )


def _tilelang_output_staging_module(
    *,
    m: int = 5,
    n: int = 7,
    k: int = 3,
) -> KernelModule:
    return import_tilelang_source(
        TILELANG_OUTPUT_STAGED_MATMUL,
        outer_function="matmul_output_staging",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_output_staging_metallib_runtime",
    )


def _tilelang_output_staging_f16_transpose_b_module(
    *,
    m: int = 5,
    n: int = 7,
    k: int = 3,
) -> KernelModule:
    return import_tilelang_source(
        TILELANG_OUTPUT_STAGED_F16_TRANSPOSE_B_MATMUL,
        outer_function="matmul_output_staging_f16_transpose_b",
        prim_func="gemm_kernel",
        constants={
            "M": m,
            "N": n,
            "K": k,
            "num_stages": 0,
            "thread_num": 32,
            "block_rows": 2,
            "block_cols": 1,
            "enable_rasteration": True,
        },
        module_name="tilelang_output_staging_f16_transpose_b_metallib_runtime",
    )


def _tilelang_output_staging_f16_transpose_b_policy_alias_module(
    *,
    m: int = 5,
    n: int = 7,
    k: int = 3,
) -> KernelModule:
    source = TILELANG_OUTPUT_STAGED_F16_TRANSPOSE_B_MATMUL.replace(
        "import tilelang.language as T",
        "import tilelang.language as T\nfrom tilelang.tileop.base import GemmWarpPolicy",
    ).replace(
        "T.GemmWarpPolicy.from_warp_partition(block_rows, block_cols)",
        "GemmWarpPolicy.FullRow",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_output_staging_f16_transpose_b",
        prim_func="gemm_kernel",
        constants={
            "M": m,
            "N": n,
            "K": k,
            "num_stages": 0,
            "thread_num": 32,
            "enable_rasteration": True,
        },
        module_name="tilelang_output_staging_f16_transpose_b_policy_alias_metallib_runtime",
    )


def _tilelang_gemm_args(*, m: int, n: int, k: int, c_dtype: str = "f32") -> PccPackedArgs:
    c_bytes_per_elem = {"f16": 2, "f32": 4}[c_dtype]
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=m * n * c_bytes_per_elem, dtype=c_dtype, device="metal:0"))
    return args


def _tilelang_inputs(m: int, n: int, k: int):
    a = [[float(((i * 3) + kk) % 7 - 3) for kk in range(k)] for i in range(m)]
    b = [[float(((kk * 2) - j) % 9 - 4) for j in range(n)] for kk in range(k)]
    return a, b


def _f16(value: float) -> float:
    return struct.unpack("<e", struct.pack("<e", float(value)))[0]


def _tilelang_transpose_b_fractional_f16_inputs(m: int, n: int, k: int):
    a = [[_f16((((i * 5) + kk + 1) % 11 - 5) / 7.0) for kk in range(k)] for i in range(m)]
    b = [[_f16((((j * 3) - kk + 2) % 13 - 6) / 5.0) for kk in range(k)] for j in range(n)]
    return a, b


def _simdgroup_micro_gemm_module() -> KernelModule:
    func = KernelFunc(
        name="gemm_kernel",
        params=(
            BufferParam("A", ScalarType.F16, rank=2, shape=(8, 8), scope=MemoryScope.GLOBAL),
            BufferParam("B", ScalarType.F16, rank=2, shape=(8, 8), scope=MemoryScope.GLOBAL),
            BufferParam("C", ScalarType.F32, rank=2, shape=(8, 8), scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer("A_shared", ScalarType.F16, shape=(8, 8), scope=MemoryScope.SHARED, layout=Layout.TILE),
            LocalBuffer("B_shared", ScalarType.F16, shape=(8, 8), scope=MemoryScope.SHARED, layout=Layout.TILE),
            LocalBuffer("C_local", ScalarType.F32, shape=(8, 8), scope=MemoryScope.FRAGMENT, layout=Layout.TILE),
        ),
        body=(
            KernelOp("fill", ("C_local",), {"value": 0}),
            KernelOp("copy", ("A", "A_shared"), {"pipeline_extent": 1, "num_stages": 0}),
            KernelOp("copy", ("B", "B_shared"), {"pipeline_extent": 1, "num_stages": 0}),
            KernelOp("gemm", ("A_shared", "B_shared", "C_local"), {"pipeline_extent": 1, "num_stages": 0}),
            KernelOp("copy", ("C_local", "C")),
        ),
        grid=(1, 1),
        threads=32,
    )
    return KernelModule("simdgroup_micro_gemm_metallib_runtime", funcs=(func,))


def _simdgroup_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=128, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=128, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=256, dtype="f32", device="metal:0"))
    return args


def _simdgroup_inputs():
    a = tuple(tuple(float((row + col) % 5 - 2) for col in range(8)) for row in range(8))
    b = tuple(tuple(float((row * 2 + col) % 7 - 3) for col in range(8)) for row in range(8))
    return a, b


def test_metallib_runtime_package_executes_copy_kernel_or_records_toolchain_skip(tmp_path):
    result = run_metal_metallib_runtime_package(
        _matrix_copy_module(),
        _matrix_copy_args(),
        tmp_path,
        input_matrices={"src": ((1.0, 2.0), (3.0, 4.0))},
        cpu_reference=_copy_cpu_reference(),
        output_name="dst",
        timeout=60.0,
    )

    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.max_abs_error == 0.0

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False


def test_metallib_runtime_package_executes_simdgroup_gemm_or_records_toolchain_skip(
    tmp_path,
):
    module = _simdgroup_micro_gemm_module()
    a, b = _simdgroup_inputs()
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _simdgroup_args(),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
        metal_source_emitter=emit_metal_simdgroup_gemm_source,
        metal_source_tool="pcc.kernel_ir.metal_finalize.emit_metal_simdgroup_gemm_source",
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.package is not None
    assert result.package.finalize.metal_source is not None
    assert "simdgroup_multiply_accumulate" in result.package.finalize.metal_source
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (8, 8)
    assert result.cpu_comparison.max_abs_error == 0.0

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["finalize"]["descriptor"]["steps"][0]["tool"].endswith(
        "emit_metal_simdgroup_gemm_source"
    )
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["cpu_comparison"]["runtime_launch_executed"] is True


def test_metallib_runtime_package_executes_imported_tilelang_gemm_or_records_toolchain_skip(
    tmp_path,
):
    m, n, k = 5, 7, 3
    module = _tilelang_gemm_module(m=m, n=n, k=k)
    a, b = _tilelang_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _tilelang_gemm_args(m=m, n=n, k=k),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (m, n)
    assert result.cpu_comparison.max_abs_error == 0.0

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False


@pytest.mark.pcc_gate(unavailable=_TILELANG_LOCAL_REASON)
def test_metallib_runtime_package_executes_local_tilelang_metal_benchmark_or_records_toolchain_skip(
    tmp_path,
):
    m, n, k = 5, 7, 3
    module = _tilelang_local_metal_benchmark_module(m=m, n=n, k=k)
    a, b = _tilelang_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _tilelang_gemm_args(m=m, n=n, k=k),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (m, n)
    assert result.cpu_comparison.max_abs_error == 0.0
    assert result.package is not None
    assert result.package.finalize.metal_source is not None
    assert "device float* C [[buffer(2)]]" in result.package.finalize.metal_source
    assert "C[(row * 7u) + col] = (float)acc;" in result.package.finalize.metal_source

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False


@pytest.mark.pcc_gate(unavailable=_TILELANG_LOCAL_REASON)
def test_metallib_runtime_package_executes_local_tilelang_matmul_nonroller_or_records_toolchain_skip(
    tmp_path,
):
    m, n, k = 5, 7, 3
    module = _tilelang_local_matmul_nonroller_module(m=m, n=n, k=k)
    a, b = _tilelang_transpose_b_fractional_f16_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _tilelang_gemm_args(m=m, n=n, k=k, c_dtype="f16"),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (m, n)
    assert result.cpu_comparison.max_abs_error == 0.0
    assert result.package is not None
    assert result.package.finalize.metal_source is not None
    assert "device half* C [[buffer(2)]]" in result.package.finalize.metal_source
    assert "swizzle_panel_size" not in result.package.finalize.metal_source
    assert "C[(row * 7u) + col] = (half)acc;" in result.package.finalize.metal_source

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False


@pytest.mark.pcc_gate(unavailable=_TILELANG_LOCAL_REASON)
def test_metallib_runtime_package_executes_local_tilelang_matmul_static_roller_or_records_toolchain_skip(
    tmp_path,
):
    m, n, k = 5, 7, 3
    module = _tilelang_local_matmul_static_roller_module(m=m, n=n, k=k)
    a, b = _tilelang_transpose_b_fractional_f16_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _tilelang_gemm_args(m=m, n=n, k=k, c_dtype="f16"),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (m, n)
    assert result.cpu_comparison.max_abs_error == 0.0
    assert result.package is not None
    assert result.package.finalize.metal_source is not None
    assert "device half* C [[buffer(2)]]" in result.package.finalize.metal_source
    assert "swizzle_panel_size = 10u * swizzle_grid_x;" in result.package.finalize.metal_source
    assert "C[(row * 7u) + col] = (half)acc;" in result.package.finalize.metal_source

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["kernel_entry"] == "pcc_main_kernel"
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False


def test_metallib_runtime_package_executes_imported_tilelang_output_staged_gemm_or_records_toolchain_skip(
    tmp_path,
):
    m, n, k = 5, 7, 3
    module = _tilelang_output_staging_module(m=m, n=n, k=k)
    a, b = _tilelang_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _tilelang_gemm_args(m=m, n=n, k=k),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (m, n)
    assert result.cpu_comparison.max_abs_error == 0.0
    assert result.package is not None
    assert result.package.finalize.metal_source is not None

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False


def test_metallib_runtime_package_executes_imported_tilelang_output_staged_f16_transpose_b_gemm_or_records_toolchain_skip(
    tmp_path,
):
    m, n, k = 5, 7, 3
    module = _tilelang_output_staging_f16_transpose_b_module(m=m, n=n, k=k)
    a, b = _tilelang_transpose_b_fractional_f16_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _tilelang_gemm_args(m=m, n=n, k=k, c_dtype="f16"),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (m, n)
    assert result.cpu_comparison.max_abs_error == 0.0
    assert result.package is not None
    assert result.package.finalize.metal_source is not None
    assert "swizzle_panel_size = 10u * swizzle_grid_x;" in result.package.finalize.metal_source
    assert "C[(row * 7u) + col] = (half)acc;" in result.package.finalize.metal_source

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False


def test_metallib_runtime_package_executes_imported_tilelang_gemmwarp_policy_alias_or_records_toolchain_skip(
    tmp_path,
):
    m, n, k = 5, 7, 3
    module = _tilelang_output_staging_f16_transpose_b_policy_alias_module(m=m, n=n, k=k)
    a, b = _tilelang_transpose_b_fractional_f16_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _tilelang_gemm_args(m=m, n=n, k=k, c_dtype="f16"),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (m, n)
    assert result.cpu_comparison.max_abs_error == 0.0
    assert result.package is not None
    assert result.package.finalize.metal_source is not None
    assert "device half* C [[buffer(2)]]" in result.package.finalize.metal_source
    assert "swizzle_panel_size = 10u * swizzle_grid_x;" in result.package.finalize.metal_source
    assert "C[(row * 7u) + col] = (half)acc;" in result.package.finalize.metal_source

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False


def test_metallib_runtime_package_executes_imported_tilelang_splitk_floor_plus_one_ceildiv_alias_or_records_toolchain_skip(
    tmp_path,
):
    m, n, k = 5, 7, 17
    module = _tilelang_splitk_floor_plus_one_module(m=m, n=n, k=k)
    a, b = _tilelang_inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})

    result = run_metal_metallib_runtime_package(
        module,
        _tilelang_gemm_args(m=m, n=n, k=k),
        tmp_path,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["whole_program_gpu"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_METALLIB_RUNTIME_PACKAGE_EXECUTED, data
    assert result.metallib_produced is True
    assert result.runtime_launch_executed is True
    assert result.runtime_source_compiled is False
    assert result.whole_program_gpu is False
    assert result.allocations_released is True
    assert result.invocation is not None
    assert result.invocation.return_code == 0
    assert result.invocation.fence_completed is True
    assert result.cpu_comparison is not None
    assert result.cpu_comparison.status == "metal_cpu_oracle_match"
    assert result.cpu_comparison.shape == (m, n)
    assert result.cpu_comparison.max_abs_error == 0.0

    package = data["package"]
    assert package["finalize"]["air_produced"] is True
    assert package["finalize"]["metallib_produced"] is True
    assert package["launch_plan"]["metallib_available"] is True
    assert package["runtime_launch_executed"] is False
