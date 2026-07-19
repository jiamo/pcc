# 2026-07-08 pcc1 Metallib Copy Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN` and `GPU-P0-METAL-PCC1-LAUNCH-REAL` now have
the first pcc1-native no-libpython proof for a prebuilt `.metallib` launch.

The covered workload is the same shaped 2x2 f32 Kernel IR copy kernel used by
the host metallib package gate. The host pytest harness builds only the
prebuilt artifacts:

- `.metal -> .air -> .metallib`
- the Objective-C `newLibraryWithURL` bridge dylib
- the native MTLBuffer helper dylib

The pcc1-produced executable then runs without libpython and performs the
actual launch path:

- creates real native `id<MTLBuffer>` objects through
  `pcc_metal_buffer_runtime_create_prebuilt(...)`
- writes four f32 bit patterns into the source buffer
- calls `pcc_metal_metallib_runtime_call_prebuilt(...)` with the bridge dylib,
  symbol name, and produced `.metallib` path
- waits synchronously for fence completion through the bridge
- reads the destination buffer back and checks exact f32 bit patterns
- releases native buffers after the launch result is verified

The classifier accepts this as `GPU_LEVEL_5_PCC1_NATIVE` with:

- `runtime_launch_executed=True`
- `runtime_source_compiled=False`
- `metallib_produced=True`
- `pcc1_native_executed=True`
- `pcc1_no_libpython=True`
- `whole_program_gpu=False`

## Gates

```bash
gtimeout 120s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_runtime_ffi.py
```

Result: `5 passed in 0.94s`.

```bash
gtimeout 120s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_classifier_accepts_pcc1_metallib_device_result
```

Result: `1 passed in 0.05s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_real_metallib_copy \
  -rs
```

Result: `1 passed in 1.84s`.

`pcc/py_runtime` was rebuilt before the pcc1 gate so the new
`pcc_metal_metallib_runtime_call_prebuilt(...)` C shim was present in
`libpy_runtime.a`.

## Claim Boundary

This proves one pcc1-native no-libpython prebuilt `.metallib` copy launch. It
does not prove imported TileLang/TIRx GEMM metallib-backed execution,
simdgroup/tensorcore metallib-backed execution, five-GC metallib-backed
lifetime parity, external framework DLPack/stream interop, deployment packaging
UX, performance, or whole-program GPU execution.
