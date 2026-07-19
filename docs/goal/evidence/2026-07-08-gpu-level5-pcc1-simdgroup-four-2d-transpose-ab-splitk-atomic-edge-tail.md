# 2026-07-08 GPU Level-5 pcc1 Simdgroup Four-2D Transpose_AB Split-K Atomic Edge/Tail Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has pcc1-native Level-5 proof for the
first non-divisible 2D opt-in Metal simdgroup GEMM tile that combines
`transpose_A`, `transpose_B`, split-K atomic output, M/N edge tiles, and a K
tail: `M=15,N=15,K=17`, `block_M=16,block_N=16,block_K=8`, `split_k=4`,
`split_k_span_mode=ceildiv`, and `threads=128`.

The generated Metal source is required to use a 3D grid, ceildiv split-K
partitioning, per-simdgroup A/B/C staging, transposed global storage,
M/N writeback guards, and f32 atomic accumulation:

- `device atomic_float* C [[buffer(2)]]`
- `uint3 tgid [[threadgroup_position_in_grid]]`
- `uint simdgroup_lane [[thread_index_in_simdgroup]]`
- `threadgroup half A_tile[256];`
- `threadgroup half B_tile[256];`
- `threadgroup float C_tile[256];`
- `uint split_k0 = split_k_index * 5u;`
- `uint split_k_end = min(split_k0 + 5u, 17u);`
- `A[(global_k * 15u) + global_m]` guarded by `global_k < split_k_end`
- `B[(global_n * 17u) + global_k]` guarded by `global_n < 15u`
- `simdgroup_load(..., A_tile + simdgroup_tile_offset, 8u, 0, false)`
- `simdgroup_load(..., B_tile + simdgroup_tile_offset, 8u, 0, false)`
- `if (row < 15u && col < 15u) {`
- `atomic_fetch_add_explicit(...)`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes odd-sized transposed-layout A(K,M)=A(17,15) and B(N,K)=B(15,17) f16
payloads byte-by-byte, explicitly zeroes C before launch, launches the generated
simdgroup Metal source through `pcc_metal_source_runtime_call_prebuilt(...)`,
waits synchronously, reads C(15,15) back, checks exact f32 bits against
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.99s`.

```bash
gtimeout 520s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_four_2d_transpose_ab_splitk_atomic_edge_tail_gemm -rs
```

Result: `1 passed in 3.40s`.

```bash
gtimeout 960s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `27 passed in 40.01s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 26.68s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one 2D opt-in simdgroup
runtime-source non-divisible split-K atomic f32 accumulation edge/tail shape
with combined static transposed operands. It is still runtime-source Metal, not
`.metallib`, and not whole-program GPU.

Still not proven: broader non-divisible/tail split-K simdgroup variants beyond
this four-simdgroup transposed shape, arbitrary larger simdgroup/tensorcore
tiling, more-than-four pcc1-native simdgroup tiles, arbitrary/non-f32 atomics,
arbitrary split-K expressions, `.air/.metallib` production, metallib-backed
launch, performance, or whole-program GPU execution.
