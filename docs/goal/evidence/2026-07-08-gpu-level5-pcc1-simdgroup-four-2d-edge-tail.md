# 2026-07-08 GPU Level-5 pcc1 Simdgroup Four-2D Edge/Tail Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has pcc1-native Level-5 proof for the
first 2D opt-in Metal simdgroup GEMM edge/tail tile:
`M=15,N=15,K=9`, `block_M=16,block_N=16,block_K=8`, and `threads=128`.

This slice is intentionally not a clean multiple of the simdgroup tile shape.
The generated Metal source is required to use per-simdgroup staging and bounds
guards:

- `threadgroup half A_tile[256];`
- `threadgroup half B_tile[256];`
- `threadgroup float C_tile[256];`
- `uint simdgroup_tile_offset = simdgroup_idx * 64u;`
- A staging guard `global_m < 15u && global_k < 9u`
- B staging guard `global_k < 9u && global_n < 15u`
- C writeback guard `row < 15u && col < 15u`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes odd-sized f16 payloads for A(15,9) and B(9,15) byte-by-byte, launches
the generated simdgroup Metal source through
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_edge_tail_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.00s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 26.32s`.

```bash
gtimeout 480s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_four_2d_edge_tail_gemm -rs
```

Result: `1 passed in 3.61s`.

```bash
gtimeout 820s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `23 passed in 29.29s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one 2D opt-in simdgroup
runtime-source edge/tail shape with four 8x8 simdgroups covering M and N,
K-tail staging, M/N writeback guards, and odd-sized f16 payload writes. It is
still runtime-source Metal, not `.metallib`.

Still not proven: arbitrary larger simdgroup/tensorcore tiling, more-than-four
pcc1-native simdgroup tiles, transposed edge/tail pcc1-native simdgroup
variants, split-K pcc1-native simdgroup variants, arbitrary/non-f32 atomics,
arbitrary split-K expressions, arbitrary dynamic TileLang/TIRx forms,
`.air/.metallib` production, metallib-backed launch, performance, or
whole-program GPU execution.
