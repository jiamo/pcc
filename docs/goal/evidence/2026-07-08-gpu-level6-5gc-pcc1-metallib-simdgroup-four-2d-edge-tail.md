# 2026-07-08 five-GC pcc1 Metallib Simdgroup Four-2D Edge/Tail Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN`,
`GPU-P0-SIMDGROUP-TENSORCORE-GEMM`,
`GPU-P0-METAL-PCC1-LAUNCH-REAL`, and
`GPU-P0-METAL-5GC-LIFETIME-REAL` now have Level-6 proof for the first
simdgroup/tensorcore-style prebuilt `.metallib` workload with 2D MxN tiling
and non-divisible M/N/K edge-tail guards.

The covered workload is a four-simdgroup edge/tail GEMM with f16 A/B inputs
and f32 C output:

```text
A: 15x9 f16
B: 9x15 f16
C: 15x15 f32
logical tile: 16x16 output over K=9
threads: 128
```

The package builder requires the generated Metal source to use
`simdgroup_multiply_accumulate`, expose both simdgroup and simdgroup-lane
indices, stage per-simdgroup A/B/C tiles, split the simdgroup tile over both
axes, zero-fill guarded K-tail loads, and guard final C writeback:

```text
A_tile[...] = (global_m < 15u && global_k < 9u) ? ... : half(0.0)
B_tile[...] = (global_k < 9u && global_n < 15u) ? ... : half(0.0)
if (row < 15u && col < 15u) { ... }
```

before compiling `.metal -> .air -> .metallib`.

The test builds one pcc1 no-libpython executable, then runs that same
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
f16 A/B payloads, calls `pcc_metal_metallib_runtime_call_prebuilt(...)` with
the produced `.metallib`, waits for fence completion, reads f32 C back as exact
bit patterns, checks the `execute_scalar_tiled_gemm_reference(...)` CPU oracle,
and releases native buffers only after readback/fence-completed launch.

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
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_edge_tail_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_four_2d_edge_tail_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `2 passed in 0.66s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_four_2d_edge_tail_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `1 passed in 4.20s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py \
  -rs
```

Result: `61 passed in 0.26s`.

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
workload: the first four-simdgroup 2D edge/tail GEMM, covering a 2x2 grid of
8x8 simdgroup tiles over a 15x15 output and K=9 reduction with guarded
zero-fill loads and guarded C writeback. It does not prove transpose_AB
edge/tail metallib, split-K atomics, more-than-four simdgroups, non-f32
atomics, arbitrary TileLang/TIRx lowering, external framework DLPack/stream
interop, deployment packaging UX, performance, or whole-program GPU execution.
