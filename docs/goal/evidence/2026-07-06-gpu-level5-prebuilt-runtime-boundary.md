# 2026-07-06 GPU Level-5 Prebuilt Runtime Boundary Evidence

## Summary

The pcc1 Metal launcher path is now split between artifact production and
runtime invocation. New `run_metal_source_runtime_prebuilt_package(...)` accepts
an already-built `MetalKernelPackage`, native MTLBuffer runtime artifact, and
runtime-source bridge artifact, then reuses the same launch path for native
buffer allocation, matrix write, runtime-source bridge invocation, optional
CPU-oracle readback, and release.

This does not prove `GPU_LEVEL_5_PCC1_NATIVE`; it removes the host toolchain
build step from the pcc1-facing runtime boundary. Static preflight now separates
full closure blockers from prebuilt-runtime blockers:

- full closure blockers: `ctypes_cdll_load`, `ctypes_dynamic_ffi`,
  `host_subprocess_toolchain`;
- prebuilt runtime blockers: `ctypes_cdll_load`, `ctypes_dynamic_ffi`;
- build-phase blocker: `host_subprocess_toolchain`.

The previous synchronized runtime-source launcher cleanup still holds:
`waitUntilCompleted` passes a NULL fence callback slot and marks the pcc fence
complete only after the synchronous native wait returns success.

## Files

- `pcc/kernel_ir/metal_source_runtime.py`
- `pcc/kernel_ir/pcc1_metal_preflight.py`
- `tests/kernel/test_metal_source_runtime.py`
- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
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

Result: `8 passed in 0.12s`.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `6 passed in 0.40s`.

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `106 passed in 37.68s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware -rs
```

Result: `18 passed in 4.37s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware -rs
```

Result: `18 passed in 4.37s`.

## Claim Boundary

This proves a prebuilt-artifact runtime boundary for the pcc1 launcher track.
It does not prove a pcc1-built no-libpython process executed the launcher path.

`GPU_LEVEL_5_PCC1_NATIVE` remains open until pcc has no-libpython dynamic
library / FFI support for loading the Metal bridge and packing pointer/scalar
ABI arguments, and a pcc1 process executes this prebuilt runtime path with a
Level-4 device result.
