# 2026-07-07 GPU Level-5 pcc1 TileLang Nonzero Pipelined Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for the
imported TileLang/TIRx nonzero-start `T.Pipelined(1, T.ceildiv(K, block_K),
num_stages=0)` runtime-source shape.

The generated Metal source is required to contain:

- `for (uint ko = 1u; ko < 3u; ++ko)`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes f16 payloads for A(5,24) and B(24,7), launches the generated Metal
source through `pcc_metal_source_runtime_call_prebuilt(...)`, waits
synchronously, reads C back, checks exact f32 bits against
`execute_scalar_tiled_gemm_reference(...)`, and releases native buffers after
launch.

## Gates

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_nonzero_start_pipelined_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.09s`.

```bash
gtimeout 480s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_nonzero_start_pipelined -rs
```

Result: `1 passed in 2.49s`.

```bash
gtimeout 760s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `18 passed in 19.70s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one imported TileLang/TIRx
runtime-source nonzero-start pipelined range with static
`M=5,N=7,K=24,block_M=8,block_N=8,block_K=8`. It is still runtime-source Metal,
not `.metallib`.

Still not proven: arbitrary nested/multi-argument loop forms, arbitrary dynamic
TileLang/TIRx forms, external framework DLPack/stream interop,
`.air/.metallib` production, metallib-backed launch, performance, or
whole-program GPU execution.
