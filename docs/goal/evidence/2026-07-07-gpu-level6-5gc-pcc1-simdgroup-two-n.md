# 2026-07-07 GPU Level-6 Five-GC pcc1 Simdgroup Two-N Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has Level-6 five-GC proof for the same
broader opt-in Metal simdgroup GEMM tile: `M=8,N=16,K=8` with
`block_M=8,block_N=16,block_K=8` and `threads=64`.

The test builds one pcc1 no-libpython executable for the two-N simdgroup
runtime-source package, then runs that same executable with `PCC_GC_BACKEND=0..4`.
For every backend, the pcc runtime process verifies the backend marker with
`getenv("PCC_GC_BACKEND")`, creates real native MTLBuffers, writes f16 A/B
payloads, launches the generated Metal source through the no-libpython C shim,
waits for the synchronous fence-completed command buffer, reads exact f32
output, and releases native buffers after the fence.

The matrix is classified with `classify_five_gc_gpu_lifetime_result(...)` and
required as `GPU_LEVEL_6_5GC_PARITY`.

## Gates

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 25.78s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_two_n_lifetime_real_or_skipped -rs
```

Result: `1 passed in 2.29s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `15 passed in 20.37s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one broader opt-in simdgroup
runtime-source shape with two 8x8 simdgroups covering the N axis. It is still
runtime-source Metal, not `.metallib`.

Still not proven: arbitrary larger simdgroup/tensorcore tiling, more-than-two
pcc1-native simdgroup tiles, arbitrary/non-f32 atomics, arbitrary split-K
expressions, arbitrary dynamic TileLang/TIRx forms, `.air/.metallib`
production, metallib-backed launch, performance, or whole-program GPU
execution.
