# 2026-07-08 five-GC pcc1 Metallib Simdgroup Two-M Evidence

## Summary

`GPU-P0-METALLIB-OFFLINE-CHAIN`,
`GPU-P0-SIMDGROUP-TENSORCORE-GEMM`,
`GPU-P0-METAL-PCC1-LAUNCH-REAL`, and
`GPU-P0-METAL-5GC-LIFETIME-REAL` now have Level-6 proof for the first
simdgroup/tensorcore-style prebuilt `.metallib` workload that expands the
initial 8x8 microkernel along the M axis.

The covered workload is a two-M 16x8 output GEMM with f16 A/B inputs and f32 C
output:

```text
A: 16x8 f16
B: 8x8 f16
C: 16x8 f32
threads: 64
```

The package builder requires the generated Metal source to use
`simdgroup_multiply_accumulate`, expose
`simdgroup_index_in_threadgroup`, split the simdgroup tile over M with
`uint simdgroup_tile_m = simdgroup_idx / 1u;`, and store with the expected
M-stride `simdgroup_store(..., 8u, ...)` before compiling
`.metal -> .air -> .metallib`.

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

Host metallib two-M simdgroup probe result:
`metal_artifacts_produced`, `metallib True`, bridge load-validated,
`simdgroup_multiply_accumulate True`, `simdgroup_tile_m True`,
`simdgroup_tile_n True`, M-stride store proof true, and CPU-oracle output shape
`16x8`.

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
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_level6_classifier_accepts_complete_pcc1_five_gc_simdgroup_two_m_metallib_matrix \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_two_m_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `2 passed in 0.55s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_two_m_metallib_lifetime_real_or_skipped \
  -rs
```

Result: `1 passed in 2.07s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py \
  -rs
```

Result: `55 passed in 0.22s`.

```bash
gtimeout 120s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: `OK: 28 tasks validated`.

```bash
gtimeout 60s git diff --check
```

Result: passed with no output.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one pcc1-native prebuilt `.metallib`
workload: the first two-M simdgroup GEMM, covering two 8x8 simdgroup tiles
across the M axis inside one 16x8 output. It does not prove 2D MxN simdgroup
tiling, split-K atomics, edge/tail guards, more-than-two simdgroups,
non-f32 atomics, arbitrary TileLang/TIRx lowering, external framework
DLPack/stream interop, deployment packaging UX, performance, or whole-program
GPU execution.
