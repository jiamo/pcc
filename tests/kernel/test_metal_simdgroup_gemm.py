"""Opt-in Metal simdgroup GEMM lowering for Kernel IR."""

import sys

from pcc.kernel_ir.cpu_reference import execute_scalar_tiled_gemm_reference
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import BufferParam, KernelFunc, KernelModule, KernelOp, Layout, LocalBuffer, MemoryScope, ScalarType
from pcc.kernel_ir.metal_finalize import (
    MetalFinalizeError,
    emit_metal_simdgroup_gemm_source,
    emit_metal_source,
)
from pcc.kernel_ir.metal_source_runtime import (
    STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED,
    STATUS_SKIPPED_WITH_REASON,
    run_metal_source_runtime_package,
)

import pytest


def _simdgroup_micro_gemm_module(
    *,
    m: int = 8,
    n: int = 8,
    k: int = 8,
    block_m: int = 8,
    block_n: int = 8,
    block_k: int = 8,
    threads: int = 32,
    swizzle: bool = False,
    swizzle_order: str = "row",
    swizzle_panel_size: int = 2,
    gemm_pipeline_start: int = 0,
    gemm_pipeline_extent: int | None = None,
    split_k: int = 1,
    split_k_span_mode: str | None = None,
    output_atomic: bool = False,
    transpose_a: bool = False,
    transpose_b: bool = False,
) -> KernelModule:
    full_k_tiles = (k + block_k - 1) // block_k
    default_k_tiles = full_k_tiles
    split_span = k
    if output_atomic:
        if split_k_span_mode == "ceildiv" and split_k > 0:
            split_span = (k + split_k - 1) // split_k
        elif split_k_span_mode == "floor_div" and split_k > 0:
            split_span = k // split_k
        else:
            split_span = k // split_k if split_k > 0 and k % split_k == 0 else k
        default_k_tiles = (split_span + block_k - 1) // block_k
    selected_k_tiles = default_k_tiles if gemm_pipeline_extent is None else gemm_pipeline_extent
    gemm_attrs = {"pipeline_extent": selected_k_tiles, "num_stages": 0}
    if gemm_pipeline_start:
        gemm_attrs["pipeline_start"] = gemm_pipeline_start
    if transpose_a:
        gemm_attrs["transpose_A"] = True
    if transpose_b:
        gemm_attrs["transpose_B"] = True
    output_op = KernelOp("atomic_add", ("C", "C_local")) if output_atomic else KernelOp("copy", ("C_local", "C"))
    grid = ((n + block_n - 1) // block_n, (m + block_m - 1) // block_m, split_k) if output_atomic else (
        (n + block_n - 1) // block_n,
        (m + block_m - 1) // block_m,
    )
    copy_attrs = {"pipeline_extent": full_k_tiles, "num_stages": 0}
    if output_atomic and split_k_span_mode is not None:
        copy_attrs = {
            **copy_attrs,
            "split_k_span_mode": split_k_span_mode,
            "split_k_span": split_span,
            "split_k_axis_var": "bz",
        }
    body: list[KernelOp] = []
    if swizzle:
        body.append(
            KernelOp(
                "swizzle",
                (),
                {"panel_size": swizzle_panel_size, "enable": True, "order": swizzle_order},
            )
        )
    body.extend(
        [
            KernelOp("fill", ("C_local",), {"value": 0}),
            KernelOp("copy", ("A", "A_shared"), copy_attrs),
            KernelOp("copy", ("B", "B_shared"), copy_attrs),
            KernelOp("gemm", ("A_shared", "B_shared", "C_local"), gemm_attrs),
            output_op,
        ]
    )
    func = KernelFunc(
        name="gemm_kernel",
        params=(
            BufferParam("A", ScalarType.F16, rank=2, shape=(k, m) if transpose_a else (m, k), scope=MemoryScope.GLOBAL),
            BufferParam("B", ScalarType.F16, rank=2, shape=(n, k) if transpose_b else (k, n), scope=MemoryScope.GLOBAL),
            BufferParam("C", ScalarType.F32, rank=2, shape=(m, n), scope=MemoryScope.GLOBAL),
        ),
        locals=(
            LocalBuffer("A_shared", ScalarType.F16, shape=(block_k, block_m) if transpose_a else (block_m, block_k), scope=MemoryScope.SHARED, layout=Layout.TILE),
            LocalBuffer("B_shared", ScalarType.F16, shape=(block_n, block_k) if transpose_b else (block_k, block_n), scope=MemoryScope.SHARED, layout=Layout.TILE),
            LocalBuffer("C_local", ScalarType.F32, shape=(block_m, block_n), scope=MemoryScope.FRAGMENT, layout=Layout.TILE),
        ),
        body=tuple(body),
        grid=grid,
        threads=threads,
    )
    return KernelModule("simdgroup_micro_gemm_mod", funcs=(func,))


def _input_matrices(
    m: int = 8, n: int = 8, k: int = 8
) -> tuple[tuple[tuple[float, ...], ...], tuple[tuple[float, ...], ...]]:
    a = tuple(tuple(float((row + col) % 5 - 2) for col in range(k)) for row in range(m))
    b = tuple(tuple(float((row * 2 + col) % 7 - 3) for col in range(n)) for row in range(k))
    return a, b


def _input_matrices_transposed(
    *,
    m: int,
    n: int,
    k: int,
    transpose_a: bool = False,
    transpose_b: bool = False,
) -> tuple[tuple[tuple[float, ...], ...], tuple[tuple[float, ...], ...]]:
    if transpose_a:
        a = tuple(tuple(float(((row * 3) + col) % 7 - 3) for col in range(m)) for row in range(k))
    else:
        a = tuple(tuple(float(((row * 3) + col) % 7 - 3) for col in range(k)) for row in range(m))
    if transpose_b:
        b = tuple(tuple(float(((row * 2) - col) % 9 - 4) for col in range(k)) for row in range(n))
    else:
        b = tuple(tuple(float(((row * 2) - col) % 9 - 4) for col in range(n)) for row in range(k))
    return a, b


def test_simdgroup_gemm_source_is_opt_in_and_keeps_scalar_fallback():
    module = _simdgroup_micro_gemm_module()
    scalar_source = emit_metal_source(module)
    simdgroup_source = emit_metal_simdgroup_gemm_source(module)

    assert "simdgroup" not in scalar_source
    assert "simdgroup_half8x8 A_frag[1];" in simdgroup_source
    assert "simdgroup_half8x8 B_frag[1];" in simdgroup_source
    assert "simdgroup_float8x8 C_frag[1];" in simdgroup_source
    assert "make_filled_simdgroup_matrix<float, 8, 8>(0.0)" in simdgroup_source
    assert "simdgroup_load(A_frag[0], A + ((tgid.y * 8u) * 8u) + (ko * 8u), 8u, 0, false);" in simdgroup_source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 8u) + (tgid.x * 8u), 8u, 0, false);" in simdgroup_source
    assert "simdgroup_multiply_accumulate(C_frag[0], A_frag[0], B_frag[0], C_frag[0]);" in simdgroup_source
    assert "simdgroup_store(C_frag[0], C + ((tgid.y * 8u) * 8u) + (tgid.x * 8u), 8u, 0, false);" in simdgroup_source
    assert "threadgroup half A_shared" not in simdgroup_source
    assert "whole_program_gpu" not in simdgroup_source


def test_simdgroup_gemm_rejects_non_8x8x8_tile_shape():
    module = _simdgroup_micro_gemm_module(block_m=10, block_n=8, block_k=8)

    with pytest.raises(MetalFinalizeError, match="requires MxNx8 tile locals"):
        emit_metal_simdgroup_gemm_source(module)


def test_simdgroup_gemm_rejects_non_single_simdgroup_threads():
    module = _simdgroup_micro_gemm_module(threads=128)

    with pytest.raises(MetalFinalizeError, match="requires exactly 32"):
        emit_metal_simdgroup_gemm_source(module)


def test_simdgroup_gemm_use_swizzle_remaps_tile_ids():
    module = _simdgroup_micro_gemm_module(m=16, n=16, k=8, swizzle=True, swizzle_panel_size=2)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint swizzle_grid_x = 2u;" in source
    assert "uint swizzle_grid_y = 2u;" in source
    assert "uint swizzle_panel_size = 2u * swizzle_grid_x;" in source
    assert "uint tile_gid_x =" in source
    assert "uint tile_gid_y =" in source
    assert "simdgroup_load(A_frag[0], A + ((tile_gid_y * 8u) * 8u) + (ko * 8u), 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 16u) + (tile_gid_x * 8u), 16u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C + ((tile_gid_y * 8u) * 16u) + (tile_gid_x * 8u), 16u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_column_use_swizzle_remaps_tile_ids():
    module = _simdgroup_micro_gemm_module(
        m=16,
        n=24,
        k=8,
        swizzle=True,
        swizzle_order="col",
        swizzle_panel_size=1,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint swizzle_grid_x = 3u;" in source
    assert "uint swizzle_grid_y = 2u;" in source
    assert "uint swizzle_panel_size = 1u * swizzle_grid_y;" in source
    assert "uint tile_gid_y =" in source
    assert "uint tile_gid_x =" in source
    assert "simdgroup_load(A_frag[0], A + ((tile_gid_y * 8u) * 8u) + (ko * 8u), 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 24u) + (tile_gid_x * 8u), 24u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C + ((tile_gid_y * 8u) * 24u) + (tile_gid_x * 8u), 24u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_nonzero_pipeline_range_uses_selected_k_tiles():
    module = _simdgroup_micro_gemm_module(
        k=16,
        gemm_pipeline_start=1,
        gemm_pipeline_extent=1,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "for (uint ko = 1u; ko < 2u; ++ko) {" in source
    assert "simdgroup_load(A_frag[0], A + ((tgid.y * 8u) * 16u) + (ko * 8u), 16u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 8u) + (tgid.x * 8u), 8u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_two_simdgroups_per_threadgroup_cover_n_tiles():
    module = _simdgroup_micro_gemm_module(n=16, block_n=16, threads=64)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 2u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 2u;" in source
    assert "simdgroup_load(A_frag[0], A + ((tgid.y * 8u) * 8u) + (ko * 8u), 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 16u) + ((tgid.x * 16u) + (simdgroup_tile_n * 8u)), 16u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C + ((tgid.y * 8u) * 16u) + ((tgid.x * 16u) + (simdgroup_tile_n * 8u)), 16u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_two_simdgroups_per_threadgroup_cover_m_tiles():
    module = _simdgroup_micro_gemm_module(m=16, block_m=16, threads=64)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 1u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 1u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 8u) + (ko * 8u), 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 8u) + (tgid.x * 8u), 8u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 8u) + (tgid.x * 8u), 8u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_four_simdgroups_per_threadgroup_cover_2d_tile():
    module = _simdgroup_micro_gemm_module(m=16, n=16, block_m=16, block_n=16, threads=128)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 2u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 2u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 8u) + (ko * 8u), 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 16u) + ((tgid.x * 16u) + (simdgroup_tile_n * 8u)), 16u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 16u) + ((tgid.x * 16u) + (simdgroup_tile_n * 8u)), 16u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_four_simdgroups_with_transposed_operands_use_subtile_strides():
    module = _simdgroup_micro_gemm_module(
        m=16,
        n=16,
        k=8,
        block_m=16,
        block_n=16,
        threads=128,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 2u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 2u;" in source
    assert "simdgroup_load(A_frag[0], A + ((ko * 8u) * 16u) + ((tgid.y * 16u) + (simdgroup_tile_m * 8u)), 16u, 0, true);" in source
    assert "simdgroup_load(B_frag[0], B + (((tgid.x * 16u) + (simdgroup_tile_n * 8u)) * 8u) + (ko * 8u), 8u, 0, true);" in source
    assert "simdgroup_store(C_frag[0], C + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 16u) + ((tgid.x * 16u) + (simdgroup_tile_n * 8u)), 16u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_eight_simdgroups_per_threadgroup_cover_wider_n_tile():
    module = _simdgroup_micro_gemm_module(m=16, n=32, block_m=16, block_n=32, threads=256)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 8u) + (ko * 8u), 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 32u) + ((tgid.x * 32u) + (simdgroup_tile_n * 8u)), 32u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 32u) + ((tgid.x * 32u) + (simdgroup_tile_n * 8u)), 32u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_sixteen_simdgroups_per_threadgroup_cover_2d_tile():
    module = _simdgroup_micro_gemm_module(
        m=32,
        n=32,
        k=8,
        block_m=32,
        block_n=32,
        threads=512,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 32u) + (simdgroup_tile_m * 8u)) * 8u) + (ko * 8u), 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 32u) + ((tgid.x * 32u) + (simdgroup_tile_n * 8u)), 32u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C + (((tgid.y * 32u) + (simdgroup_tile_m * 8u)) * 32u) + ((tgid.x * 32u) + (simdgroup_tile_n * 8u)), 32u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_thirty_two_simdgroups_per_threadgroup_cover_wide_2d_tile():
    module = _simdgroup_micro_gemm_module(
        m=32,
        n=64,
        k=8,
        block_m=32,
        block_n=64,
        threads=1024,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 8u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 8u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 32u) + (simdgroup_tile_m * 8u)) * 8u) + (ko * 8u), 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((ko * 8u) * 64u) + ((tgid.x * 64u) + (simdgroup_tile_n * 8u)), 64u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C + (((tgid.y * 32u) + (simdgroup_tile_m * 8u)) * 64u) + ((tgid.x * 64u) + (simdgroup_tile_n * 8u)), 64u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_thirty_two_simdgroups_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=31,
        n=63,
        k=9,
        block_m=32,
        block_n=64,
        threads=1024,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[2048];" in source
    assert "threadgroup half B_tile[2048];" in source
    assert "threadgroup float C_tile[2048];" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 8u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 8u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 31u && global_k < 9u) ? A[(global_m * 9u) + global_k] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < 9u && global_n < 63u) ? B[(global_k * 63u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 31u && col < 63u) {" in source
    assert "C[(row * 63u) + col] = C_tile[simdgroup_tile_offset + c_linear];" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_thirty_two_simdgroups_transposed_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=31,
        n=63,
        k=9,
        block_m=32,
        block_n=64,
        threads=1024,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[2048];" in source
    assert "threadgroup half B_tile[2048];" in source
    assert "threadgroup float C_tile[2048];" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 8u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 8u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 31u && global_k < 9u) ? A[(global_k * 31u) + global_m] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < 9u && global_n < 63u) ? B[(global_n * 9u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 31u && col < 63u) {" in source
    assert "C[(row * 63u) + col] = C_tile[simdgroup_tile_offset + c_linear];" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_eight_simdgroups_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(m=15, n=31, k=9, block_m=16, block_n=32, threads=256)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[512];" in source
    assert "threadgroup half B_tile[512];" in source
    assert "threadgroup float C_tile[512];" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 15u && global_k < 9u) ? A[(global_m * 9u) + global_k] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < 9u && global_n < 31u) ? B[(global_k * 31u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 15u && col < 31u) {" in source
    assert "C[(row * 31u) + col] = C_tile[simdgroup_tile_offset + c_linear];" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_eight_simdgroups_transposed_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=15,
        n=31,
        k=9,
        block_m=16,
        block_n=32,
        threads=256,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[512];" in source
    assert "threadgroup half B_tile[512];" in source
    assert "threadgroup float C_tile[512];" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 15u && global_k < 9u) ? A[(global_k * 15u) + global_m] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < 9u && global_n < 31u) ? B[(global_n * 9u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 15u && col < 31u) {" in source
    assert "C[(row * 31u) + col] = C_tile[simdgroup_tile_offset + c_linear];" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_sixteen_simdgroups_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=31,
        n=31,
        k=9,
        block_m=32,
        block_n=32,
        threads=512,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[1024];" in source
    assert "threadgroup half B_tile[1024];" in source
    assert "threadgroup float C_tile[1024];" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 31u && global_k < 9u) ? A[(global_m * 9u) + global_k] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < 9u && global_n < 31u) ? B[(global_k * 31u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 31u && col < 31u) {" in source
    assert "C[(row * 31u) + col] = C_tile[simdgroup_tile_offset + c_linear];" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_sixteen_simdgroups_transposed_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=31,
        n=31,
        k=9,
        block_m=32,
        block_n=32,
        threads=512,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[1024];" in source
    assert "threadgroup half B_tile[1024];" in source
    assert "threadgroup float C_tile[1024];" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 31u && global_k < 9u) ? A[(global_k * 31u) + global_m] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < 9u && global_n < 31u) ? B[(global_n * 9u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 31u && col < 31u) {" in source
    assert "C[(row * 31u) + col] = C_tile[simdgroup_tile_offset + c_linear];" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_four_simdgroups_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(m=15, n=15, k=9, block_m=16, block_n=16, threads=128)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[256];" in source
    assert "threadgroup half B_tile[256];" in source
    assert "threadgroup float C_tile[256];" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 2u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 2u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "for (uint tile_linear = simdgroup_lane; tile_linear < 64u; tile_linear += 32u) {" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 15u && global_k < 9u) ? A[(global_m * 9u) + global_k] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < 9u && global_n < 15u) ? B[(global_k * 15u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 15u && col < 15u) {" in source
    assert "C[(row * 15u) + col] = C_tile[simdgroup_tile_offset + c_linear];" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_four_simdgroups_transposed_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=15,
        n=15,
        k=9,
        block_m=16,
        block_n=16,
        threads=128,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 15u && global_k < 9u) ? A[(global_k * 15u) + global_m] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < 9u && global_n < 15u) ? B[(global_n * 9u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 15u && col < 15u) {" in source
    assert "C[(row * 15u) + col] = C_tile[simdgroup_tile_offset + c_linear];" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_splitk_atomic_source_uses_tgid_z_and_atomic_add():
    module = _simdgroup_micro_gemm_module(k=16, split_k=2, output_atomic=True)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint3 tid [[thread_position_in_threadgroup]]" in source
    assert "uint3 tgid [[threadgroup_position_in_grid]]" in source
    assert "uint split_k_index = tgid.z;" in source
    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "threadgroup float C_tile[64];" in source
    assert "simdgroup_load(A_frag[0], A + ((tgid.y * 8u) * 16u) + (split_k0 + (ko * 8u)), 16u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((split_k0 + (ko * 8u)) * 8u) + (tgid.x * 8u), 8u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C_tile, 8u, 0, false);" in source
    assert "atomic_fetch_add_explicit(&C[(row * 8u) + col], C_tile[c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_four_simdgroups_splitk_atomic_uses_per_simdgroup_c_staging():
    module = _simdgroup_micro_gemm_module(
        m=16,
        n=16,
        k=16,
        block_m=16,
        block_n=16,
        threads=128,
        split_k=2,
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint3 tgid [[threadgroup_position_in_grid]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup float C_tile[256];" in source
    assert "uint split_k_index = tgid.z;" in source
    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 16u) + (split_k0 + (ko * 8u)), 16u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((split_k0 + (ko * 8u)) * 16u) + ((tgid.x * 16u) + (simdgroup_tile_n * 8u)), 16u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "for (uint c_linear = simdgroup_lane; c_linear < 64u; c_linear += 32u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 16u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_eight_simdgroups_splitk_atomic_uses_per_simdgroup_c_staging():
    module = _simdgroup_micro_gemm_module(
        m=16,
        n=32,
        k=16,
        block_m=16,
        block_n=32,
        threads=256,
        split_k=2,
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup float C_tile[512];" in source
    assert "uint split_k_index = tgid.z;" in source
    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 16u) + (simdgroup_tile_m * 8u)) * 16u) + (split_k0 + (ko * 8u)), 16u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((split_k0 + (ko * 8u)) * 32u) + ((tgid.x * 32u) + (simdgroup_tile_n * 8u)), 32u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "for (uint c_linear = simdgroup_lane; c_linear < 64u; c_linear += 32u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 32u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_eight_simdgroups_splitk_atomic_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=15,
        n=31,
        k=17,
        block_m=16,
        block_n=32,
        threads=256,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[512];" in source
    assert "threadgroup half B_tile[512];" in source
    assert "threadgroup float C_tile[512];" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 15u && global_k < split_k_end) ? A[(global_m * 17u) + global_k] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < split_k_end && global_n < 31u) ? B[(global_k * 31u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 15u && col < 31u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 31u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_sixteen_simdgroups_splitk_atomic_uses_per_simdgroup_c_staging():
    module = _simdgroup_micro_gemm_module(
        m=32,
        n=32,
        k=16,
        block_m=32,
        block_n=32,
        threads=512,
        split_k=2,
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup float C_tile[1024];" in source
    assert "uint split_k_index = tgid.z;" in source
    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 32u) + (simdgroup_tile_m * 8u)) * 16u) + (split_k0 + (ko * 8u)), 16u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((split_k0 + (ko * 8u)) * 32u) + ((tgid.x * 32u) + (simdgroup_tile_n * 8u)), 32u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "for (uint c_linear = simdgroup_lane; c_linear < 64u; c_linear += 32u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 32u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_sixteen_simdgroups_splitk_atomic_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=31,
        n=31,
        k=17,
        block_m=32,
        block_n=32,
        threads=512,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[1024];" in source
    assert "threadgroup half B_tile[1024];" in source
    assert "threadgroup float C_tile[1024];" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 31u && global_k < split_k_end) ? A[(global_m * 17u) + global_k] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < split_k_end && global_n < 31u) ? B[(global_k * 31u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 31u && col < 31u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 31u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_sixteen_simdgroups_transposed_splitk_atomic_edge_tail_stages_logical_tiles():
    module = _simdgroup_micro_gemm_module(
        m=31,
        n=31,
        k=17,
        block_m=32,
        block_n=32,
        threads=512,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[1024];" in source
    assert "threadgroup half B_tile[1024];" in source
    assert "threadgroup float C_tile[1024];" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 31u && global_k < split_k_end) ? A[(global_k * 31u) + global_m] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < split_k_end && global_n < 31u) ? B[(global_n * 17u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 31u && col < 31u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 31u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_thirty_two_simdgroups_splitk_atomic_uses_per_simdgroup_c_staging():
    module = _simdgroup_micro_gemm_module(
        m=32,
        n=64,
        k=16,
        block_m=32,
        block_n=64,
        threads=1024,
        split_k=2,
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup float C_tile[2048];" in source
    assert "uint split_k_index = tgid.z;" in source
    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 8u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 8u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "simdgroup_load(A_frag[0], A + (((tgid.y * 32u) + (simdgroup_tile_m * 8u)) * 16u) + (split_k0 + (ko * 8u)), 16u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B + ((split_k0 + (ko * 8u)) * 64u) + ((tgid.x * 64u) + (simdgroup_tile_n * 8u)), 64u, 0, false);" in source
    assert "simdgroup_store(C_frag[0], C_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "for (uint c_linear = simdgroup_lane; c_linear < 64u; c_linear += 32u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 64u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_thirty_two_simdgroups_splitk_atomic_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=31,
        n=63,
        k=17,
        block_m=32,
        block_n=64,
        threads=1024,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[2048];" in source
    assert "threadgroup half B_tile[2048];" in source
    assert "threadgroup float C_tile[2048];" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 8u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 8u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 31u && global_k < split_k_end) ? A[(global_m * 17u) + global_k] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < split_k_end && global_n < 63u) ? B[(global_k * 63u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 31u && col < 63u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 63u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_thirty_two_simdgroups_transposed_splitk_atomic_edge_tail_stages_logical_tiles():
    module = _simdgroup_micro_gemm_module(
        m=31,
        n=63,
        k=17,
        block_m=32,
        block_n=64,
        threads=1024,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[2048];" in source
    assert "threadgroup half B_tile[2048];" in source
    assert "threadgroup float C_tile[2048];" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 8u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 8u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 31u && global_k < split_k_end) ? A[(global_k * 31u) + global_m] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < split_k_end && global_n < 63u) ? B[(global_n * 17u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 31u && col < 63u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 63u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_eight_simdgroups_transposed_splitk_atomic_edge_tail_stages_logical_tiles():
    module = _simdgroup_micro_gemm_module(
        m=15,
        n=31,
        k=17,
        block_m=16,
        block_n=32,
        threads=256,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_idx [[simdgroup_index_in_threadgroup]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[512];" in source
    assert "threadgroup half B_tile[512];" in source
    assert "threadgroup float C_tile[512];" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "uint simdgroup_tile_m = simdgroup_idx / 4u;" in source
    assert "uint simdgroup_tile_n = simdgroup_idx % 4u;" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 15u && global_k < split_k_end) ? A[(global_k * 15u) + global_m] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < split_k_end && global_n < 31u) ? B[(global_n * 17u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "if (row < 15u && col < 31u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 31u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_four_simdgroups_splitk_atomic_edge_tail_uses_per_simdgroup_staging():
    module = _simdgroup_micro_gemm_module(
        m=15,
        n=15,
        k=17,
        block_m=16,
        block_n=16,
        threads=128,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[256];" in source
    assert "threadgroup half B_tile[256];" in source
    assert "threadgroup float C_tile[256];" in source
    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "uint simdgroup_tile_offset = simdgroup_idx * 64u;" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 15u && global_k < split_k_end) ? A[(global_m * 17u) + global_k] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < split_k_end && global_n < 15u) ? B[(global_k * 15u) + global_n] : half(0.0);" in source
    assert "if (row < 15u && col < 15u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 15u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_four_simdgroups_transposed_splitk_atomic_edge_tail_stages_logical_tiles():
    module = _simdgroup_micro_gemm_module(
        m=15,
        n=15,
        k=17,
        block_m=16,
        block_n=16,
        threads=128,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "device atomic_float* C [[buffer(2)]]" in source
    assert "uint simdgroup_lane [[thread_index_in_simdgroup]]" in source
    assert "threadgroup half A_tile[256];" in source
    assert "threadgroup half B_tile[256];" in source
    assert "threadgroup float C_tile[256];" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "A_tile[simdgroup_tile_offset + tile_linear] = (global_m < 15u && global_k < split_k_end) ? A[(global_k * 15u) + global_m] : half(0.0);" in source
    assert "B_tile[simdgroup_tile_offset + tile_linear] = (global_k < split_k_end && global_n < 15u) ? B[(global_n * 17u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile + simdgroup_tile_offset, 8u, 0, false);" in source
    assert "atomic_fetch_add_explicit(&C[(row * 15u) + col], C_tile[simdgroup_tile_offset + c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_splitk_atomic_non_8wide_split_span_uses_staging():
    module = _simdgroup_micro_gemm_module(k=24, split_k=2, output_atomic=True)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint split_k0 = split_k_index * 12u;" in source
    assert "uint split_k_end = split_k0 + 12u;" in source
    assert "threadgroup half A_tile[64];" in source
    assert "threadgroup half B_tile[64];" in source
    assert "uint k_base = split_k0 + (ko * 8u);" in source
    assert "A_tile[tile_linear] = (global_m < 8u && global_k < split_k_end) ? A[(global_m * 24u) + global_k] : half(0.0);" in source
    assert "B_tile[tile_linear] = (global_k < split_k_end && global_n < 8u) ? B[(global_k * 8u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile, 8u, 0, false);" in source
    assert "atomic_fetch_add_explicit(&C[(row * 8u) + col], C_tile[c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_splitk_atomic_ceildiv_tail_uses_min_split_end():
    module = _simdgroup_micro_gemm_module(
        k=17,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "uint k_base = split_k0 + (ko * 8u);" in source
    assert "A_tile[tile_linear] = (global_m < 8u && global_k < split_k_end) ? A[(global_m * 17u) + global_k] : half(0.0);" in source
    assert "B_tile[tile_linear] = (global_k < split_k_end && global_n < 8u) ? B[(global_k * 8u) + global_n] : half(0.0);" in source
    assert "atomic_fetch_add_explicit(&C[(row * 8u) + col], C_tile[c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_splitk_atomic_edge_tiles_use_staging_and_bounds():
    module = _simdgroup_micro_gemm_module(m=10, n=13, k=16, split_k=2, output_atomic=True)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint split_k0 = split_k_index * 8u;" in source
    assert "uint split_k_end = split_k0 + 8u;" in source
    assert "threadgroup half A_tile[64];" in source
    assert "threadgroup half B_tile[64];" in source
    assert "A_tile[tile_linear] = (global_m < 10u && global_k < split_k_end) ? A[(global_m * 16u) + global_k] : half(0.0);" in source
    assert "B_tile[tile_linear] = (global_k < split_k_end && global_n < 13u) ? B[(global_k * 13u) + global_n] : half(0.0);" in source
    assert "if (row < 10u && col < 13u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 13u) + col], C_tile[c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_splitk_atomic_edge_tiles_with_ceildiv_tail_use_min_split_end():
    module = _simdgroup_micro_gemm_module(
        m=10,
        n=13,
        k=17,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint split_k0 = split_k_index * 5u;" in source
    assert "uint split_k_end = min(split_k0 + 5u, 17u);" in source
    assert "A_tile[tile_linear] = (global_m < 10u && global_k < split_k_end) ? A[(global_m * 17u) + global_k] : half(0.0);" in source
    assert "B_tile[tile_linear] = (global_k < split_k_end && global_n < 13u) ? B[(global_k * 13u) + global_n] : half(0.0);" in source
    assert "if (row < 10u && col < 13u) {" in source
    assert "atomic_fetch_add_explicit(&C[(row * 13u) + col], C_tile[c_linear], memory_order_relaxed);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_transposed_operands_use_transposed_simdgroup_loads():
    module = _simdgroup_micro_gemm_module(
        m=16,
        n=24,
        k=8,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "simdgroup_load(A_frag[0], A + ((ko * 8u) * 16u) + (tgid.y * 8u), 16u, 0, true);" in source
    assert "simdgroup_load(B_frag[0], B + ((tgid.x * 8u) * 8u) + (ko * 8u), 8u, 0, true);" in source
    assert "simdgroup_store(C_frag[0], C + ((tgid.y * 8u) * 24u) + (tgid.x * 8u), 24u, 0, false);" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_edge_tiles_use_threadgroup_zero_padding():
    module = _simdgroup_micro_gemm_module(m=10, n=13, k=9)

    source = emit_metal_simdgroup_gemm_source(module)

    assert "uint3 tid [[thread_position_in_threadgroup]]" in source
    assert "uint3 tgid [[threadgroup_position_in_grid]]" in source
    assert "threadgroup half A_tile[64];" in source
    assert "threadgroup half B_tile[64];" in source
    assert "threadgroup float C_tile[64];" in source
    assert "A_tile[tile_linear] = (global_m < 10u && global_k < 9u) ? A[(global_m * 9u) + global_k] : half(0.0);" in source
    assert "B_tile[tile_linear] = (global_k < 9u && global_n < 13u) ? B[(global_k * 13u) + global_n] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile, 8u, 0, false);" in source
    assert "if (row < 10u && col < 13u) {" in source
    assert "C[(row * 13u) + col] = C_tile[c_linear];" in source
    assert "simdgroup_load(A_frag[0], A + " not in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_edge_tiles_with_transposed_operands_stage_logical_tiles():
    module = _simdgroup_micro_gemm_module(
        m=10,
        n=13,
        k=9,
        transpose_a=True,
        transpose_b=True,
    )

    source = emit_metal_simdgroup_gemm_source(module)

    assert "A_tile[tile_linear] = (global_m < 10u && global_k < 9u) ? A[(global_k * 10u) + global_m] : half(0.0);" in source
    assert "B_tile[tile_linear] = (global_k < 9u && global_n < 13u) ? B[(global_n * 9u) + global_k] : half(0.0);" in source
    assert "simdgroup_load(A_frag[0], A_tile, 8u, 0, false);" in source
    assert "simdgroup_load(B_frag[0], B_tile, 8u, 0, false);" in source
    assert "if (row < 10u && col < 13u) {" in source
    assert "whole_program_gpu" not in source


def test_simdgroup_gemm_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    module = _simdgroup_micro_gemm_module()
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices()
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=8 * 8 * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=8 * 8 * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=8 * 8 * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_two_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source two-simdgroup GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 8, 16, 8
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, block_n=16, threads=64)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_two_m_tiles_per_threadgroup_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source two-M-tile simdgroup GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 8, 8
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, block_m=16, threads=64)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_four_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source four-simdgroup GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 16, 8
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, block_m=16, block_n=16, threads=128)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_four_simdgroups_with_transposed_operands_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source four-simdgroup transpose GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 16, 8
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=16,
        threads=128,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_eight_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source eight-simdgroup GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 32, 8
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, block_m=16, block_n=32, threads=256)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_sixteen_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source sixteen-simdgroup GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 32, 32, 8
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=32,
        threads=512,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_thirty_two_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source thirty-two-simdgroup GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 32, 64, 8
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=64,
        threads=1024,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_thirty_two_simdgroups_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source thirty-two-simdgroup edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 31, 63, 9
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=64,
        threads=1024,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_thirty_two_simdgroups_transposed_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source thirty-two-simdgroup transposed edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 31, 63, 9
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=64,
        threads=1024,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_eight_simdgroups_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source eight-simdgroup edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 15, 31, 9
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, block_m=16, block_n=32, threads=256)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_sixteen_simdgroups_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source sixteen-simdgroup edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 31, 31, 9
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=32,
        threads=512,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_sixteen_simdgroups_transposed_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source sixteen-simdgroup transposed edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 31, 31, 9
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=32,
        threads=512,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_eight_simdgroups_transposed_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source eight-simdgroup transposed edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 15, 31, 9
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=32,
        threads=256,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_four_simdgroups_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source four-simdgroup edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 15, 15, 9
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, block_m=16, block_n=16, threads=128)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_four_simdgroups_transposed_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source four-simdgroup transposed edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 15, 15, 9
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=16,
        threads=128,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_edge_tiles_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM edge-tile probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 10, 13, 9
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_edge_tiles_with_transposed_operands_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM edge-transpose probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 10, 13, 9
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_transposed_operands_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM transpose probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 24, 8
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_splitk_atomic_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM split-k atomic probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 8, 8, 16
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, split_k=2, output_atomic=True)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_four_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source four-simdgroup split-k atomic GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 16, 16
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=16,
        threads=128,
        split_k=2,
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_eight_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source eight-simdgroup split-k atomic GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 32, 16
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=32,
        threads=256,
        split_k=2,
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_eight_simdgroups_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source eight-simdgroup split-k atomic edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 15, 31, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=32,
        threads=256,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_eight_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source eight-simdgroup transposed split-k atomic edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 15, 31, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=32,
        threads=256,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_sixteen_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source sixteen-simdgroup split-k atomic GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 32, 32, 16
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=32,
        threads=512,
        split_k=2,
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_sixteen_simdgroups_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source sixteen-simdgroup split-k atomic edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 31, 31, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=32,
        threads=512,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_sixteen_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source sixteen-simdgroup transposed split-k atomic edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 31, 31, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=32,
        threads=512,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_thirty_two_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source thirty-two-simdgroup split-k atomic GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 32, 64, 16
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=64,
        threads=1024,
        split_k=2,
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_thirty_two_simdgroups_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source thirty-two-simdgroup split-k atomic edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 31, 63, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=64,
        threads=1024,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_thirty_two_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source thirty-two-simdgroup transposed split-k atomic edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 31, 63, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=32,
        block_n=64,
        threads=1024,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_four_simdgroups_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source four-simdgroup split-k atomic edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 15, 15, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=16,
        threads=128,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_four_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source four-simdgroup transposed split-k atomic edge-tail GEMM probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 15, 15, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        block_m=16,
        block_n=16,
        threads=128,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
        transpose_a=True,
        transpose_b=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices_transposed(m=m, n=n, k=k, transpose_a=True, transpose_b=True)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=k * m * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=n * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_splitk_atomic_non_8wide_span_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM split-k atomic non-8 span probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 8, 8, 24
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, split_k=2, output_atomic=True)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_splitk_atomic_ceildiv_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM split-k atomic ceildiv tail probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 8, 8, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_splitk_atomic_edge_tiles_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM split-k atomic edge-tile probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 10, 13, 16
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, split_k=2, output_atomic=True)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_splitk_atomic_edge_tiles_with_ceildiv_tail_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM split-k atomic edge-tail probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 10, 13, 17
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        split_k=4,
        split_k_span_mode="ceildiv",
        output_atomic=True,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_nonzero_pipeline_range_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM nonzero K-range probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 8, 8, 16
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        gemm_pipeline_start=1,
        gemm_pipeline_extent=1,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_column_use_swizzle_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM column-swizzle probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 24, 8
    module = _simdgroup_micro_gemm_module(
        m=m,
        n=n,
        k=k,
        swizzle=True,
        swizzle_order="col",
        swizzle_panel_size=1,
    )
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False


def test_simdgroup_gemm_use_swizzle_runtime_source_matches_cpu_oracle(tmp_path):
    if sys.platform != "darwin":
        assert {
            "status": STATUS_SKIPPED_WITH_REASON,
            "reason": "Metal runtime-source simdgroup GEMM swizzle probe requires Darwin.",
            "runtime_launch_executed": False,
        }
        return

    m, n, k = 16, 16, 8
    module = _simdgroup_micro_gemm_module(m=m, n=n, k=k, swizzle=True, swizzle_panel_size=2)
    source = emit_metal_simdgroup_gemm_source(module)
    a, b = _input_matrices(m=m, n=n, k=k)
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    packed = PccPackedArgs(launch_device="metal:0")
    packed.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    packed.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))

    result = run_metal_source_runtime_package(
        module,
        packed,
        tmp_path,
        metal_source=source,
        input_matrices={"A": a, "B": b},
        cpu_reference=cpu,
        output_name="C",
        timeout=30.0,
    )
    data = result.to_dict()

    if result.status == STATUS_SKIPPED_WITH_REASON:
        assert data["runtime_launch_executed"] is False
        assert data["reason"]
        return

    assert result.status == STATUS_SOURCE_RUNTIME_PACKAGE_EXECUTED
    assert data["invocation"]["status"] == "metal_source_runtime_invoked"
    assert data["invocation"]["return_code"] == 0
    assert data["invocation"]["runtime_source_compiled"] is True
    assert data["invocation"]["runtime_launch_executed"] is True
    assert data["invocation"]["fence_completed"] is True
    assert data["cpu_comparison"]["status"] == "metal_cpu_oracle_match"
    assert data["cpu_comparison"]["max_abs_error"] == 0.0
    assert data["allocations_released"] is True
    assert data["whole_program_gpu"] is False
