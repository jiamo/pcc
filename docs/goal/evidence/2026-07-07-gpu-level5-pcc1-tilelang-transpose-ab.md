# 2026-07-07 GPU Level-5 pcc1 TileLang Transpose A+B Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for the
imported TileLang/TIRx `T.gemm(..., True, True)` runtime-source shape.

The generated Metal source is required to use transpose-aware shared-memory
indexes:

- `A_shared[((kk * 8u) + local_m)]`
- `B_shared[((local_n * 8u) + kk)]`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes odd-sized f16 payloads for A(K,M)=3x5 and B(N,K)=7x3 byte-by-byte,
launches the generated Metal source through
`pcc_metal_source_runtime_call_prebuilt(...)`, waits synchronously, reads C
back, checks exact f32 bits against `execute_scalar_tiled_gemm_reference(...)`,
and releases native buffers after launch.

## Gates

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_transpose_ab_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.05s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_transpose_ab -rs
```

Result: `1 passed in 2.74s`.

```bash
gtimeout 600s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `15 passed in 13.29s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one imported TileLang/TIRx
runtime-source transpose shape: `T.gemm(A_shared, B_shared, C_local, True,
True)` with static `M=5,N=7,K=3`. It is still runtime-source Metal, not
`.metallib`.

Still not proven: arbitrary transposed layouts, dynamic shapes, arbitrary
TileLang/TIRx forms, external framework DLPack/stream interop, `.air/.metallib`
production, metallib-backed launch, performance, or whole-program GPU
execution.
