# 2026-07-08 five-GC pcc1 Metallib TileLang Parallel A/B/C Copy Evidence

## Summary

`GPU-P0-TILELANG-GEMM-RUNTIME-ORACLE`,
`GPU-P0-METALLIB-OFFLINE-CHAIN`,
`GPU-P0-METAL-PCC1-LAUNCH-REAL`,
`GPU-P0-METAL-5GC-LIFETIME-REAL`, and
`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now have Level-6 proof for the imported
TileLang/TIRx `T.Parallel` A/B/C tile-copy scalar GEMM path as a pcc1-native
prebuilt `.metallib` workload.

The covered TileLang slice imports this schedule shape:

```text
M=5, N=7, K=16
block_M=8, block_N=8, block_K=8
threads=32
A/B: f16
C: f32
```

Inside the K loop, the importer preserves `T.Parallel(block_M, block_K)` on the
A tile copy, `T.Parallel(block_K, block_N)` on the B tile copy, and
`T.Parallel(block_M, block_N)` on the C writeback. The metallib package builder
requires those parallel metadata records in Kernel IR and requires the generated
Metal source to retain:

```text
threadgroup half A_shared[64];
threadgroup half B_shared[64];
for (uint load = tid; load < 64u; load += 32u)
threadgroup_barrier(mem_flags::mem_threadgroup);
C[(row * 7u) + col] = (float)acc;
```

before compiling `.metal -> .air -> .metallib`.

The strict gate builds one pcc1 no-libpython executable and runs that same
executable under:

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
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_tilelang_parallel_abc_copy_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_parallel_abc_copy_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `2 passed in 0.67s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_tilelang_parallel_abc_copy_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `1 passed in 4.00s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py \
  -rs
```

Result: `77 passed in 0.25s`.

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

This proves `GPU_LEVEL_6_5GC_PARITY` for one pcc1-native prebuilt `.metallib`
workload: imported TileLang/TIRx scalar GEMM with legal `T.Parallel` A/B/C
tile-copy staging and guarded C writeback for the current static f16/f32
GEMM subset. It does not prove arbitrary executable `T.Parallel` loop bodies,
arbitrary vectorized semantics, arbitrary nested/multi-argument loop forms,
arbitrary layout functions/swizzle placement, arbitrary split-K expressions or
non-f32 atomics, TMA/wgmma lowering, external framework DLPack/stream interop,
deployment packaging UX, performance, or whole-program GPU execution.
