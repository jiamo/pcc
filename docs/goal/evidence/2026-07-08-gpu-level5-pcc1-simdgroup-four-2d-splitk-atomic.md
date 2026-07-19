# 2026-07-08 GPU Level-5 pcc1 Simdgroup Four-2D Split-K Atomic Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has pcc1-native Level-5 proof for the
first 2D opt-in Metal simdgroup GEMM split-K atomic tile:
`M=16,N=16,K=16`, `block_M=16,block_N=16,block_K=8`, `split_k=2`, and
`threads=128`.

The generated Metal source is required to use a 3D grid, split-K partitioning,
per-simdgroup C staging, and f32 atomic accumulation:

- `device atomic_float* C [[buffer(2)]]`
- `uint3 tgid [[threadgroup_position_in_grid]]`
- `uint simdgroup_idx [[simdgroup_index_in_threadgroup]]`
- `uint simdgroup_lane [[thread_index_in_simdgroup]]`
- `threadgroup float C_tile[256];`
- `uint split_k_index = tgid.z;`
- `uint split_k0 = split_k_index * 8u;`
- `atomic_fetch_add_explicit(...)`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes A(16,16) and B(16,16) f16 payloads, explicitly zeroes C before launch,
launches the generated simdgroup Metal source through
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.10s`.

```bash
gtimeout 520s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_four_2d_splitk_atomic_gemm -rs
```

Result: `1 passed in 2.90s`.

```bash
gtimeout 960s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `25 passed in 35.23s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 27.34s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one 2D opt-in simdgroup
runtime-source split-K atomic f32 accumulation shape. It is still
runtime-source Metal, not `.metallib`, and not whole-program GPU.

Still not proven: non-divisible/tail split-K simdgroup variants,
transpose-plus-split-K simdgroup variants, arbitrary larger simdgroup/tensorcore
tiling, more-than-four pcc1-native simdgroup tiles, arbitrary/non-f32 atomics,
arbitrary split-K expressions, `.air/.metallib` production, metallib-backed
launch, performance, or whole-program GPU execution.
