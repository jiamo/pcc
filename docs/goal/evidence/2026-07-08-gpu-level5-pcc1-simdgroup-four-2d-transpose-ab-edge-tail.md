# 2026-07-08 GPU Level-5 pcc1 Simdgroup Four-2D Transpose-AB Edge/Tail Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has pcc1-native Level-5 proof for the
first 2D opt-in Metal simdgroup GEMM edge/tail tile with transposed operands:
`M=15,N=15,K=9`, `block_M=16,block_N=16,block_K=8`, `threads=128`,
`transpose_A=True`, and `transpose_B=True`.

This slice combines the previous transpose_AB and edge/tail boundaries. The
generated Metal source is required to use per-simdgroup staging and bounds
guards:

- `threadgroup half A_tile[256];`
- `threadgroup half B_tile[256];`
- `threadgroup float C_tile[256];`
- `uint simdgroup_tile_offset = simdgroup_idx * 64u;`
- transpose-aware A staging `A[(global_k * 15u) + global_m]`
- transpose-aware B staging `B[(global_n * 9u) + global_k]`
- C writeback guard `row < 15u && col < 15u`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes odd-sized f16 payloads for A(K,M) = A(9,15) and B(N,K) = B(15,9)
byte-by-byte, launches the generated simdgroup Metal source through
`pcc_metal_source_runtime_call_prebuilt(...)`, waits synchronously, reads
C(15,15) back, checks exact f32 bits against
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_transposed_edge_tail_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.91s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 25.96s`.

```bash
gtimeout 520s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_four_2d_transpose_ab_edge_tail_gemm -rs
```

Result: `1 passed in 2.69s`.

```bash
gtimeout 900s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `24 passed in 31.25s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one 2D opt-in simdgroup
runtime-source edge/tail shape with transposed operands, four 8x8 simdgroups
covering M and N, K-tail staging, M/N writeback guards, and odd-sized f16
payload writes. It is still runtime-source Metal, not `.metallib`.

Still not proven: arbitrary larger simdgroup/tensorcore tiling, more-than-four
pcc1-native simdgroup tiles, split-K pcc1-native simdgroup variants,
arbitrary/non-f32 atomics, arbitrary split-K expressions, arbitrary dynamic
TileLang/TIRx forms, `.air/.metallib` production, metallib-backed launch,
performance, or whole-program GPU execution.
