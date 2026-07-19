# 2026-07-07 GPU Level-5 pcc1 TileLang Enabled Swizzle Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for the
imported TileLang/TIRx enabled `T.use_swizzle(panel_size=2, enable=True)`
runtime-source shape on a non-trivial 3x3 tile grid.

The generated Metal source is required to contain the threadblock
rasterization logic:

- `uint swizzle_grid_x = 3u;`
- `uint swizzle_grid_y = 3u;`
- `uint swizzle_panel_size = 2u * swizzle_grid_x;`
- `uint tile_col0 = tile_gid_x * 8u;`
- `uint tile_row0 = tile_gid_y * 8u;`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes f16 payloads for A(17,16) and B(16,19), launches the generated Metal
source through `pcc_metal_source_runtime_call_prebuilt(...)`, waits
synchronously, reads the 17x19 C matrix back, checks exact f32 bits against
`execute_scalar_tiled_gemm_reference(...)`, and releases native buffers after
launch.

## Gates

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_enabled_swizzle_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.99s`.

```bash
gtimeout 480s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_enabled_swizzle -rs
```

Result: `1 passed in 2.91s`.

```bash
gtimeout 720s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `17 passed in 17.71s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one imported TileLang/TIRx
runtime-source enabled-swizzle shape with static
`M=17,N=19,K=16,block_M=8,block_N=8,block_K=8,panel_size=2`. It is still
runtime-source Metal, not `.metallib`.

Still not proven: arbitrary/cluster-aware swizzle placement, arbitrary
`T.use_swizzle` expressions, dynamic shapes, arbitrary TileLang/TIRx forms,
external framework DLPack/stream interop, `.air/.metallib` production,
metallib-backed launch, performance, or whole-program GPU execution.
