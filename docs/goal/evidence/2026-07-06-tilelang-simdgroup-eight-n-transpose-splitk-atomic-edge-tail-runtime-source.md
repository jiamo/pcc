# TileLang Simdgroup Eight-N Transpose Split-K Atomic Edge/Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for the
  first eight-simdgroup-per-threadgroup split-k atomic output shape combining
  M/N edge tiles, a K tail, and static transposed A/B operand layouts.
- Covered shape: `M=15`, `N=31`, `K=17`, `block_m=16`, `block_n=32`,
  `block_k=8`, `threads=256`, `split_k=4`, `split_k_span_mode=ceildiv`,
  `output_atomic=True`, `transpose_A=True`, `transpose_B=True`.
- Source emission maps eight simdgroups onto a 2x4 grid of 8x8 C subtiles,
  uses `tgid.z` for the split-k axis, computes `split_k_end =
  min(split_k0 + 5u, 17u)`, and uses per-simdgroup A/B/C staging:
  `A_tile[512]`, `B_tile[512]`, `C_tile[512]`,
  `thread_index_in_simdgroup`, and `simdgroup_tile_offset`.
- Transposed A(K,M) and B(N,K) storage is normalized into staged row-major
  8x8 simdgroup input tiles before `simdgroup_load`; each simdgroup zero-pads
  its own A/B subtile, bounds-checks final C coordinates, and atomically
  accumulates f32 output.
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
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_splitk_atomic_edge_tail_uses_per_simdgroup_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_transposed_splitk_atomic_edge_tail_stages_logical_tiles \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle
```

Result: 4 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 56 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first eight-simdgroup split-k atomic f32 output
accumulation shape on the current opt-in f16/f16->f32 Metal simdgroup
runtime-source path that combines M/N edge predication, explicit ceildiv K-tail
handling, and static `transpose_A` / `transpose_B` storage. It does not prove
more than eight simdgroups per threadgroup, arbitrary larger TileLang block
tiling, arbitrary split-K expressions, arbitrary/non-f32 atomics, performance,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
