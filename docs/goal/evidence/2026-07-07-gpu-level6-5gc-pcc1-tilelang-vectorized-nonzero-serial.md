# 2026-07-07 GPU Level-6 Five-GC pcc1 TileLang Vectorized Nonzero-Serial Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has Level-6 five-GC proof for one
broader imported TileLang/TIRx runtime-source shape: legal
`T.Parallel`/`T.vectorized` A/B/C tile-copy staging combined with
`T.serial(1, T.ceildiv(K, block_K))`.

The test builds one pcc1 no-libpython executable for the vectorized nonzero
serial runtime-source package, then runs that same executable five times with
`PCC_GC_BACKEND` set to `0`, `1`, `2`, `3`, and `4`.

For every backend, the pcc1-produced executable:

- reads `getenv("PCC_GC_BACKEND")` and prints the backend marker seen inside the
  pcc runtime process;
- creates A, B, and C native MTLBuffers through
  `pcc_metal_buffer_runtime_create_prebuilt(...)`;
- writes the 5x24 A and 24x7 B matrices as little-endian f16 payload bytes,
  including negative values encoded as signed raw i32 payload chunks;
- calls `pcc_metal_source_runtime_call_prebuilt(...)` with the generated
  TileLang/TIRx Metal source and the real runtime-source bridge dylib;
- waits synchronously with NULL callback/context;
- reads the 5x7 f32 C matrix back and checks exact IEEE-754 bit patterns
  against `execute_scalar_tiled_gemm_reference(...)`;
- releases C, B, and A only after the synchronous fence-completed launch has
  returned.

The test records per-backend `native_release_before_fence=false` and
`native_release_after_fence=true`, then classifies the matrix with
`classify_five_gc_gpu_lifetime_result(...)` and requires
`GPU_LEVEL_6_5GC_PARITY`.

## Files

- `tests/gpu_hardware/test_metal_5gc_lifetime_real.py`
- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `docs/goal/task-board.yaml`
- `docs/current-goal-state.md`
- `codex-goal-prompt.md`

## Gates

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_vectorized_nonzero_serial_lifetime_real_or_skipped -rs
```

Result: `1 passed in 2.70s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `8 passed in 5.88s`.

```bash
gtimeout 520s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `12 passed in 7.77s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/python/gc/test_gpu_external_resource_gc0.py \
  tests/python/gc/test_gpu_external_resource_gc1.py \
  tests/python/gc/test_gpu_external_resource_gc2.py \
  tests/python/gc/test_gpu_external_resource_gc3.py \
  tests/python/gc/test_gpu_external_resource_gc4.py -rs
```

Result: `5 passed in 0.09s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one broader imported TileLang/TIRx
runtime-source shape: legal `T.Parallel`/`T.vectorized` tile-copy staging plus a
nonzero serial K-loop range. It is still runtime-source Metal, not `.metallib`.

Still not proven: five-GC parity for arbitrary broader TileLang/TIRx passes,
broader vectorized loop bodies beyond the legal tile-copy staging shape,
arbitrary nested loop forms, arbitrary split-K expressions, external framework
DLPack/stream interop, `.air/.metallib` production, metallib-backed launch,
performance, or whole-program GPU execution.
