# 2026-07-08 GPU Level-6 Five-GC pcc1 TileLang Vectorized Annotations Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has Level-6 five-GC proof for the
same pcc1-native TileLang vectorized annotation metadata slice covered by the
Level-5 evidence.

The test builds one pcc1 no-libpython executable for the runtime-source
TileLang vectorized-annotations package, then runs that same executable with
`PCC_GC_BACKEND=0..4`. For every backend, the pcc runtime process verifies the
backend marker, creates real native MTLBuffers, writes A(5,16) and B(16,7) f16
payloads, launches the generated Metal source through the no-libpython C shim,
waits for the synchronous fence-completed command buffer, reads exact f32
C(5,7) output, and releases native buffers after the fence.

The matrix is classified with `classify_five_gc_gpu_lifetime_result(...)` and
required as `GPU_LEVEL_6_5GC_PARITY`.

## Gates

```bash
gtimeout 600s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_vectorized_annotations_lifetime_real_or_skipped -rs
```

Result: `1 passed in 0.37s`.

```bash
gtimeout 1500s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_vectorized_annotations_lifetime_real_or_skipped -rs
```

Result: `1 passed in 2.11s`.

```bash
gtimeout 1800s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `31 passed in 70.34s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one legal
`T.vectorized(0, extent, annotations={...})` tile-copy staging form in the
current scalar tiled GEMM subset under `PCC_GC_BACKEND=0..4`. It is not
arbitrary vectorized loop-body execution, not nonzero vectorized starts, not
vectorized step support, not external framework DLPack/stream interop, not
`.air/.metallib` production, not metallib-backed launch, not performance
evidence, and not whole-program GPU execution.
