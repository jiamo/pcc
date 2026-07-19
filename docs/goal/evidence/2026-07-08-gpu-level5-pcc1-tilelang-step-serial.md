# 2026-07-08 GPU Level-5 pcc1 TileLang T.serial Step Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for
the documented stepped `T.serial(start, end, step)` K-loop form in the current
strict scalar tiled GEMM subset.

The covered shape is `M=5,N=7,K=32`, `block_M=8,block_N=8,block_K=8`, with:

- TileLang loop: `T.serial(0, T.ceildiv(K, block_K), 2)`
- imported attrs: `serial_extent=4`, `serial_step=2`
- CPU K tiles: `ko = 0, 2`
- Metal loop: `for (uint ko = 0u; ko < 4u; ko += 2u)`

The pcc1-compiled no-libpython executable creates real A/B/C native
MTLBuffers, writes A(5,32) and B(32,7) f16 payloads byte-by-byte, launches the
generated scalar tiled GEMM Metal source through
`pcc_metal_source_runtime_call_prebuilt(...)`, waits synchronously, reads
C(5,7) f32 output back, checks the CPU oracle, and releases native buffers
after launch.

The test classifies the result as `GPU_LEVEL_5_PCC1_NATIVE`.

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Result: passed.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_step_serial_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 1.11s`.

```bash
gtimeout 1200s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_step_serial -rs
```

Result: `1 passed in 2.48s`.

```bash
gtimeout 1800s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `31 passed in 56.89s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
```

Result: `19 passed in 12.36s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs
```

Result: `37 passed in 0.09s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one static stepped
`T.serial(start, end, step)` scalar tiled GEMM runtime-source Metal shape. It
is not `.air/.metallib` production, not metallib-backed launch, not arbitrary
dynamic loop bounds, not arbitrary stepped `T.Pipelined`, not arbitrary nested
loop lowering, not performance evidence, and not whole-program GPU execution.
