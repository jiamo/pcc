# 2026-07-07 GPU Level-5 pcc1 TileLang Split-K Atomic Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for
the imported TileLang/TIRx split-K atomic runtime-source shape:
`T.Kernel(..., split_k)` produces a 3D Metal grid, `tgid.z` selects the split-K
partition, and `T.atomic_add(C, C_local)` lowers to f32 atomic output
accumulation.

The host pytest harness performs artifact preparation only:

- imports `matmul_splitk_atomic` with `M=5`, `N=7`, `K=16`, `split_k=2`;
- finalizes that Kernel IR to runtime-source Metal;
- asserts the generated Metal source contains `device atomic_float* C`,
  `uint split_k_index = tgid.z;`, and `atomic_fetch_add_explicit(...)`;
- builds the native MTLBuffer runtime dylib and runtime-source bridge dylib;
- computes the expected 5x7 C matrix with
  `execute_scalar_tiled_gemm_reference(...)`.

The pcc1-compiled no-libpython executable performs the actual runtime work:

- creates A, B, and C native MTLBuffers through
  `pcc_metal_buffer_runtime_create_prebuilt(...)`;
- writes 5x16 A and 16x7 B as little-endian f16 payload bytes, including
  negative values encoded as signed raw i32 payload chunks;
- explicitly writes a zeroed 5x7 f32 C payload before launch, because the
  kernel accumulates with atomic add;
- calls `pcc_metal_source_runtime_call_prebuilt(...)` with the generated
  TileLang/TIRx Metal source and real runtime-source bridge dylib;
- waits synchronously with NULL callback/context;
- reads the 5x7 f32 C matrix back and checks exact IEEE-754 bit patterns
  against the CPU oracle;
- releases all three native buffers.

Both pcc1 and the pcc1-produced probe executable are checked with `otool -L` and
do not link libpython. The test classifies the result with
`classify_pcc1_native_gpu_result(...)` and requires `GPU_LEVEL_5_PCC1_NATIVE`.

## Files

- `tests/gpu_hardware/test_metal_pcc1_launch_real.py`
- `tests/gpu_hardware/test_metal_5gc_lifetime_real.py`
- `docs/goal/task-board.yaml`
- `docs/current-goal-state.md`
- `codex-goal-prompt.md`

## Gates

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_splitk_atomic_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.18s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_splitk_atomic -rs
```

Result: `1 passed in 2.46s`.

```bash
gtimeout 520s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `13 passed in 10.01s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one imported TileLang/TIRx
runtime-source split-K atomic shape: divisible `K=16`, `split_k=2`, f16/f16
inputs, f32 output, and f32 atomic accumulation. It is still runtime-source
Metal, not `.metallib`.

Still not proven: pcc1/five-GC parity for arbitrary broader TileLang/TIRx
passes, arbitrary/non-f32 atomics, arbitrary split-K index expressions,
non-divisible split-K under pcc1, external framework DLPack/stream interop,
`.air/.metallib` production, metallib-backed launch, performance, or
whole-program GPU execution.
