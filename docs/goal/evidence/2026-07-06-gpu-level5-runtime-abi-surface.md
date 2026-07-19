# 2026-07-06 GPU Level-5 Runtime ABI Surface Evidence

## Summary

The pcc1-facing Metal launcher now has a pure runtime ABI surface separate from
the CPython `ctypes` host adapter. New `pcc/kernel_ir/metal_runtime_abi.py`
builds a `MetalSourceRuntimeCallPlan` without importing `ctypes`, `pcc.gpu_metal`,
or native buffer host adapters. It records the bridge symbol, prebuilt bridge
dylib path, source hash/length, native buffer slots, scalar slots,
`wait_until_completed`, and the fact that the synchronized path does not require
a Python fence callback.

`invoke_metal_source_runtime_bridge(...)` now builds this pure call plan first
and then the host adapter consumes it for the existing CPython `ctypes` call.
This keeps the real host execution path intact while giving pcc1 a narrower
interface to implement.

Static preflight now has two distinct facts:

- the pcc1 runtime ABI surface is `pcc1_metal_launcher_preflight_ready`;
- the current host execution adapter is still blocked by `ctypes_cdll_load`,
  `ctypes_dynamic_ffi`, and build-phase `host_subprocess_toolchain`.

This still does not prove `GPU_LEVEL_5_PCC1_NATIVE`; it proves the next runtime
contract pcc1 must execute no longer depends on CPython `ctypes` as its
semantic owner.

## Files

- `pcc/kernel_ir/metal_runtime_abi.py`
- `pcc/kernel_ir/metal_source_runtime.py`
- `pcc/kernel_ir/pcc1_metal_preflight.py`
- `tests/kernel/test_metal_source_runtime.py`
- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_runtime_abi.py \
  pcc/kernel_ir/metal_source_runtime.py \
  pcc/kernel_ir/pcc1_metal_preflight.py \
  tests/kernel/test_metal_source_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py -rs
```

Result: `9 passed in 0.12s`.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `7 passed in 0.42s`.

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `107 passed in 37.98s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware -rs
```

Result: `19 passed in 4.57s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware -rs
```

Result: `19 passed in 4.41s`.

## Claim Boundary

This proves a pure ABI call-plan surface and a host-adapter split. It does not
prove a pcc1-built no-libpython process loaded or called a dylib.

`GPU_LEVEL_5_PCC1_NATIVE` remains open until pcc has a no-libpython dynamic FFI
implementation for this call plan and a pcc1 process executes the same prebuilt
runtime path with a Level-4 device result.
