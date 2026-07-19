# 2026-07-07 GPU Level-6 Five-GC pcc1 TileLang/TIRx GEMM Evidence

## Summary

`GPU-P0-METAL-5GC-LIFETIME-REAL` now has a real Level-6 proof for one
pcc1-native TileLang/TIRx Metal workload across `PCC_GC_BACKEND=0..4`.

The test builds one pcc1 no-libpython executable for the imported
TileLang/TIRx scalar GEMM runtime-source package, then runs that same executable
five times with `PCC_GC_BACKEND` set to `0`, `1`, `2`, `3`, and `4`.

For every backend, the pcc1-produced executable:

- reads `getenv("PCC_GC_BACKEND")` and prints the backend marker seen inside the
  pcc runtime process;
- creates A, B, and C native MTLBuffers through
  `pcc_metal_buffer_runtime_create_prebuilt(...)`;
- writes A=`[[1, 2], [3, 4]]` and B=`[[5, 6], [7, 8]]` as f16 payload bytes;
- calls `pcc_metal_source_runtime_call_prebuilt(...)` with the generated
  TileLang/TIRx Metal source and the real runtime-source bridge dylib;
- waits synchronously with NULL callback/context;
- reads C back as f32 bytes and checks exact IEEE-754 bit patterns for
  `[[19, 22], [43, 50]]`;
- releases C, B, and A only after the synchronous fence-completed launch has
  returned.

The test records per-backend `native_release_before_fence=false` and
`native_release_after_fence=true`, then classifies the matrix with
`classify_five_gc_gpu_lifetime_result(...)` and requires
`GPU_LEVEL_6_5GC_PARITY`.

## Files

- `tests/gpu_hardware/test_metal_5gc_lifetime_real.py`
- `docs/goal/task-board.yaml`
- `docs/current-goal-state.md`
- `codex-goal-prompt.md`

## Gates

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_lifetime_real_or_skipped -rs
```

Result: `1 passed in 3.45s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `6 passed in 2.34s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/python/gc/test_gpu_external_resource_gc0.py \
  tests/python/gc/test_gpu_external_resource_gc1.py \
  tests/python/gc/test_gpu_external_resource_gc2.py \
  tests/python/gc/test_gpu_external_resource_gc3.py \
  tests/python/gc/test_gpu_external_resource_gc4.py -rs
```

Result: `5 passed in 0.26s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one pcc1-native imported TileLang/TIRx
scalar GEMM runtime-source workload: real native MTLBuffers, command-buffer
submission, fence-completed synchronous wait, exact CPU-oracle readback, and
fence-deferred native release across all five GC backends.

Still not proven: five-GC parity for broader TileLang/TIRx GEMM variants,
simdgroup/tensorcore workloads, external framework DLPack/stream interop,
`.air/.metallib` production, metallib-backed launch, performance, or
whole-program GPU execution.
