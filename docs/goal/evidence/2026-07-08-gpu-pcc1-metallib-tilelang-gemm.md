# 2026-07-08 pcc1 Metallib TileLang GEMM Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN`, `GPU-P0-TILELANG-GEMM-RUNTIME-ORACLE`, and
`GPU-P0-METAL-PCC1-LAUNCH-REAL` now have the first imported TileLang/TIRx GEMM
proof on the prebuilt `.metallib` path.

The covered workload is a small f16/f16 -> f32 TileLang scalar GEMM imported
through the Kernel IR/TIRx route. The host harness first proves the normal
offline package path:

- import TileLang DSL into Kernel IR
- produce `.metal`, `.air`, and `.metallib`
- build/load the generated `newLibraryWithURL` bridge dylib
- allocate real native `id<MTLBuffer>` buffers
- write f16 A/B payloads
- submit the command buffer through the `.metallib` bridge
- wait for fence completion
- read f32 C back and compare against `execute_scalar_tiled_gemm_reference(...)`

The pcc1 gate then reuses prebuilt artifacts and proves the no-libpython launch
boundary. The pcc1-produced executable creates native buffers, writes the f16
payloads for `A=[[1,2],[3,4]]` and `B=[[5,6],[7,8]]`, calls
`pcc_metal_metallib_runtime_call_prebuilt(...)` with the produced `.metallib`,
reads C back as exact f32 bit patterns, verifies `[[19,22],[43,50]]`, releases
buffers, and does not link libpython.

The classifier accepts this as `GPU_LEVEL_5_PCC1_NATIVE` with:

- `runtime_launch_executed=True`
- `runtime_source_compiled=False`
- `metallib_produced=True`
- `pcc1_native_executed=True`
- `pcc1_no_libpython=True`
- `whole_program_gpu=False`

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  tests/kernel/test_metal_metallib_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_metallib_runtime.py -rs
```

Result: `2 passed in 1.72s`.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/python/test_gpu_metal.py \
  tests/kernel/test_metal_finalize.py \
  tests/kernel/test_metal_package.py \
  tests/kernel/test_metal_metallib_runtime.py \
  -rs
```

Result: `38 passed in 5.03s`.

```bash
gtimeout 150s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_runtime_ffi.py
```

Result: `5 passed in 0.92s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_gemm_metallib \
  -rs
```

Result: `1 passed in 2.04s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_classifier_accepts_pcc1_metallib_device_result \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_real_metallib_copy \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_gemm_metallib \
  -rs
```

Result: `3 passed in 3.24s`.

```bash
gtimeout 60s env -u LC_ALL uv run python scripts/goal_state.py validate
gtimeout 30s git diff --check
```

Results: `OK: 28 tasks validated`; `git diff --check` passed.

## Claim Boundary

This proves imported TileLang/TIRx scalar GEMM on the prebuilt `.metallib`
path, including a pcc1-native no-libpython launcher proof for the 2x2 f16/f16
-> f32 slice. It does not prove simdgroup/tensorcore `.metallib` launch,
five-GC `.metallib` lifetime parity, arbitrary TileLang/TIRx variants,
external framework DLPack/stream interop, deployment packaging UX, performance,
or whole-program GPU execution.
