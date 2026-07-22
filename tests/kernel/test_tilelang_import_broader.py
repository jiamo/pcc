"""Broader TileLang/TIRx import coverage beyond the first 2-D matmul shape."""

from pathlib import Path
import struct

import pytest

from pcc.kernel_ir.cpu_reference import (
    KernelCpuReferenceError,
    execute_sparse_tiled_gemm_sp_reference,
    execute_scalar_tiled_gemm_reference,
)
from pcc.kernel_ir.metal_finalize import MetalFinalizeError, emit_metal_source
from pcc.kernel_ir.ir import KernelFunc, KernelModule, KernelOp
from pcc.kernel_ir.tilelang_import import TileLangImportError, import_tilelang_source
from pcc.kernel_ir.tirx_adapter import lower_to_plain_tir

_TILELANG_LOCAL_REASON = (
    None
    if (Path.home() / "tilelang" / "benchmark").is_dir()
    else "local ~/tilelang benchmark checkout not present"
)




TILELANG_SPLITK_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_splitk(
    M,
    N,
    K,
    split_k=4,
    block_M=64,
    block_N=64,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
    trans_A=False,
    trans_B=False,
):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(
            T.ceildiv(N, block_N),
            T.ceildiv(M, block_M),
            split_k,
            threads=128,
        ) as (bx, by, bz):
            A_shared = T.alloc_shared((block_M, block_K), dtype)
            B_shared = T.alloc_shared((block_K, block_N), dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            T.clear(C_local)
            for ko in T.Pipelined(T.ceildiv(K // split_k, block_K), num_stages=0):
                T.copy(A[by * block_M, bz * (K // split_k) + ko * block_K], A_shared)
                T.copy(B[bz * (K // split_k) + ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local, trans_A, trans_B)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_SPLITK_ATOMIC_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_splitk_atomic(
    M,
    N,
    K,
    split_k=2,
    block_M=8,
    block_N=8,
    block_K=4,
    dtype=T.float16,
    accum_dtype=T.float32,
):
    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(
            T.ceildiv(N, block_N),
            T.ceildiv(M, block_M),
            split_k,
            threads=32,
        ) as (bx, by, bz):
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


TILELANG_SPLITK_ATOMIC_ALIAS_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_splitk_atomic_alias(
    M,
    N,
    K,
    split_k=2,
    block_M=8,
    block_N=8,
    block_K=4,
    dtype=T.float16,
    accum_dtype=T.float32,
):
    splitK = K // split_k

    @T.prim_func
    def gemm_kernel(
        A: T.Tensor((M, K), dtype),
        B: T.Tensor((K, N), dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(
            T.ceildiv(N, block_N),
            T.ceildiv(M, block_M),
            split_k,
            threads=32,
        ) as (bx, by, bz):
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


TILELANG_EAGER_SPLITK_ATOMIC_ALIAS_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul(A, B, C, block_M, block_N, block_K, split_k, dtype=T.float16, accum_dtype=T.float32, out_dtype=T.float32):
    M, N, K = T.const("M, N, K")
    splitK = K // split_k

    A: T.Tensor((M, K), dtype)
    B: T.Tensor((K, N), dtype)
    C: T.Tensor((M, N), out_dtype)

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
"""


TILELANG_SERIAL_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_serial(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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
                T.copy(A[by * block_M, ko * block_K], A_shared)
                T.copy(B[ko * block_K, bx * block_N], B_shared)
                T.gemm(A_shared, B_shared, C_local)
            T.copy(C_local, C[by * block_M, bx * block_N])
    return gemm_kernel
"""


TILELANG_OUTPUT_STAGED_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_output_staging(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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


TILELANG_OUTPUT_STAGED_F16_TRANSPOSE_B_GEMM_VARIANT = """
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


TILELANG_PARALLEL_COPY_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_parallel_copy(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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


TILELANG_PARALLEL_AB_COPY_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_parallel_ab_copy(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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


TILELANG_PARALLEL_ABC_COPY_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_parallel_abc_copy(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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


TILELANG_VECTORIZED_ABC_COPY_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_vectorized_abc_copy(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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


TILELANG_TRANSPOSE_A_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_transpose_a(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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


TILELANG_TRANSPOSE_B_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_transpose_b(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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


TILELANG_TRANSPOSE_AB_GEMM_VARIANT = """
import tilelang
import tilelang.language as T

@tilelang.jit
def matmul_transpose_ab(
    M,
    N,
    K,
    block_M=8,
    block_N=8,
    block_K=8,
    dtype=T.float16,
    accum_dtype=T.float32,
):
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


def _import_variant(**constants):
    base = {"M": 128, "N": 128, "K": 64}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_SPLITK_GEMM_VARIANT,
        outer_function="matmul_splitk",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_splitk_variant",
    )


def _import_splitk_atomic(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_SPLITK_ATOMIC_GEMM_VARIANT,
        outer_function="matmul_splitk_atomic",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_splitk_atomic_variant",
    )


def _import_splitk_atomic_ceildiv(**constants):
    base = {"M": 5, "N": 7, "K": 17, "split_k": 4}
    base.update(constants)
    source = TILELANG_SPLITK_ATOMIC_GEMM_VARIANT.replace(
        "K // split_k",
        "T.ceildiv(K, split_k)",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_splitk_atomic",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_splitk_atomic_ceildiv_variant",
    )


def _import_splitk_atomic_expanded_ceildiv_alias(**constants):
    base = {"M": 5, "N": 7, "K": 17, "split_k": 4}
    base.update(constants)
    source = TILELANG_SPLITK_ATOMIC_ALIAS_GEMM_VARIANT.replace(
        "splitK = K // split_k",
        "splitK = (K + split_k - 1) // split_k",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_splitk_atomic_alias",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_splitk_atomic_expanded_ceildiv_alias_variant",
    )


def _import_splitk_atomic_floor_plus_one_ceildiv_alias(**constants):
    base = {"M": 5, "N": 7, "K": 17, "split_k": 4}
    base.update(constants)
    source = TILELANG_SPLITK_ATOMIC_ALIAS_GEMM_VARIANT.replace(
        "splitK = K // split_k",
        "splitK = (K - 1) // split_k + 1",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_splitk_atomic_alias",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_splitk_atomic_floor_plus_one_ceildiv_alias_variant",
    )


def _import_output_staging(**constants):
    base = {"M": 5, "N": 7, "K": 3}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_OUTPUT_STAGED_GEMM_VARIANT,
        outer_function="matmul_output_staging",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_output_staging_variant",
    )


def _import_output_staging_f16_transpose_b(**constants):
    base = {
        "M": 5,
        "N": 7,
        "K": 3,
        "num_stages": 0,
        "thread_num": 32,
        "block_rows": 2,
        "block_cols": 1,
        "enable_rasteration": True,
    }
    base.update(constants)
    return import_tilelang_source(
        TILELANG_OUTPUT_STAGED_F16_TRANSPOSE_B_GEMM_VARIANT,
        outer_function="matmul_output_staging_f16_transpose_b",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_output_staging_f16_transpose_b_variant",
    )


def _import_splitk_atomic_alias(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_SPLITK_ATOMIC_ALIAS_GEMM_VARIANT,
        outer_function="matmul_splitk_atomic_alias",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_splitk_atomic_alias_variant",
    )


def _import_eager_splitk_atomic_alias(**constants):
    base = {
        "M": 5,
        "N": 7,
        "K": 16,
        "block_M": 8,
        "block_N": 8,
        "block_K": 4,
        "split_k": 2,
    }
    base.update(constants)
    return import_tilelang_source(
        TILELANG_EAGER_SPLITK_ATOMIC_ALIAS_GEMM_VARIANT,
        outer_function="matmul",
        constants=base,
        module_name="tilelang_eager_splitk_atomic_alias_variant",
    )


def _import_serial(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_SERIAL_GEMM_VARIANT,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_serial_variant",
    )


def _import_parallel_copy(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_PARALLEL_COPY_VARIANT,
        outer_function="matmul_parallel_copy",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_parallel_copy_variant",
    )


def _import_parallel_ab_copy(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_PARALLEL_AB_COPY_VARIANT,
        outer_function="matmul_parallel_ab_copy",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_parallel_ab_copy_variant",
    )


def _import_parallel_abc_copy(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_PARALLEL_ABC_COPY_VARIANT,
        outer_function="matmul_parallel_abc_copy",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_parallel_abc_copy_variant",
    )


def _import_parallel_abc_copy_with_metadata(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    source = TILELANG_PARALLEL_ABC_COPY_VARIANT.replace(
        "for i, kk in T.Parallel(block_M, block_K):",
        (
            'for i, kk in T.Parallel(block_M, block_K, coalesced_width=4, '
            'prefer_async=False, annotations={"pragma_unroll": True}):'
        ),
        1,
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_parallel_abc_copy",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_parallel_metadata_variant",
    )


def _import_vectorized_abc_copy(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_VECTORIZED_ABC_COPY_VARIANT,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_vectorized_abc_copy_variant",
    )


def _import_vectorized_abc_copy_nonzero_serial(**constants):
    base = {"M": 5, "N": 7, "K": 24}
    base.update(constants)
    source = TILELANG_VECTORIZED_ABC_COPY_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(1, T.ceildiv(K, block_K)):",
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_vectorized_nonzero_serial_variant",
    )


def _import_vectorized_abc_copy_with_annotations(**constants):
    base = {"M": 5, "N": 7, "K": 16}
    base.update(constants)
    source = TILELANG_VECTORIZED_ABC_COPY_VARIANT.replace(
        "for kk in T.vectorized(block_K):",
        'for kk in T.vectorized(0, block_K, annotations={"pragma_unroll": True}):',
        1,
    )
    return import_tilelang_source(
        source,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_vectorized_annotations_variant",
    )


def _import_transpose_a(**constants):
    base = {"M": 5, "N": 7, "K": 3}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_TRANSPOSE_A_GEMM_VARIANT,
        outer_function="matmul_transpose_a",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_transpose_a_variant",
    )


def _import_transpose_b(**constants):
    base = {"M": 5, "N": 7, "K": 3}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_TRANSPOSE_B_GEMM_VARIANT,
        outer_function="matmul_transpose_b",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_transpose_b_variant",
    )


def _import_transpose_ab(**constants):
    base = {"M": 5, "N": 7, "K": 3}
    base.update(constants)
    return import_tilelang_source(
        TILELANG_TRANSPOSE_AB_GEMM_VARIANT,
        outer_function="matmul_transpose_ab",
        prim_func="gemm_kernel",
        constants=base,
        module_name="tilelang_transpose_ab_variant",
    )


def _inputs(m: int = 128, n: int = 128, k: int = 64):
    a = [[float((i + kk) % 7) for kk in range(k)] for i in range(m)]
    b = [[float((kk - j) % 5) for j in range(n)] for kk in range(k)]
    return a, b


def _inputs_transpose_a(m: int = 5, n: int = 7, k: int = 3):
    a = [[float(((kk * 3) + i) % 7 - 3) for i in range(m)] for kk in range(k)]
    b = [[float(((kk * 2) - j) % 9 - 4) for j in range(n)] for kk in range(k)]
    return a, b


def _inputs_transpose_b(m: int = 5, n: int = 7, k: int = 3):
    a = [[float(((i * 3) + kk) % 7 - 3) for kk in range(k)] for i in range(m)]
    b = [[float(((j * 2) - kk) % 9 - 4) for kk in range(k)] for j in range(n)]
    return a, b


def _f16(value: float) -> float:
    return struct.unpack("<e", struct.pack("<e", float(value)))[0]


def _inputs_transpose_b_fractional_f16(m: int = 5, n: int = 7, k: int = 3):
    a = [[_f16((((i * 5) + kk + 1) % 11 - 5) / 7.0) for kk in range(k)] for i in range(m)]
    b = [[_f16((((j * 3) - kk + 2) % 13 - 6) / 5.0) for kk in range(k)] for j in range(n)]
    return a, b


def _inputs_transpose_ab(m: int = 5, n: int = 7, k: int = 3):
    a = [[float(((kk * 3) + i) % 7 - 3) for i in range(m)] for kk in range(k)]
    b = [[float(((j * 2) - kk) % 9 - 4) for kk in range(k)] for j in range(n)]
    return a, b


def _expected(a, b, *, k_start: int = 0, k_end: int | None = None):
    m = len(a)
    k = len(a[0])
    if k_end is None:
        k_end = k
    n = len(b[0])
    return tuple(
        tuple(sum(a[i][kk] * b[kk][j] for kk in range(k_start, k_end)) for j in range(n))
        for i in range(m)
    )


def _expected_k_indices(a, b, k_indices):
    m = len(a)
    n = len(b[0])
    return tuple(
        tuple(sum(a[i][kk] * b[kk][j] for kk in k_indices) for j in range(n))
        for i in range(m)
    )


def _expected_transpose_a(a, b):
    k = len(a)
    m = len(a[0])
    n = len(b[0])
    return tuple(
        tuple(sum(a[kk][i] * b[kk][j] for kk in range(k)) for j in range(n))
        for i in range(m)
    )


def _expected_transpose_b(a, b):
    m = len(a)
    k = len(a[0])
    n = len(b)
    return tuple(
        tuple(sum(a[i][kk] * b[j][kk] for kk in range(k)) for j in range(n))
        for i in range(m)
    )


def _expected_transpose_ab(a, b):
    k = len(a)
    m = len(a[0])
    n = len(b)
    return tuple(
        tuple(sum(a[kk][i] * b[j][kk] for kk in range(k)) for j in range(n))
        for i in range(m)
    )


def _expected_f16(matrix):
    return tuple(tuple(_f16(value) for value in row) for row in matrix)


def _signed_i16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value


def _sparse_2_to_4_f16_inputs(m: int = 5, k: int = 16):
    pairs = ((0, 2), (1, 3), (0, 1), (2, 3))
    meta_word = 0
    for group, (idx0, idx1) in enumerate(pairs):
        meta_word |= (idx0 | (idx1 << 2)) << (4 * group)

    sparse_rows = []
    dense_rows = []
    for row in range(m):
        sparse_row = []
        dense_row = [0.0 for _ in range(k)]
        for group, (idx0, idx1) in enumerate(pairs):
            val0 = _f16(((row + 1) * (group + 2)) / 9.0)
            val1 = _f16(-((row + 2) * (group + 1)) / 11.0)
            sparse_row.extend([val0, val1])
            dense_row[(group * 4) + idx0] = val0
            dense_row[(group * 4) + idx1] = val1
        sparse_rows.append(tuple(sparse_row))
        dense_rows.append(tuple(dense_row))
    metadata = tuple((_signed_i16(meta_word),) for _ in range(m))
    return tuple(sparse_rows), metadata, tuple(dense_rows)


def _gemm_op(module):
    return next(op for op in module.funcs[0].body if op.op == "gemm")


def _plain_gemm_op(module):
    plain = lower_to_plain_tir(module, target="metal")
    return next(op for op in plain.funcs[0]["ops"] if op["tir_op"] == "tir.gemm_expand")


def _plain_ops(module):
    return lower_to_plain_tir(module, target="metal").funcs[0]["ops"]


def test_splitk_three_dim_grid_and_positional_gemm_flags_survive_import_and_freeze():
    module = _import_variant()
    func = module.funcs[0]

    assert func.grid == (2, 2, 4)
    assert func.threads == 128
    assert [local.shape for local in func.locals] == [(64, 8), (8, 64), (64, 64)]

    gemm = _gemm_op(module)
    assert gemm.attrs["pipeline_extent"] == 2
    assert gemm.attrs["num_stages"] == 0
    assert gemm.attrs["transpose_A"] is False
    assert gemm.attrs["transpose_B"] is False

    plain = lower_to_plain_tir(module, target="metal")
    assert plain.funcs[0]["grid"] == [2, 2, 4]
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["pipeline_extent"] == 2
    assert plain_gemm["attrs"]["transpose_A"] is False
    assert plain_gemm["attrs"]["transpose_B"] is False


def test_splitk_atomic_add_survives_import_freeze_source_and_cpu_oracle():
    module = _import_splitk_atomic()
    func = module.funcs[0]

    assert func.grid == (1, 1, 2)
    copies = [op for op in func.body if op.op == "copy"]
    assert copies[0].attrs["split_k_span_mode"] == "floor_div"
    assert copies[0].attrs["split_k_span"] == 8
    assert copies[1].attrs["split_k_span_mode"] == "floor_div"
    assert copies[1].attrs["split_k_span"] == 8
    atomic = next(op for op in func.body if op.op == "atomic_add")
    assert atomic.args == ("C", "C_local")
    assert atomic.attrs["parallel_extents"] == [8, 8]
    assert atomic.attrs["parallel_vars"] == ["i", "j"]

    plain_ops = _plain_ops(module)
    plain_atomic = next(op for op in plain_ops if op["tir_op"] == "tir.atomic_add")
    assert plain_atomic["args"] == ["C", "C_local"]
    assert plain_atomic["attrs"]["parallel_extents"] == [8, 8]

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 2
    assert result.k_tiles == 4
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint split_k_index = tgid.z;" in source
    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "atomic_fetch_add_explicit(&C[(row * 7u) + col], acc, memory_order_relaxed);" in source


def test_splitk_atomic_outer_split_alias_survives_import_freeze_source_and_cpu_oracle():
    module = _import_splitk_atomic_alias()
    func = module.funcs[0]

    assert func.grid == (1, 1, 2)
    gemm = _gemm_op(module)
    assert gemm.attrs["pipeline_extent"] == 2
    copies = [op for op in func.body if op.op == "copy"]
    assert copies[0].attrs["split_k_span_mode"] == "floor_div"
    assert copies[0].attrs["split_k_span"] == 8
    assert copies[1].attrs["split_k_span_mode"] == "floor_div"
    assert copies[1].attrs["split_k_span"] == 8
    atomic = next(op for op in func.body if op.op == "atomic_add")
    assert atomic.attrs["parallel_extents"] == [8, 8]

    plain_copies = [op for op in _plain_ops(module) if op["tir_op"] == "tir.copy_loop"]
    assert plain_copies[0]["attrs"]["split_k_span_mode"] == "floor_div"
    assert plain_copies[0]["attrs"]["split_k_span"] == 8

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 2
    assert result.k_tiles == 4
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "uint split_k_index = tgid.z;" in source
    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "atomic_fetch_add_explicit(&C[(row * 7u) + col], acc, memory_order_relaxed);" in source


def test_splitk_atomic_outer_floor_div_alias_tail_fails_closed():
    module = _import_splitk_atomic_alias(K=17, split_k=4)
    func = module.funcs[0]
    copies = [op for op in func.body if op.op == "copy"]
    assert copies[0].attrs["split_k_span_mode"] == "floor_div"
    assert copies[0].attrs["split_k_span"] == 4

    a, b = _inputs(m=5, n=7, k=17)
    with pytest.raises(KernelCpuReferenceError, match="floor-div copy span requires K divisible"):
        execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="floor-div copy span requires K divisible"):
        emit_metal_source(module)


def test_eager_splitk_atomic_alias_source_survives_import_freeze_source_and_cpu_oracle():
    module = _import_eager_splitk_atomic_alias()
    func = module.funcs[0]

    assert func.name == "matmul"
    assert [param.name for param in func.params] == ["A", "B", "C"]
    assert [param.shape for param in func.params] == [(5, 16), (16, 7), (5, 7)]
    assert func.grid == (1, 1, 2)
    assert func.threads == 32
    gemm = _gemm_op(module)
    assert gemm.attrs["pipeline_extent"] == 2
    copies = [op for op in func.body if op.op == "copy"]
    assert copies[0].attrs["split_k_span_mode"] == "floor_div"
    assert copies[0].attrs["split_k_span"] == 8
    assert copies[1].attrs["split_k_span_mode"] == "floor_div"
    assert copies[1].attrs["split_k_span"] == 8
    atomic = next(op for op in func.body if op.op == "atomic_add")
    assert atomic.attrs["parallel_extents"] == [8, 8]

    plain = lower_to_plain_tir(module, target="metal")
    assert plain.funcs[0]["params"][0]["shape"] == [5, 16]
    plain_copies = [op for op in plain.funcs[0]["ops"] if op["tir_op"] == "tir.copy_loop"]
    assert plain_copies[0]["attrs"]["split_k_span_mode"] == "floor_div"

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 2
    assert result.k_tiles == 4
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint split_k_index = tgid.z;" in source
    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "atomic_fetch_add_explicit(&C[(row * 7u) + col], acc, memory_order_relaxed);" in source


def test_splitk_atomic_ceildiv_tail_survives_import_freeze_source_and_cpu_oracle():
    module = _import_splitk_atomic_ceildiv()
    func = module.funcs[0]

    assert func.grid == (1, 1, 4)
    copies = [op for op in func.body if op.op == "copy"]
    assert copies[0].attrs["split_k_span_mode"] == "ceildiv"
    assert copies[0].attrs["split_k_span"] == 5
    assert copies[1].attrs["split_k_span_mode"] == "ceildiv"
    assert copies[1].attrs["split_k_span"] == 5
    plain_copies = [op for op in _plain_ops(module) if op["tir_op"] == "tir.copy_loop"]
    assert plain_copies[0]["attrs"]["split_k_span_mode"] == "ceildiv"
    assert plain_copies[0]["attrs"]["split_k_span"] == 5

    a, b = _inputs(m=5, n=7, k=17)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 4
    assert result.k_tiles == 8
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "atomic_fetch_add_explicit(&C[(row * 7u) + col], acc, memory_order_relaxed);" in source


def test_splitk_atomic_expanded_ceildiv_alias_survives_import_freeze_source_and_cpu_oracle():
    module = _import_splitk_atomic_expanded_ceildiv_alias()
    func = module.funcs[0]

    assert func.grid == (1, 1, 4)
    copies = [op for op in func.body if op.op == "copy"]
    assert copies[0].attrs["split_k_span_mode"] == "ceildiv"
    assert copies[0].attrs["split_k_span"] == 5
    assert copies[1].attrs["split_k_span_mode"] == "ceildiv"
    assert copies[1].attrs["split_k_span"] == 5
    plain_copies = [op for op in _plain_ops(module) if op["tir_op"] == "tir.copy_loop"]
    assert plain_copies[0]["attrs"]["split_k_span_mode"] == "ceildiv"
    assert plain_copies[0]["attrs"]["split_k_span"] == 5

    a, b = _inputs(m=5, n=7, k=17)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 4
    assert result.k_tiles == 8
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "atomic_fetch_add_explicit(&C[(row * 7u) + col], acc, memory_order_relaxed);" in source


def test_splitk_atomic_floor_plus_one_ceildiv_alias_survives_import_freeze_source_and_cpu_oracle():
    module = _import_splitk_atomic_floor_plus_one_ceildiv_alias()
    func = module.funcs[0]

    assert func.grid == (1, 1, 4)
    copies = [op for op in func.body if op.op == "copy"]
    assert copies[0].attrs["split_k_span_mode"] == "ceildiv"
    assert copies[0].attrs["split_k_span"] == 5
    assert copies[1].attrs["split_k_span_mode"] == "ceildiv"
    assert copies[1].attrs["split_k_span"] == 5
    plain_copies = [op for op in _plain_ops(module) if op["tir_op"] == "tir.copy_loop"]
    assert plain_copies[0]["attrs"]["split_k_span_mode"] == "ceildiv"
    assert plain_copies[0]["attrs"]["split_k_span"] == 5

    a, b = _inputs(m=5, n=7, k=17)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 4
    assert result.k_tiles == 8
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "atomic_fetch_add_explicit(&C[(row * 7u) + col], acc, memory_order_relaxed);" in source


def test_output_staged_gemm_survives_import_freeze_source_and_cpu_oracle():
    module = _import_output_staging()
    func = module.funcs[0]

    assert any(local.name == "C_shared" for local in func.locals)
    copies = [op for op in func.body if op.op == "copy"]
    assert ("C_local", "C_shared") in [op.args for op in copies]
    assert ("C_shared", "C") in [op.args for op in copies]
    plain_copies = [op for op in _plain_ops(module) if op["tir_op"] == "tir.copy_loop"]
    assert ("C_local", "C_shared") in [
        (str(op["args"][0]), str(op["args"][1])) for op in plain_copies
    ]
    assert ("C_shared", "C") in [
        (str(op["args"][0]), str(op["args"][1])) for op in plain_copies
    ]

    a, b = _inputs(m=5, n=7, k=3)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 1
    assert result.k_tiles == 1
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "C[(row * 7u) + col] = (float)acc;" in source
    assert "C_shared" not in source


def test_output_staged_f16_transpose_b_gemm_casts_accumulator_to_half_output():
    module = _import_output_staging_f16_transpose_b()
    func = module.funcs[0]

    assert func.threads == 32
    c_param = next(param for param in func.params if param.name == "C")
    c_local = next(local for local in func.locals if local.name == "C_local")
    c_shared = next(local for local in func.locals if local.name == "C_shared")
    assert c_param.dtype.value == "f16"
    assert c_local.dtype.value == "f32"
    assert c_shared.dtype.value == "f16"
    gemm = _gemm_op(module)
    assert gemm.attrs["transpose_B"] is True
    assert gemm.attrs["policy"] == (2, 1)
    assert gemm.attrs["num_stages"] == 0
    swizzle = next(op for op in func.body if op.op == "swizzle")
    assert swizzle.attrs["panel_size"] == 10
    assert swizzle.attrs["enable"] is True
    plain_swizzle = next(op for op in _plain_ops(module) if op["tir_op"] == "tir.use_swizzle")
    assert plain_swizzle["attrs"]["panel_size"] == 10
    assert plain_swizzle["attrs"]["enable"] is True

    a, b = _inputs_transpose_b_fractional_f16()
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 1
    assert result.k_tiles == 1
    assert result.outputs["C"] == _expected_f16(_expected_transpose_b(a, b))

    source = emit_metal_source(module)
    assert "device half* C [[buffer(2)]]" in source
    assert "swizzle_panel_size = 10u * swizzle_grid_x;" in source
    assert "B[(b_row * 3u) + b_col]" in source
    assert "C[(row * 7u) + col] = (half)acc;" in source
    assert "C_shared" not in source


@pytest.mark.pcc_gate(unavailable=_TILELANG_LOCAL_REASON)
def test_local_tilelang_matmul_benchmark_source_imports_freezes_and_emits_scalar_metal():
    benchmark_path = Path.home() / "tilelang" / "benchmark" / "matmul" / "benchmark_matmul.py"
    if not benchmark_path.exists():
        pytest.fail(f"local TileLang benchmark reference not found: {benchmark_path}")

    module = import_tilelang_source(
        benchmark_path.read_text(encoding="utf-8"),
        outer_function="matmul",
        prim_func="main",
        constants={
            "M": 5,
            "N": 7,
            "K": 3,
            "with_roller": True,
            "block_M": 8,
            "block_N": 8,
            "block_K": 8,
            "num_stages": 0,
            "thread_num": 32,
            "policy": (2, 1),
            "enable_rasteration": True,
        },
        module_name="tilelang_local_benchmark_matmul_variant",
    )
    func = module.funcs[0]

    assert func.name == "main"
    assert func.grid == (1, 1)
    assert func.threads == 32
    gemm = _gemm_op(module)
    assert gemm.attrs["transpose_B"] is True
    assert gemm.attrs["policy"] == (2, 1)
    swizzle = next(op for op in func.body if op.op == "swizzle")
    assert swizzle.attrs["panel_size"] == 10
    assert swizzle.attrs["enable"] is True
    plain_swizzle = next(op for op in _plain_ops(module) if op["tir_op"] == "tir.use_swizzle")
    assert plain_swizzle["attrs"]["panel_size"] == 10
    assert plain_swizzle["attrs"]["enable"] is True

    a, b = _inputs_transpose_b_fractional_f16()
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 1
    assert result.k_tiles == 1
    assert result.outputs["C"] == _expected_f16(_expected_transpose_b(a, b))

    source = emit_metal_source(module)
    assert "device half* C [[buffer(2)]]" in source
    assert "swizzle_panel_size = 10u * swizzle_grid_x;" in source
    assert "B[(b_row * 3u) + b_col]" in source
    assert "C[(row * 7u) + col] = (half)acc;" in source


@pytest.mark.pcc_gate(unavailable=_TILELANG_LOCAL_REASON)
def test_local_tilelang_matmul_benchmark_nonroller_config_imports_freezes_and_emits_scalar_metal():
    benchmark_path = Path.home() / "tilelang" / "benchmark" / "matmul" / "benchmark_matmul.py"
    if not benchmark_path.exists():
        pytest.fail(f"local TileLang benchmark reference not found: {benchmark_path}")

    module = import_tilelang_source(
        benchmark_path.read_text(encoding="utf-8"),
        outer_function="matmul",
        prim_func="main",
        constants={
            "M": 5,
            "N": 7,
            "K": 3,
            "with_roller": False,
            "block_M": 8,
            "block_N": 8,
            "block_K": 8,
            "num_stages": 1,
            "thread_num": 128,
            "policy": "GemmWarpPolicy.Square",
            "enable_rasteration": False,
        },
        module_name="tilelang_local_benchmark_matmul_nonroller_variant",
    )
    func = module.funcs[0]

    assert func.name == "main"
    assert func.grid == (1, 1)
    assert func.threads == 128
    gemm = _gemm_op(module)
    assert gemm.attrs["transpose_B"] is True
    assert gemm.attrs["policy"] == "GemmWarpPolicy.Square"
    assert gemm.attrs["num_stages"] == 1
    swizzle = next(op for op in func.body if op.op == "swizzle")
    assert swizzle.attrs["panel_size"] == 10
    assert swizzle.attrs["enable"] is False
    plain_swizzle = next(op for op in _plain_ops(module) if op["tir_op"] == "tir.use_swizzle")
    assert plain_swizzle["attrs"]["panel_size"] == 10
    assert plain_swizzle["attrs"]["enable"] is False

    a, b = _inputs_transpose_b_fractional_f16()
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 1
    assert result.k_tiles == 1
    assert result.outputs["C"] == _expected_f16(_expected_transpose_b(a, b))

    source = emit_metal_source(module)
    assert "device half* C [[buffer(2)]]" in source
    assert "swizzle_panel_size" not in source
    assert "B[(b_row * 3u) + b_col]" in source
    assert "C[(row * 7u) + col] = (half)acc;" in source


@pytest.mark.pcc_gate(unavailable=_TILELANG_LOCAL_REASON)
def test_local_tilelang_metal_matmul_benchmark_source_imports_freezes_and_emits_scalar_metal():
    benchmark_path = (
        Path.home() / "tilelang" / "benchmark" / "matmul_metal" / "benchmark_matmul_metal.py"
    )
    if not benchmark_path.exists():
        pytest.fail(f"local TileLang Metal benchmark reference not found: {benchmark_path}")

    module = import_tilelang_source(
        benchmark_path.read_text(encoding="utf-8"),
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={
            "M": 5,
            "N": 7,
            "K": 3,
            "block_M": 8,
            "block_N": 8,
            "block_K": 8,
        },
        module_name="tilelang_local_metal_benchmark_matmul_variant",
    )
    func = module.funcs[0]

    assert func.name == "gemm_kernel"
    assert func.grid == (1, 1)
    assert func.threads == 128
    gemm = _gemm_op(module)
    assert gemm.attrs.get("transpose_A", False) is False
    assert gemm.attrs.get("transpose_B", False) is False
    plain_gemm = next(op for op in _plain_ops(module) if op["tir_op"] == "tir.gemm_expand")
    assert plain_gemm["attrs"].get("transpose_A", False) is False
    assert plain_gemm["attrs"].get("transpose_B", False) is False

    a, b = _inputs(5, 7, 3)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 1
    assert result.k_tiles == 1
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "device half* A [[buffer(0)]]" in source
    assert "device half* B [[buffer(1)]]" in source
    assert "device float* C [[buffer(2)]]" in source
    assert "B[(b_row * 7u) + b_col]" in source
    assert "C[(row * 7u) + col] = (float)acc;" in source


@pytest.mark.pcc_gate(unavailable=_TILELANG_LOCAL_REASON)
def test_local_tilelang_fp8_matmul_benchmark_reaches_dtype_boundary_without_runtime_import():
    benchmark_path = Path.home() / "tilelang" / "benchmark" / "matmul_fp8" / "benchmark_matmul.py"
    if not benchmark_path.exists():
        pytest.fail(f"local TileLang fp8 benchmark reference not found: {benchmark_path}")

    with pytest.raises(TileLangImportError, match="unsupported TileLang dtype 'float8_e4m3fn'"):
        import_tilelang_source(
            benchmark_path.read_text(encoding="utf-8"),
            outer_function="matmul",
            prim_func="main",
            constants={
                "M": 5,
                "N": 7,
                "K": 3,
                "with_roller": False,
                "block_M": 8,
                "block_N": 8,
                "block_K": 8,
                "num_stages": 0,
                "thread_num": 32,
                "k_pack": 1,
                "policy": "GemmWarpPolicy.Square",
                "enable_rasteration": False,
                "torch.version.hip": None,
            },
            module_name="tilelang_local_fp8_benchmark_matmul_boundary",
        )


@pytest.mark.pcc_gate(unavailable=_TILELANG_LOCAL_REASON)
def test_local_tilelang_sparse_matmul_benchmark_imports_tirx_cpu_oracle_and_metal_fail_closed():
    benchmark_path = Path.home() / "tilelang" / "benchmark" / "matmul" / "benchmark_matmul_sp.py"
    if not benchmark_path.exists():
        pytest.fail(f"local TileLang sparse matmul benchmark reference not found: {benchmark_path}")

    module = import_tilelang_source(
        benchmark_path.read_text(encoding="utf-8"),
        outer_function="matmul_sp",
        prim_func="main",
        constants={
            "M": 5,
            "N": 7,
            "K": 16,
            "in_dtype": "float16",
            "accum_dtype": "float32",
            "e_dtype": "int16",
            "e_factor": 16,
            "block_M": 8,
            "block_N": 8,
            "block_K": 16,
            "num_stages": 0,
            "thread_num": 32,
            "policy": "GemmWarpPolicy.Square",
            "enable_rasterization": False,
        },
        module_name="tilelang_local_sparse_matmul_gemm_sp_cpu_oracle",
    )

    func = module.funcs[0]
    assert any(op.op == "gemm_sp" for op in func.body)
    plain_ops = _plain_ops(module)
    plain_sp = next(op for op in plain_ops if op["tir_op"] == "tir.gemm_sp_expand")
    assert plain_sp["args"] == ["A_shared", "E_shared", "B_shared", "C_local"]
    assert plain_sp["attrs"]["policy"] == "GemmWarpPolicy.Square"
    assert plain_sp["attrs"]["transpose_A"] is False
    assert plain_sp["attrs"]["transpose_E"] is False
    assert plain_sp["attrs"]["transpose_B"] is False

    a_sparse, e, a_dense = _sparse_2_to_4_f16_inputs(m=5, k=16)
    b = tuple(
        tuple(_f16((((kk + 3) * (j + 1)) % 17 - 8) / 13.0) for j in range(7))
        for kk in range(16)
    )
    result = execute_sparse_tiled_gemm_sp_reference(
        module,
        {"A_sparse": a_sparse, "E": e, "B": b},
    )
    assert result.outputs["C"] == _expected(a_dense, b)
    assert result.tiles_executed == 1
    assert result.k_tiles == 1

    metal_source = emit_metal_source(module)
    assert "ushort metadata_word = ushort(E[row]);" in metal_source
    assert "simdgroup" not in metal_source


def test_imported_gemmwarp_policy_alias_survives_import_freeze_source_and_cpu_oracle():
    source = TILELANG_OUTPUT_STAGED_F16_TRANSPOSE_B_GEMM_VARIANT.replace(
        "import tilelang.language as T",
        "import tilelang.language as T\nfrom tilelang.tileop.base import GemmWarpPolicy",
    ).replace(
        "T.GemmWarpPolicy.from_warp_partition(block_rows, block_cols)",
        "GemmWarpPolicy.FullRow",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_output_staging_f16_transpose_b",
        prim_func="gemm_kernel",
        constants={
            "M": 5,
            "N": 7,
            "K": 3,
            "num_stages": 0,
            "thread_num": 32,
            "enable_rasteration": True,
        },
        module_name="tilelang_imported_gemmwarp_policy_alias_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["transpose_B"] is True
    assert gemm.attrs["policy"] == "GemmWarpPolicy.FullRow"
    plain_gemm = next(op for op in _plain_ops(module) if op["tir_op"] == "tir.gemm_expand")
    assert plain_gemm["attrs"]["policy"] == "GemmWarpPolicy.FullRow"

    a, b = _inputs_transpose_b_fractional_f16()
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.tiles_executed == 1
    assert result.k_tiles == 1
    assert result.outputs["C"] == _expected_f16(_expected_transpose_b(a, b))

    source_text = emit_metal_source(module)
    assert "device half* C [[buffer(2)]]" in source_text
    assert "swizzle_panel_size = 10u * swizzle_grid_x;" in source_text
    assert "C[(row * 7u) + col] = (half)acc;" in source_text


def test_output_staged_f16_transpose_b_gemm_policy_metadata_fails_closed():
    source = TILELANG_OUTPUT_STAGED_F16_TRANSPOSE_B_GEMM_VARIANT.replace(
        "T.GemmWarpPolicy.from_warp_partition(block_rows, block_cols)",
        "(1, True)",
    )
    with pytest.raises(TileLangImportError, match="T.gemm policy metadata"):
        import_tilelang_source(
            source,
            outer_function="matmul_output_staging_f16_transpose_b",
            prim_func="gemm_kernel",
            constants={
                "M": 5,
                "N": 7,
                "K": 3,
                "num_stages": 0,
                "thread_num": 32,
                "block_rows": 2,
                "block_cols": 1,
                "enable_rasteration": True,
            },
            module_name="tilelang_output_staging_f16_transpose_b_bad_policy_variant",
        )


def test_output_staged_f16_transpose_b_gemm_warp_partition_call_fails_closed():
    source = TILELANG_OUTPUT_STAGED_F16_TRANSPOSE_B_GEMM_VARIANT.replace(
        "T.GemmWarpPolicy.from_warp_partition(block_rows, block_cols)",
        "T.GemmWarpPolicy.from_warp_partition(0, block_cols)",
    )
    with pytest.raises(TileLangImportError, match="from_warp_partition m_warp"):
        import_tilelang_source(
            source,
            outer_function="matmul_output_staging_f16_transpose_b",
            prim_func="gemm_kernel",
            constants={
                "M": 5,
                "N": 7,
                "K": 3,
                "num_stages": 0,
                "thread_num": 32,
                "block_cols": 1,
                "enable_rasteration": True,
            },
            module_name="tilelang_output_staging_f16_transpose_b_bad_policy_variant",
        )


def test_splitk_atomic_floor_div_tail_fails_closed():
    module = _import_splitk_atomic(K=17, split_k=4)
    a, b = _inputs(m=5, n=7, k=17)
    with pytest.raises(KernelCpuReferenceError, match="floor-div copy span requires K divisible"):
        execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="floor-div copy span requires K divisible"):
        emit_metal_source(module)


def test_serial_k_loop_survives_import_freeze_source_and_cpu_oracle():
    module = _import_serial()
    gemm = _gemm_op(module)

    assert gemm.attrs["serial_extent"] == 2
    assert "pipeline_extent" not in gemm.attrs
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["serial_extent"] == 2

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "simdgroup" not in source


def test_zero_start_two_arg_serial_loop_matches_one_arg_serial_semantics():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(0, T.ceildiv(K, block_K)):",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 16},
        module_name="tilelang_zero_start_serial_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["serial_extent"] == 2
    assert _plain_gemm_op(module)["attrs"]["serial_extent"] == 2

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected(a, b)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in emit_metal_source(module)


def test_zero_start_two_arg_pipelined_loop_matches_one_arg_pipeline_semantics():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.Pipelined(0, T.ceildiv(K, block_K), num_stages=0):",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 16},
        module_name="tilelang_zero_start_pipelined_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["pipeline_extent"] == 2
    assert gemm.attrs["num_stages"] == 0
    assert "serial_extent" not in gemm.attrs
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["pipeline_extent"] == 2
    assert plain_gemm["attrs"]["num_stages"] == 0

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected(a, b)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in emit_metal_source(module)


def test_disabled_use_swizzle_is_noop_metadata_for_cpu_oracle_and_metal_source():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        "T.use_swizzle(panel_size=10, enable=False)\n            T.clear(C_local)",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 16},
        module_name="tilelang_disabled_swizzle_variant",
    )
    plain = lower_to_plain_tir(module, target="metal")

    assert plain.funcs[0]["ops"][0] == {
        "tir_op": "tir.use_swizzle",
        "args": [],
        "attrs": {"panel_size": 10, "enable": False},
    }

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected(a, b)
    assert "kernel void gemm_kernel" in emit_metal_source(module)


def test_empty_annotate_layout_is_noop_metadata_for_cpu_oracle_and_metal_source():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        "T.annotate_layout({})\n            T.clear(C_local)",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 16},
        module_name="tilelang_empty_annotate_layout_variant",
    )
    plain = lower_to_plain_tir(module, target="metal")

    assert plain.funcs[0]["ops"][0] == {
        "tir_op": "tir.annotate_layout",
        "args": [],
        "attrs": {"entries": 0},
    }

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected(a, b)
    assert "kernel void gemm_kernel" in emit_metal_source(module)


def test_swizzled_annotate_layout_preserves_metadata_and_metal_source_applies_layout():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        (
            "T.annotate_layout({\n"
            "                A_shared: tilelang.layout.make_swizzled_layout(A_shared),\n"
            "                B_shared: tilelang.layout.make_swizzled_layout(B_shared),\n"
            "            })\n"
            "            T.clear(C_local)"
        ),
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 17, "K": 16, "block_N": 16, "block_K": 16},
        module_name="tilelang_swizzled_annotate_layout_variant",
    )
    plain = lower_to_plain_tir(module, target="metal")

    assert [local["layout"] for local in plain.funcs[0]["locals"]] == [
        "swizzled",
        "swizzled",
        "tile",
    ]

    a, b = _inputs(m=5, n=17, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected(a, b)
    source = emit_metal_source(module)
    assert "uint a_shared_idx =" in source
    assert "uint b_shared_idx =" in source
    assert "^" in source


def test_swizzled_annotate_layout_padded_shape_uses_padded_physical_stride():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        (
            "T.annotate_layout({A_shared: tilelang.layout.make_swizzled_layout(A_shared)})\n"
            "            T.clear(C_local)"
        ),
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 16},
        module_name="tilelang_swizzled_padded_layout_variant",
    )
    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "uint a_shared_idx = ((a_local_m * 8u) + a_local_k);" in source


def test_swizzled_annotate_layout_padded_f32_uses_bank_incompatible_stride():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        (
            "T.annotate_layout({\n"
            "                A_shared: tilelang.layout.make_swizzled_layout(A_shared),\n"
            "                B_shared: tilelang.layout.make_swizzled_layout(B_shared),\n"
            "            })\n"
            "            T.clear(C_local)"
        ),
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={
            "M": 5,
            "N": 7,
            "K": 24,
            "block_M": 8,
            "block_N": 12,
            "block_K": 12,
            "dtype": "float32",
        },
        module_name="tilelang_swizzled_padded_f32_layout_variant",
    )
    metal = emit_metal_source(module)
    assert "threadgroup float A_shared[96];" in metal
    assert "threadgroup float B_shared[144];" in metal
    assert "uint a_shared_idx = ((a_local_m * 12u) + a_local_k);" in metal
    assert "uint b_shared_idx = ((b_local_k * 12u) + b_local_n);" in metal
    assert "^" not in metal


def test_enabled_use_swizzle_row_rasterization_survives_source_and_cpu_oracle():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        "T.use_swizzle(panel_size=2, enable=True)\n            T.clear(C_local)",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 17, "N": 19, "K": 16},
        module_name="tilelang_enabled_swizzle_variant",
    )
    plain = lower_to_plain_tir(module, target="metal")
    assert plain.funcs[0]["ops"][0] == {
        "tir_op": "tir.use_swizzle",
        "args": [],
        "attrs": {"panel_size": 2, "enable": True, "order": "row"},
    }

    a, b = _inputs(m=17, n=19, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected(a, b)
    source = emit_metal_source(module)
    assert "uint swizzle_panel_size = 2u * swizzle_grid_x;" in source
    assert "uint tile_gid_x = (swizzle_panel_idx & 1u)" in source
    assert "uint tile_col0 = tile_gid_x * 8u;" in source
    assert "uint tile_row0 = tile_gid_y * 8u;" in source


def test_enabled_use_swizzle_column_rasterization_survives_source_and_cpu_oracle():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        'T.use_swizzle(panel_size=2, order="col", enable=True)\n            T.clear(C_local)',
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 17, "N": 19, "K": 16},
        module_name="tilelang_enabled_col_swizzle_variant",
    )
    plain = lower_to_plain_tir(module, target="metal")
    assert plain.funcs[0]["ops"][0]["attrs"] == {
        "panel_size": 2,
        "order": "col",
        "enable": True,
    }

    a, b = _inputs(m=17, n=19, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected(a, b)
    source = emit_metal_source(module)
    assert "uint swizzle_panel_size = 2u * swizzle_grid_y;" in source
    assert "uint tile_gid_y = (swizzle_panel_idx & 1u)" in source
    assert "uint tile_col0 = tile_gid_x * 8u;" in source
    assert "uint tile_row0 = tile_gid_y * 8u;" in source


def test_enabled_use_swizzle_bad_order_fails_closed():
    source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        'T.use_swizzle(panel_size=2, order="diag", enable=True)\n            T.clear(C_local)',
    )
    with pytest.raises(TileLangImportError, match="order must be"):
        import_tilelang_source(
            source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 17, "N": 19, "K": 16},
            module_name="tilelang_bad_swizzle_order_variant",
        )


def test_nonzero_start_serial_range_survives_import_freeze_source_and_cpu_oracle():
    serial_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(1, T.ceildiv(K, block_K)):",
    )
    module = import_tilelang_source(
        serial_source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 24},
        module_name="tilelang_nonzero_start_serial_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["serial_start"] == 1
    assert gemm.attrs["serial_extent"] == 2
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["serial_start"] == 1
    assert plain_gemm["attrs"]["serial_extent"] == 2

    a, b = _inputs(m=5, n=7, k=24)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b, k_start=8, k_end=24)
    assert "for (uint ko = 1u; ko < 3u; ++ko)" in emit_metal_source(module)


def test_step_serial_range_survives_import_freeze_source_and_cpu_oracle():
    serial_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(0, T.ceildiv(K, block_K), 2):",
    )
    module = import_tilelang_source(
        serial_source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 32},
        module_name="tilelang_step_serial_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["serial_extent"] == 4
    assert gemm.attrs["serial_step"] == 2
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["serial_extent"] == 4
    assert plain_gemm["attrs"]["serial_step"] == 2

    a, b = _inputs(m=5, n=7, k=32)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected_k_indices(
        a,
        b,
        [*range(0, 8), *range(16, 24)],
    )
    assert "for (uint ko = 0u; ko < 4u; ko += 2u)" in emit_metal_source(module)


def test_nonzero_step_serial_range_survives_import_freeze_source_and_cpu_oracle():
    serial_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(1, T.ceildiv(K, block_K), 2):",
    )
    module = import_tilelang_source(
        serial_source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 40},
        module_name="tilelang_nonzero_step_serial_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["serial_start"] == 1
    assert gemm.attrs["serial_extent"] == 4
    assert gemm.attrs["serial_step"] == 2
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["serial_start"] == 1
    assert plain_gemm["attrs"]["serial_extent"] == 4
    assert plain_gemm["attrs"]["serial_step"] == 2

    a, b = _inputs(m=5, n=7, k=40)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected_k_indices(
        a,
        b,
        [*range(8, 16), *range(24, 32)],
    )
    assert "for (uint ko = 1u; ko < 5u; ko += 2u)" in emit_metal_source(module)


def test_step_pipelined_range_survives_import_freeze_source_and_cpu_oracle():
    pipelined_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.Pipelined(0, T.ceildiv(K, block_K), 2, num_stages=0):",
    )
    module = import_tilelang_source(
        pipelined_source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 32},
        module_name="tilelang_step_pipelined_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["pipeline_extent"] == 4
    assert gemm.attrs["pipeline_step"] == 2
    assert gemm.attrs["num_stages"] == 0
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["pipeline_extent"] == 4
    assert plain_gemm["attrs"]["pipeline_step"] == 2
    assert plain_gemm["attrs"]["num_stages"] == 0

    a, b = _inputs(m=5, n=7, k=32)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected_k_indices(
        a,
        b,
        [*range(0, 8), *range(16, 24)],
    )
    assert "for (uint ko = 0u; ko < 4u; ko += 2u)" in emit_metal_source(module)


def test_nonzero_step_pipelined_range_survives_import_freeze_source_and_cpu_oracle():
    pipelined_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.Pipelined(1, T.ceildiv(K, block_K), 2, num_stages=0):",
    )
    module = import_tilelang_source(
        pipelined_source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 40},
        module_name="tilelang_nonzero_step_pipelined_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["pipeline_start"] == 1
    assert gemm.attrs["pipeline_extent"] == 4
    assert gemm.attrs["pipeline_step"] == 2
    assert gemm.attrs["num_stages"] == 0
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["pipeline_start"] == 1
    assert plain_gemm["attrs"]["pipeline_extent"] == 4
    assert plain_gemm["attrs"]["pipeline_step"] == 2
    assert plain_gemm["attrs"]["num_stages"] == 0

    a, b = _inputs(m=5, n=7, k=40)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected_k_indices(
        a,
        b,
        [*range(8, 16), *range(24, 32)],
    )
    assert "for (uint ko = 1u; ko < 5u; ko += 2u)" in emit_metal_source(module)


def test_nonzero_start_pipelined_range_survives_import_freeze_source_and_cpu_oracle():
    pipelined_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.Pipelined(1, T.ceildiv(K, block_K), num_stages=0):",
    )
    module = import_tilelang_source(
        pipelined_source,
        outer_function="matmul_serial",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 24},
        module_name="tilelang_nonzero_start_pipelined_variant",
    )

    gemm = _gemm_op(module)
    assert gemm.attrs["pipeline_start"] == 1
    assert gemm.attrs["pipeline_extent"] == 2
    assert gemm.attrs["num_stages"] == 0
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["pipeline_start"] == 1
    assert plain_gemm["attrs"]["pipeline_extent"] == 2

    a, b = _inputs(m=5, n=7, k=24)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b, k_start=8, k_end=24)
    assert "for (uint ko = 1u; ko < 3u; ++ko)" in emit_metal_source(module)


def test_nonzero_start_serial_and_pipelined_bad_ranges_fail_closed():
    serial_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(2, 1):",
    )
    with pytest.raises(TileLangImportError, match="end > start"):
        import_tilelang_source(
            serial_source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )

    pipelined_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.Pipelined(-1, T.ceildiv(K, block_K), num_stages=0):",
    )
    with pytest.raises(TileLangImportError, match="non-negative integer start"):
        import_tilelang_source(
            pipelined_source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )

    bool_serial_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(True, T.ceildiv(K, block_K)):",
    )
    with pytest.raises(TileLangImportError, match="non-negative integer start"):
        import_tilelang_source(
            bool_serial_source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )

    bool_pipelined_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.Pipelined(True, T.ceildiv(K, block_K), num_stages=0):",
    )
    with pytest.raises(TileLangImportError, match="non-negative integer start"):
        import_tilelang_source(
            bool_pipelined_source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )

    zero_step_serial_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(0, T.ceildiv(K, block_K), 0):",
    )
    with pytest.raises(TileLangImportError, match="positive integer step"):
        import_tilelang_source(
            zero_step_serial_source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )

    bool_step_serial_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.serial(0, T.ceildiv(K, block_K), True):",
    )
    with pytest.raises(TileLangImportError, match="positive integer step"):
        import_tilelang_source(
            bool_step_serial_source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )

    zero_step_pipelined_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.Pipelined(0, T.ceildiv(K, block_K), 0, num_stages=0):",
    )
    with pytest.raises(TileLangImportError, match="positive integer step"):
        import_tilelang_source(
            zero_step_pipelined_source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )

    bool_step_pipelined_source = TILELANG_SERIAL_GEMM_VARIANT.replace(
        "for ko in T.serial(T.ceildiv(K, block_K)):",
        "for ko in T.Pipelined(0, T.ceildiv(K, block_K), True, num_stages=0):",
    )
    with pytest.raises(TileLangImportError, match="positive integer step"):
        import_tilelang_source(
            bool_step_pipelined_source,
            outer_function="matmul_serial",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )


def test_parallel_tile_copy_metadata_survives_import_freeze_source_and_cpu_oracle():
    module = _import_parallel_copy()
    first_copy = next(op for op in module.funcs[0].body if op.op == "copy")

    assert first_copy.attrs["serial_extent"] == 2
    assert first_copy.attrs["parallel_extents"] == [8, 8]
    assert first_copy.attrs["parallel_vars"] == ["i", "kk"]

    plain_first_copy = next(op for op in _plain_ops(module) if op["tir_op"] == "tir.copy_loop")
    assert plain_first_copy["attrs"]["parallel_extents"] == [8, 8]
    assert plain_first_copy["attrs"]["parallel_vars"] == ["i", "kk"]

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "threadgroup half A_shared[64];" in source
    assert "threadgroup half B_shared[64];" in source


def test_parallel_ab_tile_copy_metadata_survives_import_freeze_source_and_cpu_oracle():
    module = _import_parallel_ab_copy()
    copies = [op for op in module.funcs[0].body if op.op == "copy"]
    a_copy = next(op for op in copies if op.args[1] == "A_shared")
    b_copy = next(op for op in copies if op.args[1] == "B_shared")

    assert a_copy.attrs["parallel_extents"] == [8, 8]
    assert a_copy.attrs["parallel_vars"] == ["i", "kk"]
    assert b_copy.attrs["parallel_extents"] == [8, 8]
    assert b_copy.attrs["parallel_vars"] == ["kk", "j"]

    plain_copies = [
        op for op in _plain_ops(module)
        if op["tir_op"] == "tir.copy_loop"
    ]
    plain_a = next(op for op in plain_copies if op["args"][1] == "A_shared")
    plain_b = next(op for op in plain_copies if op["args"][1] == "B_shared")
    assert plain_a["attrs"]["parallel_extents"] == [8, 8]
    assert plain_b["attrs"]["parallel_extents"] == [8, 8]
    assert plain_b["attrs"]["parallel_vars"] == ["kk", "j"]

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "threadgroup half A_shared[64];" in source
    assert "threadgroup half B_shared[64];" in source


def test_parallel_abc_tile_copy_metadata_survives_import_freeze_source_and_cpu_oracle():
    module = _import_parallel_abc_copy()
    copies = [op for op in module.funcs[0].body if op.op == "copy"]
    a_copy = next(op for op in copies if op.args[1] == "A_shared")
    b_copy = next(op for op in copies if op.args[1] == "B_shared")
    c_copy = next(op for op in copies if op.args == ("C_local", "C"))

    assert a_copy.attrs["parallel_extents"] == [8, 8]
    assert a_copy.attrs["parallel_vars"] == ["i", "kk"]
    assert b_copy.attrs["parallel_extents"] == [8, 8]
    assert b_copy.attrs["parallel_vars"] == ["kk", "j"]
    assert c_copy.attrs["parallel_extents"] == [8, 8]
    assert c_copy.attrs["parallel_vars"] == ["i", "j"]

    plain_copies = [
        op for op in _plain_ops(module)
        if op["tir_op"] == "tir.copy_loop"
    ]
    plain_c = next(op for op in plain_copies if op["args"] == ["C_local", "C"])
    assert plain_c["attrs"]["parallel_extents"] == [8, 8]
    assert plain_c["attrs"]["parallel_vars"] == ["i", "j"]

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "C[(row * 7u) + col] = (float)acc;" in source


def test_parallel_metadata_survives_import_freeze_source_and_cpu_oracle():
    module = _import_parallel_abc_copy_with_metadata()
    a_copy = next(
        op for op in module.funcs[0].body
        if op.op == "copy" and op.args[1] == "A_shared"
    )

    assert a_copy.attrs["parallel_extents"] == [8, 8]
    assert a_copy.attrs["parallel_vars"] == ["i", "kk"]
    assert a_copy.attrs["parallel_coalesced_width"] == 4
    assert a_copy.attrs["parallel_prefer_async"] is False
    assert a_copy.attrs["parallel_annotations"] == {"pragma_unroll": True}

    plain_a = next(
        op for op in _plain_ops(module)
        if op["tir_op"] == "tir.copy_loop" and op["args"][1] == "A_shared"
    )
    assert plain_a["attrs"]["parallel_coalesced_width"] == 4
    assert plain_a["attrs"]["parallel_prefer_async"] is False
    assert plain_a["attrs"]["parallel_annotations"] == {"pragma_unroll": True}

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "C[(row * 7u) + col] = (float)acc;" in source


def test_parallel_prefer_async_true_is_rejected_for_metal_import():
    source = TILELANG_PARALLEL_ABC_COPY_VARIANT.replace(
        "for i, kk in T.Parallel(block_M, block_K):",
        "for i, kk in T.Parallel(block_M, block_K, prefer_async=True):",
        1,
    )

    with pytest.raises(TileLangImportError, match="prefer_async=True"):
        import_tilelang_source(
            source,
            outer_function="matmul_parallel_abc_copy",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )


def test_parallel_loop_layout_is_rejected_until_fragment_layout_semantics_exist():
    source = TILELANG_PARALLEL_ABC_COPY_VARIANT.replace(
        "for i, kk in T.Parallel(block_M, block_K):",
        "for i, kk in T.Parallel(block_M, block_K, loop_layout=layout):",
        1,
    )

    with pytest.raises(TileLangImportError, match="loop_layout"):
        import_tilelang_source(
            source,
            outer_function="matmul_parallel_abc_copy",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16, "layout": "fake"},
        )


def test_vectorized_abc_tile_copy_metadata_survives_import_freeze_source_and_cpu_oracle():
    module = _import_vectorized_abc_copy()
    copies = [op for op in module.funcs[0].body if op.op == "copy"]
    a_copy = next(op for op in copies if op.args[1] == "A_shared")
    b_copy = next(op for op in copies if op.args[1] == "B_shared")
    c_copy = next(op for op in copies if op.args == ("C_local", "C"))

    assert a_copy.attrs["parallel_extents"] == [8]
    assert a_copy.attrs["parallel_vars"] == ["i"]
    assert a_copy.attrs["vectorized_extent"] == 8
    assert a_copy.attrs["vectorized_var"] == "kk"
    assert b_copy.attrs["parallel_extents"] == [8]
    assert b_copy.attrs["parallel_vars"] == ["kk"]
    assert b_copy.attrs["vectorized_extent"] == 8
    assert b_copy.attrs["vectorized_var"] == "j"
    assert c_copy.attrs["parallel_extents"] == [8]
    assert c_copy.attrs["parallel_vars"] == ["i"]
    assert c_copy.attrs["vectorized_extent"] == 8
    assert c_copy.attrs["vectorized_var"] == "j"

    plain_copies = [
        op for op in _plain_ops(module)
        if op["tir_op"] == "tir.copy_loop"
    ]
    plain_a = next(op for op in plain_copies if op["args"][1] == "A_shared")
    assert plain_a["attrs"]["vectorized_extent"] == 8
    assert plain_a["attrs"]["vectorized_var"] == "kk"

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "C[(row * 7u) + col] = (float)acc;" in source


def test_vectorized_abc_tile_copy_combines_with_nonzero_serial_range():
    module = _import_vectorized_abc_copy_nonzero_serial()
    gemm = _gemm_op(module)
    copies = [op for op in module.funcs[0].body if op.op == "copy"]
    a_copy = next(op for op in copies if op.args[1] == "A_shared")
    c_copy = next(op for op in copies if op.args == ("C_local", "C"))

    assert gemm.attrs["serial_start"] == 1
    assert gemm.attrs["serial_extent"] == 2
    assert a_copy.attrs["serial_start"] == 1
    assert a_copy.attrs["parallel_extents"] == [8]
    assert a_copy.attrs["vectorized_extent"] == 8
    assert c_copy.attrs["parallel_extents"] == [8]
    assert c_copy.attrs["vectorized_extent"] == 8

    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["serial_start"] == 1
    assert plain_gemm["attrs"]["serial_extent"] == 2
    plain_copies = [op for op in _plain_ops(module) if op["tir_op"] == "tir.copy_loop"]
    plain_a = next(op for op in plain_copies if op["args"][1] == "A_shared")
    assert plain_a["attrs"]["serial_start"] == 1
    assert plain_a["attrs"]["vectorized_extent"] == 8

    a, b = _inputs(m=5, n=7, k=24)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b, k_start=8, k_end=24)

    source = emit_metal_source(module)
    assert "for (uint ko = 1u; ko < 3u; ++ko)" in source
    assert "C[(row * 7u) + col] = (float)acc;" in source


def test_vectorized_annotations_survive_import_freeze_source_and_cpu_oracle():
    module = _import_vectorized_abc_copy_with_annotations()
    a_copy = next(
        op for op in module.funcs[0].body
        if op.op == "copy" and op.args[1] == "A_shared"
    )

    assert a_copy.attrs["parallel_extents"] == [8]
    assert a_copy.attrs["parallel_vars"] == ["i"]
    assert a_copy.attrs["vectorized_extent"] == 8
    assert a_copy.attrs["vectorized_var"] == "kk"
    assert a_copy.attrs["vectorized_annotations"] == {"pragma_unroll": True}

    plain_a = next(
        op for op in _plain_ops(module)
        if op["tir_op"] == "tir.copy_loop" and op["args"][1] == "A_shared"
    )
    assert plain_a["attrs"]["vectorized_annotations"] == {"pragma_unroll": True}

    a, b = _inputs(m=5, n=7, k=16)
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.k_tiles == 2
    assert result.outputs["C"] == _expected(a, b)

    source = emit_metal_source(module)
    assert "for (uint ko = 0u; ko < 2u; ++ko)" in source
    assert "C[(row * 7u) + col] = (float)acc;" in source


def test_transpose_a_gemm_executes_cpu_oracle_and_emits_transposed_metal_source():
    module = _import_transpose_a()
    gemm = _gemm_op(module)

    assert gemm.attrs["transpose_A"] is True
    assert gemm.attrs["transpose_B"] is False
    assert _plain_gemm_op(module)["attrs"]["transpose_A"] is True

    a, b = _inputs_transpose_a()
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected_transpose_a(a, b)

    source = emit_metal_source(module)
    assert "uint a_local_k = load / 8u;" in source
    assert "A[(a_row * 5u) + a_col]" in source
    assert "acc += float(A_shared[((kk * 8u) + local_m)])" in source


def test_transpose_b_gemm_executes_cpu_oracle_and_emits_transposed_metal_source():
    module = _import_transpose_b()
    gemm = _gemm_op(module)

    assert gemm.attrs["transpose_A"] is False
    assert gemm.attrs["transpose_B"] is True
    assert _plain_gemm_op(module)["attrs"]["transpose_B"] is True

    a, b = _inputs_transpose_b()
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected_transpose_b(a, b)

    source = emit_metal_source(module)
    assert "uint b_local_n = load / 8u;" in source
    assert "B[(b_row * 3u) + b_col]" in source
    assert "acc += float(A_shared[((local_m * 8u) + kk)])" in source
    assert "float(B_shared[((local_n * 8u) + kk)]);" in source


def test_transpose_ab_gemm_executes_cpu_oracle_and_emits_transposed_metal_source():
    module = _import_transpose_ab()
    gemm = _gemm_op(module)

    assert gemm.attrs["transpose_A"] is True
    assert gemm.attrs["transpose_B"] is True
    plain_gemm = _plain_gemm_op(module)
    assert plain_gemm["attrs"]["transpose_A"] is True
    assert plain_gemm["attrs"]["transpose_B"] is True

    a, b = _inputs_transpose_ab()
    result = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    assert result.outputs["C"] == _expected_transpose_ab(a, b)

    source = emit_metal_source(module)
    assert "uint a_local_k = load / 8u;" in source
    assert "A[(a_row * 5u) + a_col]" in source
    assert "uint b_local_n = load / 8u;" in source
    assert "B[(b_row * 3u) + b_col]" in source
    assert "acc += float(A_shared[((kk * 8u) + local_m)])" in source
    assert "float(B_shared[((local_n * 8u) + kk)]);" in source


def test_parallel_tile_copy_extent_mismatch_fails_closed():
    module = _import_parallel_copy()
    func = module.funcs[0]
    body = tuple(
        KernelOp(op.op, op.args, {**op.attrs, "parallel_extents": [4, 8]})
        if op.op == "copy" and op.args[1] == "A_shared"
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
    a, b = _inputs(m=5, n=7, k=16)

    with pytest.raises(KernelCpuReferenceError, match="T.Parallel extents"):
        execute_scalar_tiled_gemm_reference(bad, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="T.Parallel extents"):
        emit_metal_source(bad)


def test_parallel_output_tile_copy_extent_mismatch_fails_closed():
    module = _import_parallel_abc_copy()
    func = module.funcs[0]
    body = tuple(
        KernelOp(op.op, op.args, {**op.attrs, "parallel_extents": [4, 8]})
        if op.op == "copy" and op.args == ("C_local", "C")
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
    a, b = _inputs(m=5, n=7, k=16)

    with pytest.raises(KernelCpuReferenceError, match="T.Parallel extents"):
        execute_scalar_tiled_gemm_reference(bad, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="T.Parallel extents"):
        emit_metal_source(bad)


def test_vectorized_tile_copy_extent_mismatch_fails_closed():
    module = _import_vectorized_abc_copy()
    func = module.funcs[0]
    body = tuple(
        KernelOp(op.op, op.args, {**op.attrs, "vectorized_extent": 4})
        if op.op == "copy" and op.args[1] == "A_shared"
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
    a, b = _inputs(m=5, n=7, k=16)

    with pytest.raises(KernelCpuReferenceError, match="scheduled tile-copy extents"):
        execute_scalar_tiled_gemm_reference(bad, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="scheduled tile-copy extents"):
        emit_metal_source(bad)


def test_vectorized_zero_start_two_arg_extent_survives_import():
    source = TILELANG_VECTORIZED_ABC_COPY_VARIANT.replace(
        "for kk in T.vectorized(block_K):",
        "for kk in T.vectorized(0, block_K):",
        1,
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_vectorized_abc_copy",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 16},
        module_name="tilelang_vectorized_zero_start_variant",
    )
    a_copy = next(op for op in module.funcs[0].body if op.op == "copy" and op.args[1] == "A_shared")

    assert a_copy.attrs["vectorized_extent"] == 8
    assert execute_scalar_tiled_gemm_reference(
        module,
        {"A": _inputs(m=5, n=7, k=16)[0], "B": _inputs(m=5, n=7, k=16)[1]},
    ).outputs["C"] == _expected(*_inputs(m=5, n=7, k=16))


def test_vectorized_executable_body_fails_closed_in_importer():
    source = TILELANG_VECTORIZED_ABC_COPY_VARIANT.replace(
        "T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)",
        "C_local[0, 0] = C_local[0, 0] + 1.0",
        1,
    )

    with pytest.raises(
        TileLangImportError,
        match="executable T.Parallel/T.vectorized loop bodies are not supported",
    ):
        import_tilelang_source(
            source,
            outer_function="matmul_vectorized_abc_copy",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )


def test_parallel_executable_body_fails_closed_in_importer():
    source = TILELANG_PARALLEL_COPY_VARIANT.replace(
        "T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)",
        "C_local[0, 0] = C_local[0, 0] + 1.0",
        1,
    )

    with pytest.raises(
        TileLangImportError,
        match="executable T.Parallel/T.vectorized loop bodies are not supported",
    ):
        import_tilelang_source(
            source,
            outer_function="matmul_parallel_copy",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )


def test_parallel_loop_target_arity_mismatch_is_rejected():
    source = TILELANG_PARALLEL_COPY_VARIANT.replace(
        "for i, kk in T.Parallel(block_M, block_K):",
        "for i in T.Parallel(block_M, block_K):",
    )

    with pytest.raises(TileLangImportError, match="target arity"):
        import_tilelang_source(
            source,
            outer_function="matmul_parallel_copy",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16},
        )


def test_bad_serial_extent_fails_closed_for_cpu_oracle_and_metal_source():
    module = _import_serial()
    func = module.funcs[0]
    body = tuple(
        KernelOp(op.op, op.args, {**op.attrs, "serial_extent": 99})
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
    a, b = _inputs(m=5, n=7, k=16)

    with pytest.raises(KernelCpuReferenceError, match="serial_extent"):
        execute_scalar_tiled_gemm_reference(bad, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="serial_extent"):
        emit_metal_source(bad)


def test_bad_serial_step_fails_closed_for_cpu_oracle_and_metal_source():
    module = _import_serial()
    func = module.funcs[0]
    body = tuple(
        KernelOp(op.op, op.args, {**op.attrs, "serial_step": True})
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
    a, b = _inputs(m=5, n=7, k=16)

    with pytest.raises(KernelCpuReferenceError, match="serial_step"):
        execute_scalar_tiled_gemm_reference(bad, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="serial_step"):
        emit_metal_source(bad)


def test_gemm_positional_transpose_b_is_not_dropped_and_fails_closed():
    module = _import_variant(trans_B=True)
    gemm = _gemm_op(module)

    assert gemm.attrs["transpose_B"] is True
    assert _plain_gemm_op(module)["attrs"]["transpose_B"] is True

    a, b = _inputs()
    with pytest.raises(KernelCpuReferenceError, match="transpose_B"):
        execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="transpose_B"):
        emit_metal_source(module)


def test_gemm_positional_transpose_a_is_not_dropped_and_fails_closed():
    module = _import_variant(trans_A=True)
    gemm = _gemm_op(module)

    assert gemm.attrs["transpose_A"] is True
    assert _plain_gemm_op(module)["attrs"]["transpose_A"] is True

    a, b = _inputs()
    with pytest.raises(KernelCpuReferenceError, match="transpose_A"):
        execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    with pytest.raises(MetalFinalizeError, match="transpose_A"):
        emit_metal_source(module)


def test_conflicting_positional_and_keyword_gemm_transpose_metadata_is_rejected():
    source = TILELANG_SPLITK_GEMM_VARIANT.replace(
        "T.gemm(A_shared, B_shared, C_local, trans_A, trans_B)",
        "T.gemm(A_shared, B_shared, C_local, trans_A, trans_B, transpose_A=True)",
    )

    with pytest.raises(TileLangImportError, match="conflicting TileLang metadata"):
        import_tilelang_source(
            source,
            outer_function="matmul_splitk",
            prim_func="gemm_kernel",
            constants={"M": 128, "N": 128, "K": 64, "trans_A": False},
        )


def test_zero_fill_survives_import_freeze_source_and_cpu_oracle():
    source = TILELANG_SPLITK_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        "T.fill(C_local, 0.0)",
    )
    module = import_tilelang_source(
        source,
        outer_function="matmul_splitk",
        prim_func="gemm_kernel",
        constants={"M": 5, "N": 7, "K": 16, "split_k": 1, "block_M": 8, "block_N": 8, "block_K": 8},
        module_name="tilelang_zero_fill",
    )
    fill = next(op for op in module.funcs[0].body if op.op == "fill")
    assert fill.args == ("C_local",)
    assert fill.attrs["value"] == 0.0
    plain_fill = next(
        op for op in lower_to_plain_tir(module, target="metal").funcs[0]["ops"]
        if op["tir_op"] == "tir.fill_loop"
    )
    assert plain_fill["attrs"]["value"] == 0.0
    assert "float acc = 0.0;" in emit_metal_source(module)
    a, b = _inputs(m=5, n=7, k=16)
    assert execute_scalar_tiled_gemm_reference(
        module, {"A": a, "B": b}
    ).outputs["C"] == _expected(a, b)


@pytest.mark.parametrize("value", ["float('inf')", "A"])
def test_nonfinite_or_dynamic_fill_fails_closed(value: str):
    source = TILELANG_SPLITK_GEMM_VARIANT.replace(
        "T.clear(C_local)",
        f"T.fill(C_local, {value})",
    )
    with pytest.raises(
        TileLangImportError,
        match="T.fill|unsupported expression|unknown symbolic value",
    ):
        import_tilelang_source(
            source,
            outer_function="matmul_splitk",
            prim_func="gemm_kernel",
            constants={"M": 5, "N": 7, "K": 16, "split_k": 1},
        )
