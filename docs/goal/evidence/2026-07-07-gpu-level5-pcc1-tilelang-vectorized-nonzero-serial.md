# 2026-07-07 GPU Level-5 pcc1 TileLang Vectorized Nonzero-Serial Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for a
broader imported TileLang/TIRx runtime-source shape: legal
`T.Parallel`/`T.vectorized` A/B/C tile-copy staging combined with
`T.serial(1, T.ceildiv(K, block_K))`.

The host pytest harness performs artifact preparation only:

- imports the TileLang Python DSL `matmul_vectorized_abc_copy` shape into pcc
  Kernel IR with `M=5`, `N=7`, and `K=24`;
- finalizes that Kernel IR to Metal source through the TIRx/plain-TIR path;
- builds the native MTLBuffer runtime dylib;
- builds the runtime-source command-buffer bridge dylib;
- computes the expected 5x7 C matrix with
  `execute_scalar_tiled_gemm_reference(...)`.

The pcc1-compiled no-libpython executable performs the actual runtime work:

- creates A, B, and C native MTLBuffers through
  `pcc_metal_buffer_runtime_create_prebuilt(...)`;
- writes the 5x24 A and 24x7 B matrices as little-endian f16 payload bytes,
  including negative values encoded as signed raw i32 payload chunks;
- calls `pcc_metal_source_runtime_call_prebuilt(...)` with the real generated
  TileLang/TIRx Metal source, the real runtime-source bridge dylib, and the
  three native MTLBuffer pointers;
- waits synchronously with NULL callback/context;
- reads the 5x7 f32 C matrix back and checks exact IEEE-754 bit patterns against
  the CPU oracle;
- releases all three native buffers.

Both pcc1 and the pcc1-produced probe executable are checked with `otool -L` and
do not link libpython. The test classifies the result with
`classify_pcc1_native_gpu_result(...)` and requires `GPU_LEVEL_5_PCC1_NATIVE`.

## Files

- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `docs/goal/task-board.yaml`
- `docs/current-goal-state.md`
- `codex-goal-prompt.md`

## Gates

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_vectorized_nonzero_serial -rs
```

Result: `1 passed in 2.69s`.

```bash
gtimeout 520s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `12 passed in 8.12s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  PCC_GPU_HARDWARE_STRICT=1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: `6 passed in 4.76s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one broader imported TileLang/TIRx
runtime-source shape: `T.Parallel`/`T.vectorized` tile-copy staging plus a
nonzero serial K-loop range. It is still runtime-source Metal, not `.metallib`.

Still not proven: pcc1/five-GC parity for arbitrary broader TileLang/TIRx
passes, broader vectorized loop bodies beyond the legal tile-copy staging shape,
arbitrary nested loop forms, arbitrary split-K expressions, external framework
DLPack/stream interop, `.air/.metallib` production, metallib-backed launch,
performance, or whole-program GPU execution.
