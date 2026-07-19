# TileLang Simdgroup Thirty-Two Edge/Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for the
  first thirty-two-simdgroup-per-threadgroup direct-copy tile with M/N edge
  tiles and a K tail.
- Covered shape: `M=31`, `N=63`, `K=9`, `block_m=32`, `block_n=64`,
  `block_k=8`, `threads=1024`.
- Source emission maps thirty-two simdgroups onto a 4x8 grid of 8x8 C
  subtiles and uses per-simdgroup A/B/C staging: `A_tile[2048]`,
  `B_tile[2048]`, `C_tile[2048]`, `thread_index_in_simdgroup`, and
  `simdgroup_tile_offset`.
- Static transposed A(K,M) and B(N,K) storage is also covered; the generated
  source normalizes transposed storage into staged row-major 8x8 simdgroup
  input tiles before `simdgroup_load`.
- Runtime-source Metal execution submits command buffers, completes fences,
  reads back C, and matches CPU oracles for both non-transposed and combined
  transposed edge/tail shapes.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_thirty_two_simdgroups_edge_tail_uses_per_simdgroup_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_thirty_two_simdgroups_transposed_edge_tail_uses_per_simdgroup_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_thirty_two_simdgroups_edge_tail_runtime_source_matches_cpu_oracle \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_thirty_two_simdgroups_transposed_edge_tail_runtime_source_matches_cpu_oracle
```

Result: 4 passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 74 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first thirty-two-simdgroup direct-copy f16/f16->f32 Metal
simdgroup GEMM runtime-source tile with M/N edge predication, a K tail, and
combined static `transpose_A` / `transpose_B` storage. It does not prove more
than thirty-two simdgroups per threadgroup, thirty-two-simdgroup split-k
atomic output, arbitrary larger TileLang block tiling, arbitrary split-K
expressions, arbitrary/non-f32 atomics, performance, `.air/.metallib`
production, pcc1-native GPU launch, five-GC GPU lifetime parity, or
whole-program GPU execution.
