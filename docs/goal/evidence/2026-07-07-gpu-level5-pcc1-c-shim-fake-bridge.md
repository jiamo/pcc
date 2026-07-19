# 2026-07-07 GPU Level-5 pcc1 C-Shim Fake-Bridge Evidence

## Summary

The Level-5 Metal launcher path now has pcc1-native proof for the dynamic FFI
boundary and native-buffer C ABI wrappers, still against a fake bridge rather
than real Metal.

A fresh no-libpython pcc1 was built from the current worktree:

```bash
gtimeout 900s env -u LC_ALL uv run pcc --backend self \
  --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o build/bootstrap-gpu-level5-pcc1-shim/pcc1
```

Result: passed. `otool -L build/bootstrap-gpu-level5-pcc1-shim/pcc1` reports
only `/usr/lib/libSystem.B.dylib`; no libpython linkage.

Using that pcc1, the Level-5 gate compiles a strict no-libpython probe that:

- declares `pcc_metal_source_runtime_call_prebuilt(...)` through `pcc.extern`;
- declares `pcc_metal_buffer_runtime_{create,length,write,read,release}_prebuilt(...)`
  through `pcc.extern`;
- creates a native buffer through the C shim, checks its byte length, writes
  bytes into it, reads them back, and releases it;
- builds raw source/buffer/scalar ABI arrays with `pcc.unsafe`;
- calls the no-libpython C runtime shim;
- reaches fake prebuilt dylib source and buffer-runtime symbols through
  `dlopen`/`dlsym`;
- passes NULL fence callback/context and synchronous wait semantics;
- returns `0` from the fake bridge.

This proves the pcc1-compiled no-libpython execution path can call the same C
shim that replaces Python `ctypes` dynamic FFI for runtime-source Metal launch,
including the native-buffer create/write/read/release surface needed by the
real package path.

## Files

- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `tests/kernel/test_metal_runtime_ffi.py`
- `pcc/py_runtime/src/pcc_metal_runtime.c`
- `pcc/py_runtime/include/py_runtime.h`
- `pcc/py_runtime/Makefile`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 300s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_calls_runtime_c_shim_fake_bridge -rs
```

Result after rebuilding pcc1 from the current worktree: `1 passed in 1.17s`.

```bash
gtimeout 300s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result after rebuilding pcc1 from the current worktree: `8 passed in 1.16s`.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_runtime_ffi.py -rs
```

Result: `4 passed in 0.98s`.

```bash
gtimeout 300s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_GPU_HARDWARE_STRICT=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result after rebuilding pcc1 from the current worktree: `6 passed in 4.30s`.

## Claim Boundary

This is pcc1-native proof of the C-shim dynamic FFI and native-buffer ABI
boundary, not `GPU_LEVEL_5_PCC1_NATIVE` for a real Metal workload.

Still not proven: a pcc1 process executing the prebuilt runtime-source package
against real Metal buffers/kernel source and producing a Level-4 device result,
`.air/.metallib` production, five-GC GPU lifetime parity, performance, or
whole-program GPU execution.
