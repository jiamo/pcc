# 2026-07-08 GPU Level-5 pcc1 TileLang Nonzero T.serial Step Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for a
combined nonzero-start plus stepped `T.serial(start, end, step)` K-loop form in
the current strict scalar tiled GEMM subset.

The covered shape is `M=5,N=7,K=40`, `block_M=8,block_N=8,block_K=8`, with:

- TileLang loop: `T.serial(1, T.ceildiv(K, block_K), 2)`
- imported attrs: `serial_start=1`, `serial_extent=4`, `serial_step=2`
- CPU K tiles: `ko = 1, 3`
- Metal loop: `for (uint ko = 1u; ko < 5u; ko += 2u)`

The pcc1-compiled no-libpython executable creates real A/B/C native
MTLBuffers, writes A(5,40) and B(40,7) f16 payloads byte-by-byte, launches the
generated scalar tiled GEMM Metal source through
`pcc_metal_source_runtime_call_prebuilt(...)`, waits synchronously, reads
C(5,7) f32 output back, checks the CPU oracle, and releases native buffers
after launch.

The test classifies the result as `GPU_LEVEL_5_PCC1_NATIVE`.

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_nonzero_step_serial_range_survives_import_freeze_source_and_cpu_oracle -rs
```

Result: `1 passed in 0.12s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_nonzero_step_serial_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.85s`.

```bash
gtimeout 1200s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_nonzero_step_serial -rs
```

Result: `1 passed in 2.20s`.

```bash
gtimeout 1800s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `34 passed in 63.68s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs
```

Result: `40 passed in 0.09s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
```

Result: `22 passed in 14.83s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one static nonzero-start plus stepped
`T.serial(start, end, step)` scalar tiled GEMM runtime-source Metal shape. It
is not `.air/.metallib` production, not metallib-backed launch, not arbitrary
dynamic loop bounds, not arbitrary stepped `T.serial` forms beyond this static
K-loop shape, not arbitrary nested loop lowering, not performance evidence, and
not whole-program GPU execution.
