# TileLang Simdgroup Edge/Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM source path now handles non-atomic
  copy-output edge/tail tiles for the current 8x8x8 f16/f16->f32 microkernel
  slice instead of requiring M/N/K to be multiples of 8.
- Full-tile shapes keep the existing direct global `simdgroup_load` fast path.
  Edge/tail shapes use a correctness-first slow path: each threadgroup fills
  `threadgroup half A_tile[64]` and `threadgroup half B_tile[64]` with
  bounds-checked source loads or `half(0.0)`, runs the simdgroup matrix
  multiply on those logical 8x8 tiles, stores the fragment into
  `threadgroup float C_tile[64]`, then bounds-checks the final C writeback.
- The staged edge path also covers combined `transpose_A=True` and
  `transpose_B=True`: it stages the logical A(M,K) and B(K,N) tiles from
  A(K,M) and B(N,K) storage before the simdgroup load.
- Runtime-source Metal execution covers `M=10, N=13, K=9` for both plain and
  combined transposed operands, submits command buffers, completes fences,
  reads back C, and matches the CPU oracle.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_edge_tiles_use_threadgroup_zero_padding \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_edge_tiles_with_transposed_operands_stage_logical_tiles \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_edge_tiles_runtime_source_matches_cpu_oracle \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_edge_tiles_with_transposed_operands_runtime_source_matches_cpu_oracle
```

Result: 4 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 19 passed.

```bash
gtimeout 720s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 43 passed.

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

Result: 252 passed.

```bash
gtimeout 180s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: OK, 27 tasks validated.

## Claim Boundary

This proves M/N edge-tile and K-tail predication for the current non-atomic
copy-output opt-in 8x8x8 f16/f16->f32 Metal simdgroup GEMM runtime-source path,
including combined transposed A(K,M) / B(N,K) storage. It does not prove
non-divisible simdgroup split-k atomic tails, larger TileLang block tiling,
multiple simdgroups per threadgroup, arbitrary indexed TileLang expressions,
arbitrary/dynamic loop ranges, arbitrary swizzle placement, cluster-aware
swizzle, arbitrary TileLang loop bodies, arbitrary layout functions, TMA/wgmma
descriptor lowering, performance, `.air/.metallib` production, pcc1-native GPU
launch, five-GC GPU lifetime parity, or whole-program GPU execution.
