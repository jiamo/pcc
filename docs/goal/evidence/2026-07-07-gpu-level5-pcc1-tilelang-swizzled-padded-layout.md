# 2026-07-07 GPU Level-5 pcc1 TileLang Swizzled Padded Layout Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for the
imported TileLang/TIRx `T.annotate_layout({A_shared:
tilelang.layout.make_swizzled_layout(A_shared), ...})` runtime-source shape.

The generated Metal source is required to preserve the shared-memory layout
indexing:

- `uint a_shared_idx = ((a_local_m * 8u) + a_local_k);`
- `uint b_shared_idx = ((b_local_k * 8u) + b_local_n);`
- `A_shared[a_shared_idx]`
- `B_shared[b_shared_idx]`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes odd-sized f16 payloads for A(5,3) and B(3,7) byte-by-byte, launches the
generated Metal source through `pcc_metal_source_runtime_call_prebuilt(...)`,
waits synchronously, reads C back, checks exact f32 bits against
`execute_scalar_tiled_gemm_reference(...)`, and releases native buffers after
launch.

## Gates

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_swizzled_padded_annotate_layout_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.01s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_swizzled_padded_annotate_layout -rs
```

Result: `1 passed in 2.33s`.

```bash
gtimeout 660s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `16 passed in 14.68s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one imported TileLang/TIRx
runtime-source shared-layout shape using `tilelang.layout.make_swizzled_layout`
on A/B shared buffers with static `M=5,N=7,K=3,block_M=8,block_N=8,block_K=8`.
It is still runtime-source Metal, not `.metallib`.

Still not proven: arbitrary `T.annotate_layout` functions, TMA/wgmma layouts,
dynamic shapes, arbitrary TileLang/TIRx forms, external framework
DLPack/stream interop, `.air/.metallib` production, metallib-backed launch,
performance, or whole-program GPU execution.
