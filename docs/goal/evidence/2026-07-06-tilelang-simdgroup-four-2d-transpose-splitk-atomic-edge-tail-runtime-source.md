# TileLang Simdgroup Four-2D Transpose Split-K Atomic Edge/Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for the
  first four-simdgroup 2-D tile that combines static transposed operand layouts,
  split-k atomic output, M/N edge tiles, and a non-8-wide K split tail.
- Covered shape: `M=15`, `N=15`, `K=17`, `block_m=16`, `block_n=16`,
  `block_k=8`, `threads=128`, `split_k=4`, `split_k_span=ceildiv(K, split_k)=5`,
  `transpose_A=True`, `transpose_B=True`.
- Source emission uses `tgid.z`, `split_k_end = min(split_k0 + 5u, 17u)`,
  per-simdgroup A/B/C staging, `thread_index_in_simdgroup`, and
  `simdgroup_tile_offset`.
- Transposed A(K,M) and B(N,K) storage is normalized into staged row-major
  8x8 simdgroup input tiles before `simdgroup_load`; each simdgroup zero-pads
  its own A/B subtile and atomically accumulates only valid C elements.
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_transposed_splitk_atomic_edge_tail_stages_logical_tiles \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 44 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first four-simdgroup 2-D tile that combines static
`transpose_A` / `transpose_B`, split-k atomic f32 output accumulation, M/N edge
predication, and an explicit ceildiv K split tail for the current opt-in
f16/f16->f32 Metal simdgroup GEMM runtime-source path. It does not prove
arbitrary larger TileLang block tiling, more than four simdgroups per
threadgroup, arbitrary/non-f32 atomics, arbitrary split-K index expressions,
arbitrary indexed TileLang expressions, performance, `.air/.metallib`
production, pcc1-native GPU launch, five-GC GPU lifetime parity, or
whole-program GPU execution.
