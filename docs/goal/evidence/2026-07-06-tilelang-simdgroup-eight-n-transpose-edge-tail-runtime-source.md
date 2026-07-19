# TileLang Simdgroup Eight-N Transpose Edge/Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for the
  first eight-simdgroup-per-threadgroup direct-copy tile combining static
  transposed operand layouts with M/N edge tiles and a K tail.
- Covered shape: `M=15`, `N=31`, `K=9`, `block_m=16`, `block_n=32`,
  `block_k=8`, `threads=256`, `transpose_A=True`, `transpose_B=True`.
- Source emission maps eight simdgroups onto a 2x4 grid of 8x8 C subtiles and
  uses per-simdgroup A/B/C staging: `A_tile[512]`, `B_tile[512]`,
  `C_tile[512]`, `thread_index_in_simdgroup`, and `simdgroup_tile_offset`.
- Transposed A(K,M) and B(N,K) storage is normalized into staged row-major
  8x8 simdgroup input tiles before `simdgroup_load`; each simdgroup zero-pads
  its own A/B subtile and bounds-checks final C writeback.
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_transposed_edge_tail_uses_per_simdgroup_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_transposed_edge_tail_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 50 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first eight-simdgroup direct-copy f16/f16->f32 Metal
simdgroup GEMM runtime-source shape combining static `transpose_A` /
`transpose_B`, M/N edge predication, and a K tail. It does not prove more than
eight simdgroups per threadgroup, eight-simdgroup split-k atomic output,
arbitrary larger TileLang block tiling, performance, `.air/.metallib`
production, pcc1-native GPU launch, five-GC GPU lifetime parity, or
whole-program GPU execution.
