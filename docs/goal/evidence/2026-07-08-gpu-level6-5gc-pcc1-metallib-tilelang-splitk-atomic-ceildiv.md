# 2026-07-08 five-GC pcc1 Metallib TileLang split-K Atomic Ceildiv Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN`,
`GPU-P0-TILELANG-GEMM-RUNTIME-ORACLE`,
`GPU-P1-BROADER-TILELANG-TIRX-PASSES`, and
`GPU-P0-METAL-5GC-LIFETIME-REAL` now have Level-6 proof for the first
imported TileLang/TIRx non-divisible split-K atomic scalar GEMM workload on
the prebuilt `.metallib` path.

The covered workload is `M=5,N=7,K=17,split_k=4` f16/f16 -> f32 scalar tiled
GEMM imported through the TileLang/TIRx route with a 3-D `T.Kernel` grid,
`T.atomic_add(C, C_local)` output accumulation, and an explicit ceildiv K-tail
span. The package builder asserts the generated Metal source preserves the
split-K tail guards before compiling the offline artifact:

```text
device atomic_float* C [[buffer(2)]]
uint split_k0 = split_k_index * 5u;
uint split_k_end = min(split_k0 + 5u, 17u);
atomic_fetch_add_explicit
```

The test builds one pcc1 no-libpython executable, then runs that same
executable under:

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
- writes f16 A/B payloads, including odd-sized `K=17` rows through byte-wise
  half payload stores
- explicitly writes a zeroed f32 C payload before the atomic launch
- calls `pcc_metal_metallib_runtime_call_prebuilt(...)` with the produced
  `.metallib`
- waits for fence completion
- reads f32 C back as exact bit patterns
- checks the `execute_scalar_tiled_gemm_reference(...)` CPU oracle
- releases native buffers only after the readback/fence-completed launch

This is a `.metallib` proof, not runtime-source Metal. The Level-6 classifier
therefore preserves `runtime_source_compiled=False` and requires
`metallib_produced=True`.

## Gates

Host metallib split-K ceildiv probe result:
`metal_metallib_runtime_package_executed`, `metallib True`, `launch True`,
`runtime_source False`, `comparison metal_cpu_oracle_match`, and
`max_abs 0.0`.

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_splitk_atomic_ceildiv_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_splitk_atomic_ceildiv_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `2 passed in 0.35s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_splitk_atomic_ceildiv_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `1 passed in 2.49s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py \
  -rs
```

Result: `41 passed in 0.23s`.

```bash
gtimeout 120s env -u LC_ALL \
  uv run python scripts/goal_state.py validate
```

Result: `OK: 28 tasks validated`.

```bash
gtimeout 60s git diff --check
```

Result: passed with no output.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one pcc1-native prebuilt `.metallib`
workload: the first imported TileLang/TIRx non-divisible split-K atomic scalar
GEMM with `K=17,split_k=4`, ceildiv K-tail guards, and explicit zeroed C
initialization. It does not prove five-GC metallib parity for arbitrary
split-K expressions, arbitrary/non-f32 atomics, broader TileLang/TIRx variants,
broader simdgroup/tensorcore variants, external framework DLPack/stream
interop, deployment packaging UX, performance, or whole-program GPU execution.
