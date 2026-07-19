# 2026-07-07 GPU Level-5 pcc1 Simdgroup Two-M Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has pcc1-native Level-5 proof for the
two-M opt-in Metal simdgroup GEMM tile: `M=16,N=8,K=8` with
`block_M=16,block_N=8,block_K=8` and `threads=64`.

The generated Metal source is required to contain:

- `uint simdgroup_idx [[simdgroup_index_in_threadgroup]]`
- `uint simdgroup_tile_m = simdgroup_idx / 1u;`
- `uint simdgroup_tile_n = simdgroup_idx % 1u;`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes f16 payloads for A(16,8) and B(8,8), launches the generated simdgroup
Metal source through `pcc_metal_source_runtime_call_prebuilt(...)`, waits
synchronously, reads C(16,8) back, checks exact f32 bits against
`execute_scalar_tiled_gemm_reference(...)`, and releases native buffers after
launch.

## Gates

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_two_m_tiles_per_threadgroup_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.52s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 25.76s`.

```bash
gtimeout 480s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_two_m_gemm -rs
```

Result: `1 passed in 2.40s`.

```bash
gtimeout 760s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `20 passed in 21.71s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one broader opt-in simdgroup
runtime-source shape with two 8x8 simdgroups covering the M axis. It is still
runtime-source Metal, not `.metallib`.

Still not proven: arbitrary larger simdgroup/tensorcore tiling, 2D multi-tile
pcc1-native simdgroup shapes, more-than-two pcc1-native simdgroup tiles,
arbitrary/non-f32 atomics, arbitrary split-K expressions, arbitrary dynamic
TileLang/TIRx forms, `.air/.metallib` production, metallib-backed launch,
performance, or whole-program GPU execution.
