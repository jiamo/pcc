# 2026-07-07 GPU Level-5 pcc1 Real Runtime-Source Copy Evidence

## Summary

`GPU-P0-METAL-PCC1-LAUNCH-REAL` now has a real pcc1-native runtime-source
Metal device-result proof for the first copy-kernel slice.

The host pytest harness only builds the prebuilt Objective-C artifacts:

- native MTLBuffer runtime dylib;
- runtime-source command-buffer bridge dylib;
- `.metal` source artifact for a 4-element f32 copy kernel.

The pcc1-compiled no-libpython executable then performs the runtime work:

- creates source and destination native MTLBuffers through
  `pcc_metal_buffer_runtime_create_prebuilt(...)`;
- writes four f32 bit patterns into the source buffer through the C shim;
- calls `pcc_metal_source_runtime_call_prebuilt(...)` with the real
  runtime-source bridge dylib, real Metal source bytes, two native buffer
  pointers, scalar `n=4`, NULL fence callback/context, and synchronous wait;
- reads destination bytes back through the C shim;
- compares the four copied f32 bit patterns exactly;
- releases both native buffers.

Both the pcc1 compiler binary and the pcc1-produced probe executable are checked
with `otool -L` and do not link libpython. The test also classifies the result
with `classify_pcc1_native_gpu_result(...)` and requires
`GPU_LEVEL_5_PCC1_NATIVE`.

## Files

- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `pcc/py_runtime/src/pcc_metal_runtime.c`
- `pcc/py_runtime/include/py_runtime.h`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 300s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_real_runtime_source_copy -rs
```

Result: `1 passed in 1.89s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `9 passed in 2.70s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_GPU_HARDWARE_STRICT=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: `6 passed in 4.46s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for the first runtime-source Metal copy
kernel: pcc1 no-libpython process, real native MTLBuffers, real runtime-source
command-buffer submission, fence-completed synchronous wait, readback, and CPU
byte oracle match.

Still not proven: pcc1-native TileLang/TIRx scalar GEMM, pcc1-native simdgroup
GEMM, `.air/.metallib` production, metallib-backed launch, five-GC GPU lifetime
parity, broader TileLang/TIRx pass coverage, performance, or whole-program GPU
execution.
