# 2026-07-08 GPU Level-6 Five-GC pcc1 Simdgroup Thirty-Two Transpose_AB Split-K Atomic Edge/Tail Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has Level-6 five-GC proof for the first
thirty-two-simdgroup opt-in Metal simdgroup GEMM tile that combines
`transpose_A`, `transpose_B`, split-K atomic output, M/N edge tiles, and a K
tail: `M=31,N=63,K=17`, `block_M=32,block_N=64,block_K=8`, `split_k=4`,
`split_k_span_mode=ceildiv`, and `threads=1024`.

The test builds one pcc1 no-libpython executable for the thirty-two-simdgroup
transpose_AB split-K atomic edge/tail simdgroup runtime-source package, then
runs that same executable with `PCC_GC_BACKEND=0..4`. For every backend, the
pcc runtime process verifies the backend marker with `getenv("PCC_GC_BACKEND")`,
creates real native MTLBuffers, writes A(K,M)=A(17,31) and B(N,K)=B(63,17) f16
payloads byte-by-byte, explicitly zeroes C before launch, launches the
generated Metal source through the no-libpython C shim, waits for the
synchronous fence-completed command buffer, reads exact f32 C(31,63) output,
and releases native buffers after the fence.

The matrix is classified with `classify_five_gc_gpu_lifetime_result(...)` and
required as `GPU_LEVEL_6_5GC_PARITY`.

## Gates

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_thirty_two_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.80s`.

```bash
gtimeout 1500s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py::test_gpu_level6_five_gc_simdgroup_thirty_two_transpose_ab_splitk_atomic_edge_tail_lifetime_real_or_skipped -rs
```

Result: `1 passed in 8.73s`.

```bash
gtimeout 1800s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_RUN_GPU_5GC_LIFETIME=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `26 passed in 58.93s`.

```bash
gtimeout 1200s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_5gc_lifetime_real.py -rs
```

Result: `26 passed in 0.21s`.

```bash
gtimeout 1200s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 26.88s`.

## Claim Boundary

This proves `GPU_LEVEL_6_5GC_PARITY` for one thirty-two-simdgroup opt-in
simdgroup runtime-source non-divisible split-K atomic f32 accumulation
edge/tail shape with combined static transposed operands. It is still
runtime-source Metal, not `.metallib`, and not whole-program GPU.

Still not proven: broader pcc1-native simdgroup/tensorcore tiling beyond this
thirty-two-simdgroup transposed shape, more-than-thirty-two-simdgroup
pcc1-native runtime workloads, arbitrary/non-f32 atomics, arbitrary split-K
expressions, `.air/.metallib` production, metallib-backed launch, performance,
or whole-program GPU execution.
