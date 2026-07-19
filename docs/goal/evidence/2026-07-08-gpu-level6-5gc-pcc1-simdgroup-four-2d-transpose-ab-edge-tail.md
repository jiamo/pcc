# 2026-07-08 GPU Level-6 Five-GC pcc1 Simdgroup Four-2D Transpose-AB Edge/Tail Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has Level-6 five-GC proof for the first
2D opt-in Metal simdgroup GEMM edge/tail tile with transposed operands:
`M=15,N=15,K=9`, `block_M=16,block_N=16,block_K=8`, `threads=128`,
`transpose_A=True`, and `transpose_B=True`.

The test builds one pcc1 no-libpython executable for the four-2D transpose_AB
edge/tail simdgroup runtime-source package, then runs that same executable with
`PCC_GC_BACKEND=0..4`. For every backend, the pcc runtime process verifies the
backend marker with `getenv("PCC_GC_BACKEND")`, creates real native MTLBuffers,
writes odd-sized f16 A(K,M) and B(N,K) payloads byte-by-byte, launches the
generated Metal source through the no-libpython C shim, waits for the
synchronous fence-completed command buffer, reads exact f32 C(15,15) output,
and releases native buffers after the fence.

The matrix is classified with `classify_five_gc_gpu_lifetime_result(...)` and
required as `GPU_LEVEL_6_5GC_PARITY`.

## Gates

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_transposed_edge_tail_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.91s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 25.96s`.

```bash
gtimeout 960s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_four_2d_transpose_ab_edge_tail_lifetime_real_or_skipped -rs
```

Result: `1 passed in 2.85s`.

```bash
gtimeout 1020s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `20 passed in 32.76s`.

```bash
gtimeout 120s env -u LC_ALL \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `20 passed in 0.06s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one 2D opt-in simdgroup
runtime-source edge/tail shape with transposed operands, four 8x8 simdgroups
covering M and N, K-tail staging, M/N writeback guards, and odd-sized f16
payload writes. It is still runtime-source Metal, not `.metallib`.

Still not proven: arbitrary larger simdgroup/tensorcore tiling, more-than-four
pcc1-native simdgroup tiles, split-K pcc1-native simdgroup variants,
arbitrary/non-f32 atomics, arbitrary split-K expressions, arbitrary dynamic
TileLang/TIRx forms, `.air/.metallib` production, metallib-backed launch,
performance, or whole-program GPU execution.
