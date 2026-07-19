# TileLang Simdgroup Use-Swizzle Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM source path now accepts enabled
  `T.use_swizzle` rasterization metadata instead of failing closed.
- Simdgroup GEMM source uses the same TileLang row/column tile-id remap helper
  as scalar GEMM source, then feeds the logical `tile_gid_x/y` into
  `simdgroup_load` and `simdgroup_store`.
- The default scalar fallback remains unchanged, and the simdgroup path remains
  opt-in through `emit_metal_simdgroup_gemm_source(...)`.
- Runtime-source Metal execution covers enabled row swizzle on a 2x2 tile grid
  (`M=16, N=16, K=8`) and enabled column swizzle on a non-square 3x2 tile grid
  (`M=16, N=24, K=8`), submits the command buffer, completes the fence, reads
  back C, and matches the CPU oracle.

## Reference Notes

The tile-id mapping continues to follow the local TileLang references recorded
in the scalar swizzle slice:

- `~/tilelang/tilelang/language/annotations.py`
- `~/tilelang/src/tl_templates/cuda/threadblock_swizzle.h`
- `~/tilelang/tilelang/contrib/cutedsl/threadblock_swizzle.py`

The pcc implementation still treats this as explicit Kernel IR rasterization
metadata, not as TileLang/TVM runtime execution.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_use_swizzle_remaps_tile_ids \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_use_swizzle_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_column_use_swizzle_remaps_tile_ids \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_column_use_swizzle_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 8 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 61 passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 32 passed.

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

Result: 241 passed.

## Claim Boundary

This proves enabled row/column `T.use_swizzle` tile-id remap for the current
opt-in 8x8x8 f16/f16->f32 Metal simdgroup GEMM runtime-source path. It does
not prove larger TileLang block tiling, multiple simdgroups per threadgroup,
edge-tile predication, simdgroup split-k, arbitrary swizzle placement,
cluster-aware swizzle, arbitrary TileLang loop bodies, arbitrary layout
functions, TMA/wgmma descriptor lowering, performance, `.air/.metallib`
production, pcc1-native GPU launch, five-GC GPU lifetime parity, or
whole-program GPU execution.
