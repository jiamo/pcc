# 2026-07-08 five-GC pcc1 Metallib Simdgroup Four-2D Transpose-AB Split-K Atomic Edge/Tail Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN`,
`GPU-P0-SIMDGROUP-TENSORCORE-GEMM`,
`GPU-P0-METAL-PCC1-LAUNCH-REAL`, and
`GPU-P0-METAL-5GC-LIFETIME-REAL` now have Level-6 proof for the first
simdgroup/tensorcore-style prebuilt `.metallib` workload combining
transpose_A+transpose_B operand layout, split-K atomic accumulation, and
non-divisible M/N/K edge-tail guards.

The covered workload is a four-simdgroup edge/tail GEMM with f16 A/B inputs
and f32 C output:

```text
A: 17x15 f16, read as transposed A
B: 15x17 f16, read as transposed B
C: 15x15 f32, lowered as device atomic_float*
split_k: 4 z-axis threadgroups, ceildiv span 5
threads: 128
```

The package builder requires the generated Metal source to use
`simdgroup_multiply_accumulate`, expose both simdgroup and simdgroup-lane
indices, use a 3D threadgroup id, derive `split_k_index = tgid.z`, compute
`split_k0 = split_k_index * 5u`, clamp `split_k_end = min(split_k0 + 5u, 17u)`,
stage guarded transposed A/B shared tiles, and accumulate guarded output with:

```text
atomic_fetch_add_explicit(..., memory_order_relaxed)
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
the odd-size f16 A/B payloads with byte-exact stores, zero-initializes the f32
C payload before launch, calls `pcc_metal_metallib_runtime_call_prebuilt(...)`
with the produced `.metallib`, waits for fence completion, reads f32 C back as
exact bit patterns, checks the `execute_scalar_tiled_gemm_reference(...)` CPU
oracle, and releases native buffers only after readback/fence-completed launch.

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
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `2 passed in 0.63s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `1 passed in 4.63s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py \
  -rs
```

Result: `69 passed in 0.26s`.

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
workload: the first four-simdgroup 2D transpose_A+transpose_B split-K atomic
edge/tail GEMM, covering four z-axis split-K launches over a 15x15 output and
K=17 reduction with ceildiv-clamped K ranges, guarded transposed A/B zero-fill,
explicit C zero initialization, and f32 atomic accumulation. It does not prove
more-than-four simdgroups, non-f32 atomics, arbitrary TileLang/TIRx lowering,
external framework DLPack/stream interop, deployment packaging UX, performance,
or whole-program GPU execution.
