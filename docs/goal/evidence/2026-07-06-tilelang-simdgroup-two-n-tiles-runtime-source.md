# TileLang Simdgroup Two-N-Tile Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM source path now supports the first
  multiple-simdgroup-per-threadgroup slice: `block_m=8`, `block_n=16`,
  `block_k=8`, `threads=64`.
- The source uses `simdgroup_index_in_threadgroup` to assign each simdgroup
  one 8-column sub-tile inside the 8x16 threadgroup tile.
- Runtime-source Metal execution covers `M=8, N=16, K=8`, submits the command
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_two_simdgroups_per_threadgroup_cover_n_tiles \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_two_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 28 passed.

## Claim Boundary

This proves only the first two-simdgroup N-direction tile for the current
opt-in f16/f16->f32 Metal simdgroup GEMM runtime-source path. It does not prove
arbitrary larger TileLang block tiling, M-direction multiple simdgroups,
2-D simdgroup tiling, multiple simdgroups combined with edge/tail/atomic/
transpose variants, performance, `.air/.metallib` production, pcc1-native GPU
launch, five-GC GPU lifetime parity, or whole-program GPU execution.
