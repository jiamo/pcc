# TileLang Simdgroup Nonzero K-Range Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has explicit source and
  runtime-source proof for nonzero `pipeline_start` / `pipeline_extent`
  metadata.
- The tested shape uses `K=16`, `block_K=8`, `pipeline_start=1`, and
  `pipeline_extent=1`, so the simdgroup kernel computes only the second
  8-wide K tile. This catches accidental "always start at zero" behavior.
- The runtime-source Metal result is compared against the same CPU oracle that
  validates scalar GEMM schedule metadata. The default scalar fallback remains
  unchanged, and the simdgroup path remains opt-in.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_nonzero_pipeline_range_uses_selected_k_tiles \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_nonzero_pipeline_range_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 10 passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 34 passed.

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
gtimeout 780s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 243 passed.

## Claim Boundary

This proves nonzero K-loop range metadata for the current opt-in 8x8x8
f16/f16->f32 Metal simdgroup GEMM runtime-source path. It does not prove
larger TileLang block tiling, multiple simdgroups per threadgroup, edge-tile
predication, simdgroup split-k, arbitrary/dynamic loop ranges, arbitrary
swizzle placement, cluster-aware swizzle, arbitrary TileLang loop bodies,
arbitrary layout functions, TMA/wgmma descriptor lowering, performance,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
