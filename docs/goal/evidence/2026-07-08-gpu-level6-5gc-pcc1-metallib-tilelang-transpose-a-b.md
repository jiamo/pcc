# 2026-07-08 five-GC pcc1 Metallib TileLang Transpose-A/B Evidence

## Summary

`GPU-P0-TILELANG-GEMM-RUNTIME-ORACLE`,
`GPU-P0-METALLIB-OFFLINE-CHAIN`,
`GPU-P0-METAL-PCC1-LAUNCH-REAL`,
`GPU-P0-METAL-5GC-LIFETIME-REAL`, and
`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now have Level-6 proof for the imported
TileLang/TIRx single-sided transpose GEMM paths as pcc1-native prebuilt
`.metallib` workloads.

The covered TileLang slices are:

```text
transpose_A: A shaped (K, M), B shaped (K, N), C shaped (M, N)
transpose_B: A shaped (M, K), B shaped (N, K), C shaped (M, N)
M=5, N=7, K=3
block_M=8, block_N=8, block_K=8
threads=32
A/B: f16
C: f32
```

The transpose_A package builder requires the generated Metal source to retain
the transposed A read/use:

```text
A[(a_row * 5u) + a_col]
A_shared[((kk * 8u) + local_m)]
B_shared[((kk * 8u) + local_n)]
```

The transpose_B package builder requires the generated Metal source to retain
the transposed B read/use:

```text
B[(b_row * 3u) + b_col]
A_shared[((local_m * 8u) + kk)]
B_shared[((local_n * 8u) + kk)]
```

Both compile `.metal -> .air -> .metallib`.

The strict gates build pcc1 no-libpython executables and run each workload
under:

```text
PCC_GC_BACKEND=0
PCC_GC_BACKEND=1
PCC_GC_BACKEND=2
PCC_GC_BACKEND=3
PCC_GC_BACKEND=4
```

For each backend, the pcc1-produced executable verifies the backend marker
inside the pcc runtime process, creates native `id<MTLBuffer>` objects, writes
f16 A/B payloads, calls `pcc_metal_metallib_runtime_call_prebuilt(...)` with the
produced `.metallib`, waits for fence completion, reads f32 C back as exact bit
patterns, checks the `execute_scalar_tiled_gemm_reference(...)` CPU oracle, and
releases native buffers only after readback/fence-completed launch.

This is a `.metallib` proof, not runtime-source Metal. The Level-6 classifier
therefore preserves `runtime_source_compiled=False` and requires
`metallib_produced=True`.

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_transpose_a_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_transpose_a_metallib_lifetime_real_or_skipped \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_transpose_b_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_transpose_b_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `4 passed in 0.67s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_transpose_a_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `1 passed in 2.30s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_transpose_b_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `1 passed in 2.48s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py \
  -rs
```

Result: `81 passed in 0.25s`.

```bash
gtimeout 120s env -u LC_ALL \
  uv run python scripts/goal_state.py validate
```

Result: `OK: 28 tasks validated`.

```bash
gtimeout 60s git diff --check
```

Result: passed.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for two pcc1-native prebuilt `.metallib`
workloads: imported TileLang/TIRx scalar GEMM with single-sided static
`transpose_A` and single-sided static `transpose_B` operand layouts. It does
not prove arbitrary transposed layouts, dynamic transpose flags, transpose
combined with arbitrary split-K/atomic/layout/swizzle forms, arbitrary
executable loop bodies, TMA/wgmma lowering, external framework DLPack/stream
interop, deployment packaging UX, performance, or whole-program GPU execution.
