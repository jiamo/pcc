# 2026-07-08 GPU Level-5 pcc1 Simdgroup Eight-N Transpose_AB Split-K Atomic Edge/Tail Evidence

## Summary

`GPU-P0-SIMDGROUP-TENSORCORE-GEMM` now has pcc1-native Level-5 proof for the
first eight-simdgroup opt-in Metal simdgroup GEMM tile that combines
`transpose_A`, `transpose_B`, split-K atomic output, M/N edge tiles, and a K
tail: `M=15,N=31,K=17`, `block_M=16,block_N=32,block_K=8`, `split_k=4`,
`split_k_span_mode=ceildiv`, and `threads=256`.

The generated Metal source is required to use eight simdgroups per threadgroup,
a 3D split-K grid, ceildiv split-K partitioning, per-simdgroup A/B/C staging,
transposed global storage, M/N writeback guards, and f32 atomic accumulation:

- `device atomic_float* C [[buffer(2)]]`
- `uint3 tgid [[threadgroup_position_in_grid]]`
- `uint simdgroup_lane [[thread_index_in_simdgroup]]`
- `threadgroup half A_tile[512];`
- `threadgroup half B_tile[512];`
- `threadgroup float C_tile[512];`
- `uint split_k0 = split_k_index * 5u;`
- `uint split_k_end = min(split_k0 + 5u, 17u);`
- `uint simdgroup_tile_m = simdgroup_idx / 4u;`
- `uint simdgroup_tile_n = simdgroup_idx % 4u;`
- `A[(global_k * 15u) + global_m]` guarded by `global_k < split_k_end`
- `B[(global_n * 17u) + global_k]` guarded by `global_n < 31u`
- `simdgroup_load(..., A_tile + simdgroup_tile_offset, 8u, 0, false)`
- `simdgroup_load(..., B_tile + simdgroup_tile_offset, 8u, 0, false)`
- `if (row < 15u && col < 31u) {`
- `atomic_fetch_add_explicit(...)`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes A(K,M)=A(17,15) and B(N,K)=B(31,17) f16 payloads byte-by-byte,
explicitly zeroes C before launch, launches the generated simdgroup Metal
source through `pcc_metal_source_runtime_call_prebuilt(...)`, waits
synchronously, reads C(15,31) back, checks exact f32 bits against
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_transposed_splitk_atomic_edge_tail_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.91s`.

```bash
gtimeout 620s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_simdgroup_eight_n_transpose_ab_splitk_atomic_edge_tail_gemm -rs
```

Result: `1 passed in 4.22s`.

```bash
gtimeout 1080s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `28 passed in 42.15s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 25.37s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one eight-simdgroup opt-in simdgroup
runtime-source non-divisible split-K atomic f32 accumulation edge/tail shape
with combined static transposed operands. It is still runtime-source Metal, not
`.metallib`, and not whole-program GPU.

Still not proven: broader pcc1-native simdgroup/tensorcore tiling beyond this
eight-simdgroup transposed shape, sixteen/thirty-two-simdgroup pcc1-native
runtime workloads, arbitrary/non-f32 atomics, arbitrary split-K expressions,
`.air/.metallib` production, metallib-backed launch, performance, or
whole-program GPU execution.
