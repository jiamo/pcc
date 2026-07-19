# 2026-07-07 GPU Level-6 Five-GC pcc1 TileLang Transpose A+B Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has Level-6 five-GC proof for the
imported TileLang/TIRx `T.gemm(..., True, True)` runtime-source shape.

The test builds one pcc1 no-libpython executable for the transpose_A+transpose_B
runtime-source package, then runs that same executable with
`PCC_GC_BACKEND=0..4`. For every backend, the pcc runtime process verifies the
backend marker with `getenv("PCC_GC_BACKEND")`, creates real native MTLBuffers,
writes odd-sized f16 A/B payloads byte-by-byte, launches the generated Metal
source through the no-libpython C shim, waits for the synchronous
fence-completed command buffer, reads exact f32 output, and releases native
buffers after the fence.

The matrix is classified with `classify_five_gc_gpu_lifetime_result(...)` and
required as `GPU_LEVEL_6_5GC_PARITY`.

## Gates

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_transpose_ab_lifetime_real_or_skipped -rs
```

Result: `1 passed in 2.59s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `11 passed in 12.11s`.

```bash
gtimeout 120s env -u LC_ALL \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `11 passed in 0.06s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/python/gc/test_gpu_external_resource_gc0.py \
  tests/python/gc/test_gpu_external_resource_gc1.py \
  tests/python/gc/test_gpu_external_resource_gc2.py \
  tests/python/gc/test_gpu_external_resource_gc3.py \
  tests/python/gc/test_gpu_external_resource_gc4.py -rs
```

Result: `5 passed in 0.27s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one imported TileLang/TIRx
runtime-source transpose shape: `T.gemm(A_shared, B_shared, C_local, True,
True)` with static `M=5,N=7,K=3`. It is still runtime-source Metal, not
`.metallib`.

Still not proven: arbitrary transposed layouts, dynamic shapes, arbitrary
TileLang/TIRx forms, external framework DLPack/stream interop, `.air/.metallib`
production, metallib-backed launch, performance, or whole-program GPU
execution.
