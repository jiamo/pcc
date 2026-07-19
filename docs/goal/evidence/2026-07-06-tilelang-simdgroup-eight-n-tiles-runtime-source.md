# TileLang Simdgroup Eight-N-Tile Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for the
  first eight-simdgroup-per-threadgroup direct-copy tile.
- Covered shape: `M=16`, `N=32`, `K=8`, `block_m=16`, `block_n=32`,
  `block_k=8`, `threads=256`.
- Source emission raises the conservative simdgroup-count cap from four to
  eight and uses `simdgroup_index_in_threadgroup` to map the eight simdgroups
  onto a 2x4 grid of 8x8 C subtiles inside the 16x32 threadgroup tile.
- Runtime-source Metal execution submits the command buffer, completes the
  fence, reads back C, and matches the CPU oracle.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_per_threadgroup_cover_wider_n_tile \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 46 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first eight-simdgroup direct-copy f16/f16->f32 Metal
simdgroup GEMM runtime-source shape, `M=16,N=32,K=8`, with one 2x4 grid of
8x8 simdgroup subtiles inside a threadgroup. It does not prove more than eight
simdgroups per threadgroup, eight-simdgroup edge/tail staging, eight-simdgroup
split-k atomic output, arbitrary larger TileLang block tiling, performance,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
