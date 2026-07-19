# 2026-07-08 GPU Level-6 Five-GC pcc1 TileLang T.Pipelined Step Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has Level-6 five-GC proof for the
same pcc1-native stepped `T.Pipelined(start, end, step, ...)` scalar tiled GEMM
shape covered by the Level-5 slice.

The test builds one pcc1 no-libpython executable for the runtime-source
TileLang stepped-pipelined package, then runs that same executable with
`PCC_GC_BACKEND=0..4`. For every backend, the pcc runtime process verifies the
backend marker with `getenv("PCC_GC_BACKEND")`, creates real native
MTLBuffers, writes A(5,32) and B(32,7) f16 payloads, launches the generated
Metal source through the no-libpython C shim, waits for the synchronous
fence-completed command buffer, reads exact f32 C(5,7) output, and releases
native buffers after the fence.

The matrix is classified with `classify_five_gc_gpu_lifetime_result(...)` and
required as `GPU_LEVEL_6_5GC_PARITY`.

## Gates

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_step_pipelined_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.91s`.

```bash
gtimeout 1500s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_step_pipelined_lifetime_real_or_skipped -rs
```

Result: `1 passed in 2.50s`.

```bash
gtimeout 600s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `28 passed in 0.08s`.

```bash
gtimeout 1800s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `28 passed in 62.48s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs
```

Result: `38 passed in 0.09s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
```

Result: `20 passed in 12.83s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one static stepped
`T.Pipelined(start, end, step, num_stages=0)` scalar tiled GEMM runtime-source
Metal shape under `PCC_GC_BACKEND=0..4`. It is not `.air/.metallib`
production, not metallib-backed launch, not arbitrary dynamic loop bounds, not
arbitrary stepped `T.Pipelined` forms beyond this static K-loop shape, not
arbitrary nested loop lowering, not performance evidence, and not whole-program
GPU execution.
