"""Runtime-source Metal execution for imported TileLang/TIRx GEMM."""

from __future__ import annotations

import struct
import sys

from pcc.kernel_ir.cpu_reference import execute_scalar_tiled_gemm_reference
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.metal_finalize import emit_metal_source
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
    run_metal_source_runtime_package,
)
from pcc.kernel_ir.tilelang_import import import_tilelang_source


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
def matmul_splitk_atomic(M, N, K, split_k=2, block_M=8, block_N=8, block_K=4, dtype=T.float16, accum_dtype=T.float32):
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
            for ko in T.Pipelined(T.ceildiv(K // split_k, block_K), num_stages=0):
                T.copy(A[by * block_M, bz * (K // split_k) + ko * block_K], A_shared)
                T.copy(B[bz * (K // split_k) + ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            for i, j in T.Parallel(block_M, block_N):
                T.atomic_add(C[by * block_M + i, bx * block_N + j], C_local[i, j])
    return gemm_kernel
"""


TILELANG_PARALLEL_COPY_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_parallel_copy(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
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
            T.clear(C_local)
            for ko in T.serial(T.ceildiv(K, block_K)):
                for i, kk in T.Parallel(block_M, block_K):
                    T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)
                T.copy(B[ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_PARALLEL_AB_COPY_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_parallel_ab_copy(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
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
            T.clear(C_local)
            for ko in T.serial(T.ceildiv(K, block_K)):
                for i, kk in T.Parallel(block_M, block_K):
                    T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)
                for kk, j in T.Parallel(block_K, block_N):
                    T.copy(B[ko * block_K + kk, bx * block_N + j], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_PARALLEL_ABC_COPY_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_parallel_abc_copy(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
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
            T.clear(C_local)
            for ko in T.serial(T.ceildiv(K, block_K)):
                for i, kk in T.Parallel(block_M, block_K):
                    T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)
                for kk, j in T.Parallel(block_K, block_N):
                    T.copy(B[ko * block_K + kk, bx * block_N + j], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            for i, j in T.Parallel(block_M, block_N):
                T.copy(C_local, C[by * block_M + i, bx * block_N + j])
    return gemm_kernel
"""


TILELANG_VECTORIZED_ABC_COPY_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_vectorized_abc_copy(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
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
            T.clear(C_local)
            for ko in T.serial(T.ceildiv(K, block_K)):
                for i in T.Parallel(block_M):
                    for kk in T.vectorized(block_K):
                        T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)
                for kk in T.Parallel(block_K):
                    for j in T.vectorized(block_N):
                        T.copy(B[ko * block_K + kk, bx * block_N + j], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            for i in T.Parallel(block_M):
                for j in T.vectorized(block_N):
                    T.copy(C_local, C[by * block_M + i, bx * block_N + j])
    return gemm_kernel
"""


TILELANG_TRANSPOSE_A_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_transpose_a(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((K, M), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=32) as (bx, by):
            A_shared = T.alloc_shared((block_K, block_M), dtype)
            B_shared = T.alloc_shared((block_K, block_N), dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.serial(T.ceildiv(K, block_K)):
                T.copy(A[ko * block_K, by * block_M], A_shared)
                T.copy(B[ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local, True, False)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_TRANSPOSE_B_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_transpose_b(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((N, K), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=32) as (bx, by):
            A_shared = T.alloc_shared((block_M, block_K), dtype)
            B_shared = T.alloc_shared((block_N, block_K), dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.serial(T.ceildiv(K, block_K)):
                T.copy(A[by * block_M, ko * block_K], A_shared)
                T.copy(B[bx * block_N, ko * block_K], B_shared)
                T.gemm(A_shared, B_shared, C_local, False, True)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_TRANSPOSE_AB_MATMUL = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_transpose_ab(M, N, K, block_M=8, block_N=8, block_K=8, dtype=T.float16, accum_dtype=T.float32):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((K, M), dtype),
        B: T.Tensor((N, K), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=32) as (bx, by):
            A_shared = T.alloc_shared((block_K, block_M), dtype)
            B_shared = T.alloc_shared((block_N, block_K), dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.serial(T.ceildiv(K, block_K)):
                T.copy(A[ko * block_K, by * block_M], A_shared)
                T.copy(B[bx * block_N, ko * block_K], B_shared)
                T.gemm(A_shared, B_shared, C_local, True, True)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


def _module(*, m: int = 5, n: int = 7, k: int = 3):
    return import_tilelang_source(
        TILELANG_METAL_MATMUL,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_metal_matmul_runtime",
    )


def _zero_fill_module(*, m: int = 5, n: int = 7, k: int = 3):
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.fill(C_local, 0.0)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_zero_fill_runtime",
    )


def _splitk_atomic_module(*, m: int = 5, n: int = 7, k: int = 16):
    return import_tilelang_source(
        TILELANG_SPLITK_ATOMIC_MATMUL,
        outer_function="matmul_splitk_atomic",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_splitk_atomic_runtime",
    )


def _splitk_atomic_ceildiv_module(*, m: int = 5, n: int = 7, k: int = 17):
    source = TILELANG_SPLITK_ATOMIC_MATMUL.replace(
        "K // split_k",
        "T.ceildiv(K, split_k)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_splitk_atomic",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "split_k": 4},
        module_name="tilelang_splitk_atomic_ceildiv_runtime",
    )


def _splitk_atomic_expanded_ceildiv_alias_module(*, m: int = 5, n: int = 7, k: int = 17):
    source = TILELANG_SPLITK_ATOMIC_MATMUL.replace(
        "    @T.prim_func",
        "    splitK = (K + split_k - 1) // split_k\n\n    @T.prim_func",
    ).replace(
        "K // split_k",
        "splitK",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_splitk_atomic",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "split_k": 4},
        module_name="tilelang_splitk_atomic_expanded_ceildiv_alias_runtime",
    )


def _splitk_atomic_floor_plus_one_ceildiv_alias_module(*, m: int = 5, n: int = 7, k: int = 17):
    source = TILELANG_SPLITK_ATOMIC_MATMUL.replace(
        "    @T.prim_func",
        "    splitK = (K - 1) // split_k + 1\n\n    @T.prim_func",
    ).replace(
        "K // split_k",
        "splitK",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_splitk_atomic",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "split_k": 4},
        module_name="tilelang_splitk_atomic_floor_plus_one_ceildiv_alias_runtime",
    )


def _output_staging_module(*, m: int = 5, n: int = 7, k: int = 3):
    return import_tilelang_source(
        TILELANG_OUTPUT_STAGED_MATMUL,
        outer_function="matmul_output_staging",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_output_staging_runtime",
    )


def _output_staging_f16_transpose_b_module(*, m: int = 5, n: int = 7, k: int = 3):
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
        module_name="tilelang_output_staging_f16_transpose_b_runtime",
    )


def _output_staging_f16_transpose_b_policy_alias_module(
    *,
    m: int = 5,
    n: int = 7,
    k: int = 3,
):
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
        module_name="tilelang_output_staging_f16_transpose_b_policy_alias_runtime",
    )


def _parallel_copy_module(*, m: int = 5, n: int = 7, k: int = 16):
    return import_tilelang_source(
        TILELANG_PARALLEL_COPY_MATMUL,
        outer_function="matmul_parallel_copy",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_parallel_copy_runtime",
    )


def _parallel_ab_copy_module(*, m: int = 5, n: int = 7, k: int = 16):
    return import_tilelang_source(
        TILELANG_PARALLEL_AB_COPY_MATMUL,
        outer_function="matmul_parallel_ab_copy",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_parallel_ab_copy_runtime",
    )


def _parallel_abc_copy_module(*, m: int = 5, n: int = 7, k: int = 16):
    return import_tilelang_source(
        TILELANG_PARALLEL_ABC_COPY_MATMUL,
        outer_function="matmul_parallel_abc_copy",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_parallel_abc_copy_runtime",
    )


def _vectorized_abc_copy_module(*, m: int = 5, n: int = 7, k: int = 16):
    return import_tilelang_source(
        TILELANG_VECTORIZED_ABC_COPY_MATMUL,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_vectorized_abc_copy_runtime",
    )


def _vectorized_abc_copy_nonzero_serial_module(*, m: int = 5, n: int = 7, k: int = 24):
    source = TILELANG_VECTORIZED_ABC_COPY_MATMUL.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(1, T.ceildiv(K, block_K)):",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_vectorized_nonzero_serial_runtime",
    )


def _vectorized_abc_copy_with_annotations_module(*, m: int = 5, n: int = 7, k: int = 16):
    source = TILELANG_VECTORIZED_ABC_COPY_MATMUL.replace(
        "for kk in T.vectorized(block_K):",
        'for kk in T.vectorized(0, block_K, annotations={"pragma_unroll": True}):',
        1,
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_vectorized_annotations_runtime",
    )


def _zero_start_pipelined_module(*, m: int = 5, n: int = 7, k: int = 3):
    source = TILELANG_METAL_MATMUL.replace(
        "T.Pipelined(T.ceildiv(K, block_K), num_stages=0)",
        "T.Pipelined(0, T.ceildiv(K, block_K), num_stages=0)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_zero_start_pipelined_runtime",
    )


def _nonzero_start_pipelined_module(*, m: int = 5, n: int = 7, k: int = 24):
    source = TILELANG_METAL_MATMUL.replace(
        "T.Pipelined(T.ceildiv(K, block_K), num_stages=0)",
        "T.Pipelined(1, T.ceildiv(K, block_K), num_stages=0)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "block_M": 8, "block_N": 8, "block_K": 8},
        module_name="tilelang_nonzero_start_pipelined_runtime",
    )


def _step_serial_module(*, m: int = 5, n: int = 7, k: int = 32):
    source = TILELANG_METAL_MATMUL.replace(
        "for ko in T.Pipelined(T.ceildiv(K, block_K), num_stages=0):",
        "for ko in T.serial(0, T.ceildiv(K, block_K), 2):",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "block_M": 8, "block_N": 8, "block_K": 8},
        module_name="tilelang_step_serial_runtime",
    )


def _nonzero_step_serial_module(*, m: int = 5, n: int = 7, k: int = 40):
    source = TILELANG_METAL_MATMUL.replace(
        "for ko in T.Pipelined(T.ceildiv(K, block_K), num_stages=0):",
        "for ko in T.serial(1, T.ceildiv(K, block_K), 2):",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "block_M": 8, "block_N": 8, "block_K": 8},
        module_name="tilelang_nonzero_step_serial_runtime",
    )


def _step_pipelined_module(*, m: int = 5, n: int = 7, k: int = 32):
    source = TILELANG_METAL_MATMUL.replace(
        "T.Pipelined(T.ceildiv(K, block_K), num_stages=0)",
        "T.Pipelined(0, T.ceildiv(K, block_K), 2, num_stages=0)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "block_M": 8, "block_N": 8, "block_K": 8},
        module_name="tilelang_step_pipelined_runtime",
    )


def _nonzero_step_pipelined_module(*, m: int = 5, n: int = 7, k: int = 40):
    source = TILELANG_METAL_MATMUL.replace(
        "T.Pipelined(T.ceildiv(K, block_K), num_stages=0)",
        "T.Pipelined(1, T.ceildiv(K, block_K), 2, num_stages=0)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "block_M": 8, "block_N": 8, "block_K": 8},
        module_name="tilelang_nonzero_step_pipelined_runtime",
    )


def _disabled_swizzle_module(*, m: int = 5, n: int = 7, k: int = 3):
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.use_swizzle(panel_size=10, enable=False)\n            T.clear(C_local)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_disabled_swizzle_runtime",
    )


def _enabled_swizzle_module(*, m: int = 17, n: int = 19, k: int = 16):
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.use_swizzle(panel_size=2, enable=True)\n            T.clear(C_local)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "block_M": 8, "block_N": 8, "block_K": 8},
        module_name="tilelang_enabled_swizzle_runtime",
    )


def _empty_annotate_layout_module(*, m: int = 5, n: int = 7, k: int = 3):
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        "T.annotate_layout({})\n            T.clear(C_local)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_empty_annotate_layout_runtime",
    )


def _swizzled_annotate_layout_module(*, m: int = 5, n: int = 7, k: int = 3):
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        (
            "T.annotate_layout({\n"
            "                A_shared: tilelang.layout.make_swizzled_layout(A_shared),\n"
            "                B_shared: tilelang.layout.make_swizzled_layout(B_shared),\n"
            "            })\n"
            "            T.clear(C_local)"
        ),
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_swizzled_annotate_layout_runtime",
    )


def _swizzled_padded_annotate_layout_module(*, m: int = 5, n: int = 7, k: int = 3):
    source = TILELANG_METAL_MATMUL.replace(
        "T.clear(C_local)",
        (
            "T.annotate_layout({\n"
            "                A_shared: tilelang.layout.make_swizzled_layout(A_shared),\n"
            "                B_shared: tilelang.layout.make_swizzled_layout(B_shared),\n"
            "            })\n"
            "            T.clear(C_local)"
        ),
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k, "block_M": 8, "block_N": 8, "block_K": 8},
        module_name="tilelang_swizzled_padded_annotate_layout_runtime",
    )


def _transpose_a_module(*, m: int = 5, n: int = 7, k: int = 3):
    return import_tilelang_source(
        TILELANG_TRANSPOSE_A_MATMUL,
        outer_function="matmul_transpose_a",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_transpose_a_runtime",
    )


def _transpose_b_module(*, m: int = 5, n: int = 7, k: int = 3):
    return import_tilelang_source(
        TILELANG_TRANSPOSE_B_MATMUL,
        outer_function="matmul_transpose_b",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_transpose_b_runtime",
    )


def _transpose_ab_module(*, m: int = 5, n: int = 7, k: int = 3):
    return import_tilelang_source(
        TILELANG_TRANSPOSE_AB_MATMUL,
        outer_function="matmul_transpose_ab",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="tilelang_transpose_ab_runtime",
    )


def _inputs(m: int, n: int, k: int):
    a = [[float(((i * 3) + kk) % 7 - 3) for kk in range(k)] for i in range(m)]
    b = [[float(((kk * 2) - j) % 9 - 4) for j in range(n)] for kk in range(k)]
    return a, b


def _inputs_transpose_a(m: int, n: int, k: int):
    a = [[float(((kk * 3) + i) % 7 - 3) for i in range(m)] for kk in range(k)]
    b = [[float(((kk * 2) - j) % 9 - 4) for j in range(n)] for kk in range(k)]
    return a, b


def _inputs_transpose_b(m: int, n: int, k: int):
    a = [[float(((i * 3) + kk) % 7 - 3) for kk in range(k)] for i in range(m)]
    b = [[float(((j * 2) - kk) % 9 - 4) for kk in range(k)] for j in range(n)]
    return a, b


def _f16(value: float) -> float:
    return struct.unpack("<e", struct.pack("<e", float(value)))[0]


def _inputs_transpose_b_fractional_f16(m: int, n: int, k: int):
    a = [[_f16((((i * 5) + kk + 1) % 11 - 5) / 7.0) for kk in range(k)] for i in range(m)]
    b = [[_f16((((j * 3) - kk + 2) % 13 - 6) / 5.0) for kk in range(k)] for j in range(n)]
    return a, b


def _inputs_transpose_ab(m: int, n: int, k: int):
    a = [[float(((kk * 3) + i) % 7 - 3) for i in range(m)] for kk in range(k)]
    b = [[float(((j * 2) - kk) % 9 - 4) for kk in range(k)] for j in range(n)]
    return a, b


def _packed_args(
    *,
    m: int,
    n: int,
    k: int,
    a_elements: int | None = None,
    b_elements: int | None = None,
    c_dtype: str = "f32",
) -> PccPackedArgs:
    c_bytes_per_elem = {"f16": 2, "f32": 4}[c_dtype]
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(
        PccBufferHandle(nbytes=(a_elements if a_elements is not None else m * k) * 2, dtype="f16", device="metal:0")
    )
    args.add_buffer(
        PccBufferHandle(nbytes=(b_elements if b_elements is not None else k * n) * 2, dtype="f16", device="metal:0")
    )
    args.add_buffer(PccBufferHandle(nbytes=m * n * c_bytes_per_elem, dtype=c_dtype, device="metal:0"))
    return args


def _assert_runtime_source_matches_cpu(module, a, b, packed_args, tmp_path, *, m: int, n: int):
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    result = run_metal_source_runtime_package(
        module,
        packed_args,
        tmp_path,
        metal_source=emit_metal_source(module),
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

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED, data
    assert data["runtime_launch_executed"] is True
    assert data["runtime_source_compiled"] is True
    assert data["whole_program_gpu"] is False
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["shape"] == [m, n]
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True


def test_imported_tilelang_gemm_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    result = run_metal_source_runtime_package(
        module,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        metal_source=emit_metal_source(module),
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

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED, data
    assert data["runtime_launch_executed"] is True
    assert data["runtime_source_compiled"] is True
    assert data["whole_program_gpu"] is False
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["shape"] == [m, n]
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True


def test_imported_tilelang_zero_fill_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        return
    m, n, k = 5, 7, 3
    module = _zero_fill_module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_zero_start_pipelined_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal zero-start Pipelined GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _zero_start_pipelined_module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_nonzero_start_pipelined_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal nonzero-start Pipelined GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 24
    module = _nonzero_start_pipelined_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "for (uint ko = 1u; ko < 3u; ++ko)" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_step_serial_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal stepped T.serial GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 32
    module = _step_serial_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 4u; ko += 2u)" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_nonzero_step_serial_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal nonzero stepped T.serial GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 40
    module = _nonzero_step_serial_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "for (uint ko = 1u; ko < 5u; ko += 2u)" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_step_pipelined_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal stepped T.Pipelined GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 32
    module = _step_pipelined_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 4u; ko += 2u)" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_nonzero_step_pipelined_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal nonzero stepped T.Pipelined GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 40
    module = _nonzero_step_pipelined_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "for (uint ko = 1u; ko < 5u; ko += 2u)" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_splitk_atomic_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal split-k atomic GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 16
    module = _splitk_atomic_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint split_k_index = tgid.z;" in source
    assert "atomic_fetch_add_explicit(&C[(row * 7u) + col], acc, memory_order_relaxed);" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_splitk_atomic_ceildiv_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal split-k atomic tail GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 17
    module = _splitk_atomic_ceildiv_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_splitk_atomic_expanded_ceildiv_alias_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal split-k expanded ceildiv alias GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 17
    module = _splitk_atomic_expanded_ceildiv_alias_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_splitk_atomic_floor_plus_one_ceildiv_alias_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal split-k floor-plus-one ceildiv alias GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 17
    module = _splitk_atomic_floor_plus_one_ceildiv_alias_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_output_staged_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal output-staged GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _output_staging_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "C[(row * 7u) + col] = (float)acc;" in source
    assert "C_shared" not in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_output_staged_f16_transpose_b_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal output-staged f16 transpose_B GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _output_staging_f16_transpose_b_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "device half* C [[buffer(2)]]" in source
    assert "swizzle_panel_size = 10u * swizzle_grid_x;" in source
    assert "B[(b_row * 3u) + b_col]" in source
    assert "C[(row * 7u) + col] = (half)acc;" in source
    assert "C_shared" not in source

    a, b = _inputs_transpose_b_fractional_f16(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k, c_dtype="f16"),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_gemmwarp_policy_alias_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal GemmWarpPolicy alias GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _output_staging_f16_transpose_b_policy_alias_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "device half* C [[buffer(2)]]" in source
    assert "swizzle_panel_size = 10u * swizzle_grid_x;" in source
    assert "C[(row * 7u) + col] = (half)acc;" in source

    a, b = _inputs_transpose_b_fractional_f16(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k, c_dtype="f16"),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_disabled_swizzle_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal disabled T.use_swizzle GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _disabled_swizzle_module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_enabled_swizzle_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal enabled T.use_swizzle GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 17, 19, 16
    module = _enabled_swizzle_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "uint swizzle_panel_size = 2u * swizzle_grid_x;" in source
    assert "uint tile_col0 = tile_gid_x * 8u;" in source
    assert "uint tile_row0 = tile_gid_y * 8u;" in source
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_empty_annotate_layout_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal empty T.annotate_layout GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _empty_annotate_layout_module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_swizzled_annotate_layout_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal swizzled T.annotate_layout GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _swizzled_annotate_layout_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "uint a_shared_idx =" in source
    assert "uint b_shared_idx =" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_swizzled_padded_annotate_layout_runtime_source_matches_cpu_oracle(
    tmp_path,
):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal padded swizzled T.annotate_layout GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _swizzled_padded_annotate_layout_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "uint a_shared_idx = ((a_local_m * 8u) + a_local_k);" in source
    assert "uint b_shared_idx = ((b_local_k * 8u) + b_local_n);" in source

    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_transpose_a_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal transpose_A GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _transpose_a_module(m=m, n=n, k=k)
    a, b = _inputs_transpose_a(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k, a_elements=k * m, b_elements=k * n),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_transpose_b_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal transpose_B GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _transpose_b_module(m=m, n=n, k=k)
    a, b = _inputs_transpose_b(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k, a_elements=m * k, b_elements=n * k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_transpose_ab_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal transpose_A+transpose_B GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 3
    module = _transpose_ab_module(m=m, n=n, k=k)
    a, b = _inputs_transpose_ab(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k, a_elements=k * m, b_elements=n * k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_parallel_tile_copy_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal T.Parallel tile-copy GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 16
    module = _parallel_copy_module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    result = run_metal_source_runtime_package(
        module,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        metal_source=emit_metal_source(module),
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

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED, data
    assert data["runtime_launch_executed"] is True
    assert data["runtime_source_compiled"] is True
    assert data["whole_program_gpu"] is False
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["shape"] == [m, n]
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True


def test_imported_tilelang_parallel_ab_tile_copy_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal A/B T.Parallel tile-copy GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 16
    module = _parallel_ab_copy_module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_parallel_abc_tile_copy_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal A/B/C T.Parallel tile-copy GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 16
    module = _parallel_abc_copy_module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_vectorized_abc_tile_copy_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal vectorized A/B/C tile-copy GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 16
    module = _vectorized_abc_copy_module(m=m, n=n, k=k)
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_vectorized_nonzero_serial_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal vectorized nonzero-serial GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 24
    module = _vectorized_abc_copy_nonzero_serial_module(m=m, n=n, k=k)
    source = emit_metal_source(module)
    assert "for (uint ko = 1u; ko < 3u; ++ko)" in source
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )


def test_imported_tilelang_vectorized_annotations_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        verdict = {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "runtime-source Metal vectorized annotations GEMM requires Darwin Metal framework",
        }
        assert verdict["status"] == STATUS_SKIPPED_WITH_REASON
        return

    m, n, k = 5, 7, 16
    module = _vectorized_abc_copy_with_annotations_module(m=m, n=n, k=k)
    a_copy = next(
        op for op in module.funcs[0].body
        if op.op == "copy" and op.args[1] == "A_shared"
    )
    assert a_copy.attrs["vectorized_annotations"] == {"pragma_unroll": True}
    a, b = _inputs(m, n, k)
    _assert_runtime_source_matches_cpu(
        module,
        a,
        b,
        _packed_args(m=m, n=n, k=k),
        tmp_path,
        m=m,
        n=n,
    )
