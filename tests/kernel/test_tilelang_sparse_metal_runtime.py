from __future__ import annotations

import sys

import pytest

from pcc.kernel_ir.cpu_reference import execute_sparse_tiled_gemm_sp_reference
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.metal_finalize import MetalFinalizeError, emit_metal_source
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SKIPPED_WITH_REASON,
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
    run_metal_source_runtime_package,
)
from pcc.kernel_ir.metal_tensor import (
    pack_matrix_to_metal_bytes,
    unpack_matrix_from_metal_bytes,
)
from pcc.kernel_ir.tilelang_import import import_tilelang_source


TILELANG_FIXED_SPARSE_GEMM = """
import tilelang.language as T

pytestmark = pytest.mark.pcc_gate(probe="metal")


def matmul_sp(M, N, K, in_dtype, accum_dtype, e_dtype, e_factor,
              block_M, block_N, block_K, num_stages, thread_num,
              policy, enable_rasterization):
    @T.prim_func
    def main(
        A_sparse: T.Tensor((M, K // 2), in_dtype),
        E: T.Tensor((M, K // e_factor), e_dtype),
        B: T.Tensor((K, N), in_dtype),
        C: T.Tensor((M, N), accum_dtype),
    ):
        with T.Kernel(T.ceildiv(N, block_N), T.ceildiv(M, block_M), threads=thread_num) as (bx, by):
            A_shared = T.alloc_shared((block_M, block_K // 2), in_dtype)
            B_shared = T.alloc_shared((block_K, block_N), in_dtype)
            E_shared = T.alloc_shared((block_M, block_K // e_factor), e_dtype)
            C_local = T.alloc_fragment((block_M, block_N), accum_dtype)
            C_shared = T.alloc_shared((block_M, block_N), accum_dtype)
            T.clear(C_local)
            T.use_swizzle(panel_size=10, enable=enable_rasterization)
            for k in T.Pipelined(T.ceildiv(K, block_K), num_stages=num_stages):
                T.copy(A_sparse[by * block_M, k * block_K // 2], A_shared)
                T.copy(E[by * block_M, k * block_K // e_factor], E_shared)
                T.copy(B[k * block_K, bx * block_N], B_shared)
                T.gemm_sp(
                    A_shared,
                    E_shared,
                    B_shared,
                    C_local,
                    transpose_A=False,
                    transpose_E=False,
                    transpose_B=False,
                    policy=policy,
                )
            T.copy(C_local, C_shared)
            T.copy(C_shared, C[by * block_M, bx * block_N])
    return main
"""


def fixed_sparse_module(**overrides: object):
    constants: dict[str, object] = {
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
    }
    constants.update(overrides)
    return import_tilelang_source(
        TILELANG_FIXED_SPARSE_GEMM,
        outer_function="matmul_sp",
        prim_func="main",
        constants=constants,
        module_name="tilelang_fixed_sparse_gemm_sp_metal",
    )


def fixed_sparse_inputs():
    # Each metadata nibble is 0b0100: keep offsets 0 and 1 in every group of 4.
    metadata_word = 0x4444
    a_sparse = tuple(
        tuple(float(((row + col) % 5) - 2) for col in range(8))
        for row in range(5)
    )
    metadata = tuple((metadata_word,) for _ in range(5))
    b = tuple(
        tuple(float(((k_index * 2 + col) % 5) - 2) for col in range(7))
        for k_index in range(16)
    )
    dense = tuple(
        tuple(
            a_sparse[row][(k_index // 4) * 2 + (k_index % 4)]
            if k_index % 4 < 2
            else 0.0
            for k_index in range(16)
        )
        for row in range(5)
    )
    expected = tuple(
        tuple(
            sum(dense[row][k_index] * b[k_index][col] for k_index in range(16))
            for col in range(7)
        )
        for row in range(5)
    )
    return a_sparse, metadata, b, expected


def test_fixed_sparse_gemm_sp_emits_owned_scalar_metal_and_matches_cpu_oracle():
    module = fixed_sparse_module()
    a_sparse, metadata, b, expected = fixed_sparse_inputs()
    oracle = execute_sparse_tiled_gemm_sp_reference(
        module,
        {"A_sparse": a_sparse, "E": metadata, "B": b},
    )
    assert oracle.outputs["C"] == expected

    source = emit_metal_source(module)
    assert "kernel void pcc_main_kernel(" in source
    assert "ushort metadata_word = ushort(E[row]);" in source
    assert "uint code = (uint(metadata_word) >> (4u * (group % 4u))) & 15u;" in source
    assert "acc += aval * float(B[(k_index * 7u) + col]);" in source
    assert "simdgroup" not in source
    assert "wgmma" not in source


def test_fixed_sparse_gemm_sp_int16_metadata_has_typed_matrix_transfer():
    metadata = ((0x4444,),) * 5
    packed = pack_matrix_to_metal_bytes(metadata, dtype="i16", shape=(5, 1), name="E")
    assert len(packed) == 10
    assert unpack_matrix_from_metal_bytes(packed, dtype="i16", shape=(5, 1), name="E") == metadata


def test_sparse_gemm_sp_metal_fails_closed_outside_fixed_shape():
    with pytest.raises(MetalFinalizeError, match="fixed sparse GEMM_SP"):
        emit_metal_source(fixed_sparse_module(K=32))


def test_sparse_gemm_sp_metal_fails_closed_for_transpose():
    source = TILELANG_FIXED_SPARSE_GEMM.replace("transpose_A=False", "transpose_A=True")
    module = import_tilelang_source(
        source,
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
    )
    with pytest.raises(MetalFinalizeError, match="does not support transposes"):
        emit_metal_source(module)


def test_fixed_sparse_gemm_sp_real_metal_readback(tmp_path):
    if sys.platform != "darwin":
        pytest.fail("fixed TileLang sparse GEMM runtime requires Darwin Metal")
    module = fixed_sparse_module()
    a_sparse, metadata, b, _ = fixed_sparse_inputs()
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=5 * 8 * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=5 * 1 * 2, dtype="i16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=16 * 7 * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=5 * 7 * 4, dtype="f32", device="metal:0"))
    inputs = {"A_sparse": a_sparse, "E": metadata, "B": b}
    oracle = execute_sparse_tiled_gemm_sp_reference(module, inputs)
    result = run_metal_source_runtime_package(
        module,
        args,
        tmp_path,
        metal_source=emit_metal_source(module),
        input_matrices=inputs,
        cpu_reference=oracle,
        output_name="C",
        timeout=90.0,
    )
    data = result.to_dict()
    if result.status == STATUS_SKIPPED_WITH_REASON:
        pytest.fail(data["reason"])
    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED, data
    assert data["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
