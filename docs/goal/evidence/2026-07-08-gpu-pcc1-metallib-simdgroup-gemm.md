# 2026-07-08 pcc1 Metallib Simdgroup GEMM Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN`, `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`, and
`GPU-P0-METAL-PCC1-LAUNCH-REAL` now have the first opt-in simdgroup GEMM proof
on the prebuilt `.metallib` path.

This slice adds an explicit package/finalize source-emitter hook:

```python
metal_source_emitter=emit_metal_simdgroup_gemm_source
```

The default remains `emit_metal_source`, so scalar fallback semantics are not
changed. The simdgroup gate passes the opt-in emitter through
`finalize_metal(...)`, `build_metal_kernel_package(...)`, and
`run_metal_metallib_runtime_package(...)`, producing a real `.metal -> .air ->
.metallib` artifact whose source contains `simdgroup_multiply_accumulate`.

The host harness proves an 8x8 f16/f16 -> f32 simdgroup micro-GEMM on the
metallib path:

- produce `.metal`, `.air`, and `.metallib` from the simdgroup emitter
- build/load the `newLibraryWithURL` bridge dylib
- allocate native `id<MTLBuffer>` buffers
- write f16 A/B payloads
- submit the command buffer through the `.metallib` bridge
- wait for fence completion
- read f32 C back and compare against `execute_scalar_tiled_gemm_reference(...)`

The pcc1 gate reuses the prebuilt artifacts and proves the no-libpython launch
boundary. The pcc1-produced executable creates native buffers, writes the same
8x8 f16 payloads, calls `pcc_metal_metallib_runtime_call_prebuilt(...)` with
the produced `.metallib`, reads C back as exact f32 bit patterns, verifies the
CPU oracle, releases buffers, and does not link libpython.

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
  pcc/kernel_ir/metal_finalize.py \
  pcc/kernel_ir/metal_package.py \
  pcc/kernel_ir/metal_metallib_runtime.py \
  tests/kernel/test_metal_metallib_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_finalize.py \
  tests/kernel/test_metal_package.py \
  tests/kernel/test_metal_simdgroup_gemm.py \
  tests/kernel/test_metal_metallib_runtime.py \
  -rs
```

Result: `111 passed in 29.13s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/python/test_gpu_metal.py \
  tests/kernel/test_metal_finalize.py \
  tests/kernel/test_metal_package.py \
  tests/kernel/test_metal_metallib_runtime.py \
  -rs
```

Result: `39 passed in 5.85s`.

```bash
gtimeout 150s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_runtime_ffi.py
```

Result: `5 passed in 0.93s`.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_metallib_runtime.py::test_metallib_runtime_package_executes_simdgroup_gemm_or_records_toolchain_skip \
  -rs
```

Result: `1 passed in 0.93s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_gemm_metallib \
  -rs
```

Result: `1 passed in 2.18s`.

```bash
gtimeout 480s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_classifier_accepts_pcc1_metallib_device_result \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_real_metallib_copy \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_gemm_metallib \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_gemm_metallib \
  -rs
```

Result: `4 passed in 4.91s`.

```bash
gtimeout 60s env -u LC_ALL uv run python scripts/goal_state.py validate
gtimeout 30s git diff --check
```

Results: `OK: 28 tasks validated`; `git diff --check` passed.

## Claim Boundary

This proves the first opt-in 8x8 simdgroup GEMM on the prebuilt `.metallib`
path, including pcc1-native no-libpython launch. It does not prove broader
simdgroup/tensorcore metallib variants, thirty-two-simdgroup/tail/split-K
metallib launch, five-GC `.metallib` lifetime parity, arbitrary TileLang/TIRx
variants, external framework DLPack/stream interop, deployment packaging UX,
performance, or whole-program GPU execution.
