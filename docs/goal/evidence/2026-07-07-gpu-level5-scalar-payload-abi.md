# 2026-07-07 GPU Level-5 Scalar Payload ABI Evidence

## Summary

The pcc1-facing Metal runtime ABI surface now owns scalar packing as pure data,
not as CPython `ctypes` storage objects. `MetalSourceRuntimeCallPlan` records an
aligned scalar payload plus per-scalar ABI offsets and byte sizes:

- `u32` / `i32` -> 4 bytes;
- `u64` / `i64` / `f64` -> 8 bytes;
- `f32` -> 4 bytes;
- `bool` -> 1 byte.

The current CPython host adapter now consumes the call plan's `scalar_payload`
and passes pointers to offsets inside that payload. It no longer rebuilds scalar
storage from dtype-specific `ctypes.c_*` objects. This makes the next
no-libpython adapter target concrete: pass source bytes, native buffer pointer
array, and scalar payload offsets to the bridge.

This still does not prove `GPU_LEVEL_5_PCC1_NATIVE`; it removes another
CPython-owned semantic detail from the launcher path.

## Files

- `pcc/kernel_ir/metal_runtime_abi.py`
- `pcc/kernel_ir/metal_source_runtime.py`
- `tests/kernel/test_metal_source_runtime.py`
- `docs/goal/task-board.yaml`

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_runtime_abi.py \
  pcc/kernel_ir/metal_source_runtime.py \
  tests/kernel/test_metal_source_runtime.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py -rs
```

Result: `10 passed in 0.29s`.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `7 passed in 0.40s`.

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `108 passed in 37.91s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/gpu_hardware -rs
```

Result: `19 passed in 4.37s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware -rs
```

Result: `19 passed in 4.35s`.

## Claim Boundary

This proves scalar ABI packing for the pcc1-facing call plan and confirms the
existing host adapter consumes that plan. It does not prove a pcc1-built
no-libpython process loaded or called a dylib.

`GPU_LEVEL_5_PCC1_NATIVE` remains open until pcc has a no-libpython dynamic FFI
implementation for this call plan and a pcc1 process executes the same prebuilt
runtime path with a Level-4 device result.
