# 2026-07-08 GPU Level-6 Five-GC pcc1 Simdgroup Four-2D Split-K Atomic Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has Level-6 five-GC proof for the first
2D opt-in Metal simdgroup GEMM split-K atomic tile:
`M=16,N=16,K=16`, `block_M=16,block_N=16,block_K=8`, `split_k=2`, and
`threads=128`.

The test builds one pcc1 no-libpython executable for the four-2D split-K atomic
simdgroup runtime-source package, then runs that same executable with
`PCC_GC_BACKEND=0..4`. For every backend, the pcc runtime process verifies the
backend marker with `getenv("PCC_GC_BACKEND")`, creates real native MTLBuffers,
writes f16 A/B payloads, explicitly zeroes C before launch, launches the
generated Metal source through the no-libpython C shim, waits for the
synchronous fence-completed command buffer, reads exact f32 C(16,16) output,
and releases native buffers after the fence.

The matrix is classified with `classify_five_gc_gpu_lifetime_result(...)` and
required as `GPU_LEVEL_6_5GC_PARITY`.

## Gates

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.10s`.

```bash
gtimeout 960s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_four_2d_splitk_atomic_lifetime_real_or_skipped -rs
```

Result: `1 passed in 2.94s`.

```bash
gtimeout 1080s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `21 passed in 36.29s`.

```bash
gtimeout 120s env -u LC_ALL \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `21 passed in 0.07s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 27.34s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one 2D opt-in simdgroup
runtime-source split-K atomic f32 accumulation shape. It is still
runtime-source Metal, not `.metallib`, and not whole-program GPU.

Still not proven: non-divisible/tail split-K simdgroup variants,
transpose-plus-split-K simdgroup variants, arbitrary larger simdgroup/tensorcore
tiling, more-than-four pcc1-native simdgroup tiles, arbitrary/non-f32 atomics,
arbitrary split-K expressions, `.air/.metallib` production, metallib-backed
launch, performance, or whole-program GPU execution.
