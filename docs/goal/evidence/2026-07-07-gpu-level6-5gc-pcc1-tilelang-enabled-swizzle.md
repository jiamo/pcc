# 2026-07-07 GPU Level-6 Five-GC pcc1 TileLang Enabled Swizzle Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has Level-6 five-GC proof for the
imported TileLang/TIRx enabled `T.use_swizzle(panel_size=2, enable=True)`
runtime-source shape on a non-trivial 3x3 tile grid.

The test builds one pcc1 no-libpython executable for the enabled-swizzle
runtime-source package, then runs that same executable with
`PCC_GC_BACKEND=0..4`. For every backend, the pcc runtime process verifies the
backend marker with `getenv("PCC_GC_BACKEND")`, creates real native MTLBuffers,
writes f16 A/B payloads, launches the generated Metal source through the
no-libpython C shim, waits for the synchronous fence-completed command buffer,
reads exact 17x19 f32 output, and releases native buffers after the fence.

The matrix is classified with `classify_five_gc_gpu_lifetime_result(...)` and
required as `GPU_LEVEL_6_5GC_PARITY`.

## Gates

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_enabled_swizzle_lifetime_real_or_skipped -rs
```

Result: `1 passed in 3.16s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `13 passed in 16.36s`.

```bash
gtimeout 120s env -u LC_ALL \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `13 passed in 0.07s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/python/gc/test_gpu_external_resource_gc0.py \
  tests/python/gc/test_gpu_external_resource_gc1.py \
  tests/python/gc/test_gpu_external_resource_gc2.py \
  tests/python/gc/test_gpu_external_resource_gc3.py \
  tests/python/gc/test_gpu_external_resource_gc4.py -rs
```

Result: `5 passed in 0.29s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one imported TileLang/TIRx
runtime-source enabled-swizzle shape with static
`M=17,N=19,K=16,block_M=8,block_N=8,block_K=8,panel_size=2`. It is still
runtime-source Metal, not `.metallib`.

Still not proven: arbitrary/cluster-aware swizzle placement, arbitrary
`T.use_swizzle` expressions, dynamic shapes, arbitrary TileLang/TIRx forms,
external framework DLPack/stream interop, `.air/.metallib` production,
metallib-backed launch, performance, or whole-program GPU execution.
