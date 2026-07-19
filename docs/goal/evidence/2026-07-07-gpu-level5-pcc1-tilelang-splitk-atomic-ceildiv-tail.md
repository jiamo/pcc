# 2026-07-07 GPU Level-5 pcc1 TileLang Split-K Atomic Ceildiv-Tail Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for the
imported TileLang/TIRx non-divisible split-K atomic runtime-source shape:
`K=17`, `split_k=4`, and the split span is computed with
`T.ceildiv(K, split_k)`.

The generated Metal source is required to contain:

- `device atomic_float* C [[buffer(2)]]`
- `uint split_k0 = split_k_index * 5u;`
- `uint split_k_end = min(split_k0 + 5u, 17u);`

The pcc1-compiled no-libpython executable creates real A/B/C native MTLBuffers,
writes odd-sized f16 payloads for 5x17 A and 17x7 B byte-by-byte, explicitly
zeros the 5x7 f32 C buffer before launch, launches the generated Metal source
through `pcc_metal_source_runtime_call_prebuilt(...)`, waits synchronously,
reads C back, checks exact f32 bits against `execute_scalar_tiled_gemm_reference(...)`,
and releases native buffers after the launch.

## Gates

```bash
gtimeout 180s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_splitk_atomic_ceildiv_tail_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.28s`.

```bash
gtimeout 420s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_splitk_atomic_ceildiv_tail -rs
```

Result: `1 passed in 2.55s`.

```bash
gtimeout 540s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `14 passed in 11.30s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one non-divisible imported
TileLang/TIRx runtime-source split-K atomic shape: `K=17`, `split_k=4`, f16/f16
inputs, f32 output, and f32 atomic accumulation. It is still runtime-source
Metal, not `.metallib`.

Still not proven: arbitrary/non-f32 atomics, arbitrary split-K index
expressions, arbitrary dynamic TileLang/TIRx forms, external framework
DLPack/stream interop, `.air/.metallib` production, metallib-backed launch,
performance, or whole-program GPU execution.
