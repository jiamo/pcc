# 2026-07-07 GPU Level-5 pcc1 Simdgroup Four-2D Transpose-AB Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has pcc1-native Level-5 proof for a
2D opt-in Metal simdgroup GEMM tile with transposed operands:
`M=16,N=16,K=8`, `block_M=16,block_N=16,block_K=8`, `threads=128`,
`transpose_A=True`, and `transpose_B=True`.

The generated Metal source is required to contain:

- `uint simdgroup_idx [[simdgroup_index_in_threadgroup]]`
- `uint simdgroup_tile_m = simdgroup_idx / 2u;`
- `uint simdgroup_tile_n = simdgroup_idx % 2u;`
- transpose-aware A load with row stride `16u` and transpose flag `true`
- transpose-aware B load with row stride `8u` and transpose flag `true`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes f16 payloads for A(K,M) = A(8,16) and B(N,K) = B(16,8), launches the
generated simdgroup Metal source through
`pcc_metal_source_runtime_call_prebuilt(...)`, waits synchronously, reads
C(16,16) back, checks exact f32 bits against
`execute_scalar_tiled_gemm_reference(...)`, and releases native buffers after
launch.

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_with_transposed_operands_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.39s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 24.64s`.

```bash
gtimeout 480s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_four_2d_transpose_ab_gemm -rs
```

Result: `1 passed in 2.66s`.

```bash
gtimeout 760s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `22 passed in 25.63s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one 2D opt-in simdgroup
runtime-source shape with four 8x8 simdgroups covering M and N and both
operands read through transposed layout semantics. It is still runtime-source
Metal, not `.metallib`.

Still not proven: arbitrary larger simdgroup/tensorcore tiling, more-than-four
pcc1-native simdgroup tiles, edge/tail/split-K pcc1-native simdgroup variants,
arbitrary/non-f32 atomics, arbitrary split-K expressions, arbitrary dynamic
TileLang/TIRx forms, `.air/.metallib` production, metallib-backed launch,
performance, or whole-program GPU execution.
