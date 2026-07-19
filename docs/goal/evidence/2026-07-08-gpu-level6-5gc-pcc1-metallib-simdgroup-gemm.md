# 2026-07-08 five-GC pcc1 Metallib Simdgroup GEMM Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN`, `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`, and
`GPU-P0-METAL-5GC-LIFETIME-REAL` now have the first five-GC proof for a
prebuilt `.metallib` workload.

The covered workload is the opt-in 8x8 f16/f16 -> f32 simdgroup micro-GEMM.
The test builds one pcc1 no-libpython executable, then runs that same executable
under:

```text
PCC_GC_BACKEND=0
PCC_GC_BACKEND=1
PCC_GC_BACKEND=2
PCC_GC_BACKEND=3
PCC_GC_BACKEND=4
```

For each backend, the pcc1-produced executable:

- verifies the `PCC_GC_BACKEND` marker from inside the pcc runtime process
- creates native `id<MTLBuffer>` objects
- writes the same 8x8 f16 A/B payloads
- calls `pcc_metal_metallib_runtime_call_prebuilt(...)` with the produced
  `.metallib`
- waits for fence completion
- reads f32 C back as exact bit patterns
- checks the CPU oracle
- releases native buffers only after the readback/fence-completed launch

The Level-6 classifier now preserves `runtime_source_compiled=False` for
metallib-backed five-GC records, instead of claiming runtime-source compilation.

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  pcc/kernel_ir/gpu_claims.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Result: passed.

```bash
gtimeout 120s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `2 passed in 0.32s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `1 passed in 2.21s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_lifetime_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `3 passed in 0.06s`.

```bash
gtimeout 480s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_gemm_metallib \
  -rs
```

Result: `1 passed in 1.95s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `33 passed in 0.08s`.

## Claim Boundary

This proves five-GC lifetime parity for one pcc1-native prebuilt `.metallib`
workload: the first opt-in 8x8 simdgroup GEMM. It does not prove five-GC
metallib parity for TileLang/TIRx scalar GEMM, broader simdgroup/tensorcore
variants, thirty-two-simdgroup/tail/split-K metallib launch, external framework
DLPack/stream interop, deployment packaging UX, performance, or whole-program
GPU execution.
