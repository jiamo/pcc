# 2026-07-08 GPU Level-5 pcc1 TileLang T.Pipelined Step Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for
the documented stepped `T.Pipelined(start, end, step, ...)` K-loop form in the
current strict scalar tiled GEMM subset.

The covered shape is `M=5,N=7,K=32`, `block_M=8,block_N=8,block_K=8`, with:

- TileLang loop: `T.Pipelined(0, T.ceildiv(K, block_K), 2, num_stages=0)`
- imported attrs: `pipeline_extent=4`, `pipeline_step=2`, `num_stages=0`
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
  tests/kernel/test_tilelang_import_broader.py::test_step_pipelined_range_survives_import_freeze_source_and_cpu_oracle \
  tests/kernel/test_tilelang_import_broader.py::test_nonzero_start_serial_and_pipelined_bad_ranges_fail_closed -rs
```

Result: `2 passed in 0.12s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_step_pipelined_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.91s`.

```bash
gtimeout 1200s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_step_pipelined -rs
```

Result: `1 passed in 1.96s`.

```bash
gtimeout 1800s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `32 passed in 58.81s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs
```

Result: `38 passed in 0.09s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
```

Result: `20 passed in 12.83s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one static stepped
`T.Pipelined(start, end, step, num_stages=0)` scalar tiled GEMM runtime-source
Metal shape. It is not `.air/.metallib` production, not metallib-backed launch,
not arbitrary dynamic loop bounds, not arbitrary stepped `T.Pipelined` forms
beyond this static K-loop shape, not arbitrary nested loop lowering, not
performance evidence, and not whole-program GPU execution.
