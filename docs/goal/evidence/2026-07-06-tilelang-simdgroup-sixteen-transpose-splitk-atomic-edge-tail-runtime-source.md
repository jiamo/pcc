# TileLang Simdgroup Sixteen Transpose Split-K Atomic Edge/Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for
  sixteen-simdgroup-per-threadgroup split-k atomic output.
- Covered aligned shape: `M=32`, `N=32`, `K=16`, `block_m=32`,
  `block_n=32`, `block_k=8`, `threads=512`, `split_k=2`,
  `output_atomic=True`.
- Covered edge/tail shape: `M=31`, `N=31`, `K=17`, `block_m=32`,
  `block_n=32`, `block_k=8`, `threads=512`, `split_k=4`,
  `split_k_span_mode=ceildiv`, `output_atomic=True`.
- Static transposed A(K,M) and B(N,K) storage is also covered for the edge/tail
  shape; staged tiles normalize that storage into row-major 8x8 simdgroup
  input tiles before `simdgroup_load`.
- Source emission maps sixteen simdgroups onto a 4x4 grid of 8x8 C subtiles,
  uses `tgid.z` for the split-k axis, uses per-simdgroup `C_tile[1024]` for
  aligned atomic accumulation, and uses per-simdgroup `A_tile[1024]`,
  `B_tile[1024]`, and `C_tile[1024]` staging for edge/tail variants.
- Runtime-source Metal execution submits command buffers, completes fences,
  reads back C, and matches CPU oracles for aligned, edge/tail, and combined
  transposed edge/tail variants.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_sixteen_simdgroups_splitk_atomic_uses_per_simdgroup_c_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_sixteen_simdgroups_splitk_atomic_edge_tail_uses_per_simdgroup_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_sixteen_simdgroups_transposed_splitk_atomic_edge_tail_stages_logical_tiles \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_sixteen_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_sixteen_simdgroups_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_sixteen_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle
```

Result: 6 passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 68 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the current sixteen-simdgroup f16/f16->f32 Metal simdgroup
runtime-source split-k atomic variants: aligned output accumulation, M/N edge
predication plus explicit ceildiv K-tail handling, and combined static
`transpose_A` / `transpose_B` storage for the edge/tail case. It does not prove
more than sixteen simdgroups per threadgroup, arbitrary larger TileLang block
tiling, arbitrary split-K expressions, arbitrary/non-f32 atomics, performance,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
