# TileLang Simdgroup Split-K Atomic Edge Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM source path now handles split-k atomic M/N
  edge tiles for the current 8x8x8 f16/f16->f32 microkernel slice.
- The staged atomic path now triggers for M/N edge tiles as well as non-8-wide
  K spans. It emits `split_k_end`, zero-padded `threadgroup half A_tile[64]`
  / `B_tile[64]` staging, `threadgroup float C_tile[64]`, bounds-checked
  C coordinates, and `atomic_fetch_add_explicit(...)` only for valid output
  elements.
- Runtime-source Metal execution covers both `M=10, N=13, K=16, split_k=2`
  and the combined edge/tail case
  `M=10, N=13, K=17, split_k=4, ceildiv(K, split_k)=5`, submits command
  buffers, completes fences, reads back C, and matches the CPU oracle.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_edge_tiles_use_staging_and_bounds \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_edge_tiles_with_ceildiv_tail_use_min_split_end \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_edge_tiles_runtime_source_matches_cpu_oracle \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_edge_tiles_with_ceildiv_tail_runtime_source_matches_cpu_oracle
```

Result: 4 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 26 passed.

```bash
gtimeout 780s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 50 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 61 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 900s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 259 passed.

```bash
gtimeout 180s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: OK, 27 tasks validated.

## Claim Boundary

This proves split-k atomic M/N edge-tile predication for the current opt-in
8x8x8 f16/f16->f32 Metal simdgroup GEMM runtime-source path, including a
combined M/N edge plus explicit ceildiv K-tail case. It does not prove larger
TileLang block tiling, multiple simdgroups per threadgroup, arbitrary indexed
TileLang expressions, arbitrary/dynamic loop ranges, arbitrary swizzle
placement, cluster-aware swizzle, arbitrary TileLang loop bodies, arbitrary
layout functions, TMA/wgmma descriptor lowering, performance, `.air/.metallib`
production, pcc1-native GPU launch, five-GC GPU lifetime parity, or
whole-program GPU execution.
