# TileLang Simdgroup Four-2D-Tile Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM source path now supports the first 2-D
  multiple-simdgroup-per-threadgroup tile: `block_m=16`, `block_n=16`,
  `block_k=8`, `threads=128`.
- Source emission derives `simdgroup_tile_m` / `simdgroup_tile_n` from
  `simdgroup_index_in_threadgroup`; each of the four simdgroups computes one
  8x8 sub-tile inside the 16x16 threadgroup tile.
- Runtime-source Metal execution covers `M=16,N=16,K=8`, submits the command
  buffer, completes the fence, reads back C, and matches the CPU oracle.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_per_threadgroup_cover_2d_tile \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 32 passed.

## Claim Boundary

This proves only the first four-simdgroup 2-D tile for the current opt-in
f16/f16->f32 Metal simdgroup GEMM runtime-source path. It does not prove
arbitrary larger TileLang block tiling, more than four simdgroups per
threadgroup, multiple simdgroups combined with edge/tail/atomic/transpose
variants, performance, `.air/.metallib` production, pcc1-native GPU launch,
five-GC GPU lifetime parity, or whole-program GPU execution.
