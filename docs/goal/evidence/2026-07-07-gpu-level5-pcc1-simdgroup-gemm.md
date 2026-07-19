# 2026-07-07 GPU Level-5 pcc1 Simdgroup GEMM Evidence

## Summary

`GPU-P0-METAL-PCC1-LAUNCH-REAL` now has pcc1-native runtime-source Metal proof
for the opt-in 8x8 f16/f16->f32 simdgroup GEMM microkernel, not only copy and
scalar TileLang/TIRx GEMM slices.

The host pytest harness performs artifact preparation only:

- builds the Kernel IR 8x8 simdgroup GEMM module and launch plan;
- emits the opt-in Metal simdgroup source through
  `emit_metal_simdgroup_gemm_source(...)`;
- builds the native MTLBuffer runtime dylib;
- builds the runtime-source command-buffer bridge dylib.

The pcc1-compiled no-libpython executable performs the actual runtime work:

- creates A, B, and C native MTLBuffers through
  `pcc_metal_buffer_runtime_create_prebuilt(...)`;
- writes 8x8 A and B matrices as little-endian f16 payload bytes;
- calls `pcc_metal_source_runtime_call_prebuilt(...)` with the real generated
  simdgroup Metal source, the real runtime-source bridge dylib, and the three
  native MTLBuffer pointers;
- waits synchronously with NULL callback/context;
- reads the 8x8 f32 C matrix back and checks exact IEEE-754 bit patterns
  generated from `execute_scalar_tiled_gemm_reference(...)`;
- releases all three native buffers.

Both pcc1 and the pcc1-produced probe executable are checked with `otool -L` and
do not link libpython. The test classifies the result with
`classify_pcc1_native_gpu_result(...)` and requires `GPU_LEVEL_5_PCC1_NATIVE`.

## Files

- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `docs/goal/task-board.yaml`
- `docs/current-goal-state.md`
- `codex-goal-prompt.md`

## Gates

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_gemm -rs
```

Result: `1 passed in 3.18s`.

```bash
gtimeout 520s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `11 passed in 7.08s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_GPU_HARDWARE_STRICT=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: `6 passed in 4.92s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for the first opt-in 8x8 simdgroup GEMM
runtime-source slice: pcc1 no-libpython process, real native MTLBuffers, real
runtime-source command-buffer submission, fence-completed synchronous wait, f32
readback, and exact CPU-oracle bit-pattern match.

Still not proven: pcc1-native broader simdgroup/tensorcore tiling, broader
TileLang/TIRx GEMM variants under pcc1, `.air/.metallib` production,
metallib-backed launch, five-GC GPU lifetime parity, performance, or
whole-program GPU execution.
