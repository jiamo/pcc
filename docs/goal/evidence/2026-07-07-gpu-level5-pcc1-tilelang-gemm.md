# 2026-07-07 GPU Level-5 pcc1 TileLang/TIRx GEMM Evidence

## Summary

`GPU-P0-METAL-PCC1-LAUNCH-REAL` now has pcc1-native runtime-source Metal proof
for an imported TileLang/TIRx scalar GEMM slice, not only the earlier copy
kernel.

The host pytest harness performs artifact preparation only:

- imports the strict TileLang Python DSL matmul into pcc Kernel IR;
- finalizes that Kernel IR to Metal source through the TIRx/plain-TIR path;
- builds the native MTLBuffer runtime dylib;
- builds the runtime-source command-buffer bridge dylib.

The pcc1-compiled no-libpython executable performs the actual runtime work:

- creates A, B, and C native MTLBuffers through
  `pcc_metal_buffer_runtime_create_prebuilt(...)`;
- writes A=`[[1, 2], [3, 4]]` and B=`[[5, 6], [7, 8]]` as little-endian f16
  payload bytes;
- calls `pcc_metal_source_runtime_call_prebuilt(...)` with the real generated
  TileLang/TIRx Metal source, the real runtime-source bridge dylib, and the
  three native MTLBuffer pointers;
- waits synchronously with NULL callback/context;
- reads C back as f32 bytes and checks exact IEEE-754 bit patterns for
  `[[19, 22], [43, 50]]`;
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
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_imported_tilelang_gemm -rs
```

Result: `1 passed in 2.28s`.

```bash
gtimeout 520s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `10 passed in 4.39s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_GPU_HARDWARE_STRICT=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: `6 passed in 4.10s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one imported TileLang/TIRx scalar GEMM
shape: pcc1 no-libpython process, real native MTLBuffers, real runtime-source
command-buffer submission, fence-completed synchronous wait, f32 readback, and
exact CPU-oracle bit-pattern match.

Still not proven: broader TileLang/TIRx GEMM shapes under pcc1, pcc1-native
simdgroup GEMM, `.air/.metallib` production, metallib-backed launch, five-GC
GPU lifetime parity, broader TileLang/TIRx pass coverage, performance, or
whole-program GPU execution.
