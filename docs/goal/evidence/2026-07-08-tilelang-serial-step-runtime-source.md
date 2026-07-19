# 2026-07-08 TileLang T.serial Step Runtime-Source Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now covers the documented
`T.serial(start, end, step)` loop form for the current strict scalar tiled GEMM
subset. The importer preserves `serial_step` metadata, TIRx/plain-TIR freeze
keeps that metadata, the CPU oracle executes only the stepped K-tile sequence,
and Metal source emits the matching stepped `ko` loop.

The covered runtime-source shape is `M=5,N=7,K=32`,
`block_M=8,block_N=8,block_K=8`, with:

- TileLang loop: `T.serial(0, T.ceildiv(K, block_K), 2)`
- imported attrs: `serial_extent=4`, `serial_step=2`
- CPU K tiles: `ko = 0, 2`
- Metal loop: `for (uint ko = 0u; ko < 4u; ko += 2u)`
- runtime-source Metal launch with fence completion and CPU-oracle readback

Invalid stepped ranges fail closed: `step=0` and boolean `step=True` are
rejected by the importer.

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_step_serial_range_survives_import_freeze_source_and_cpu_oracle \
  tests/kernel/test_tilelang_import_broader.py::test_nonzero_start_serial_and_pipelined_bad_ranges_fail_closed -rs
```

Result: `3 passed in 0.29s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_step_serial_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.97s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs
```

Result: `37 passed in 0.09s`.

```bash
gtimeout 900s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
```

Result: `19 passed in 12.77s`.

```bash
gtimeout 600s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py -rs
```

Result: `38 passed in 0.14s`.

```bash
gtimeout 1200s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_simdgroup_gemm.py -rs
```

Result: `80 passed in 25.72s`.

## Claim Boundary

This proves runtime-source Metal execution for one static stepped
`T.serial(start, end, step)` scalar tiled GEMM loop shape. It is not
`.air/.metallib` production, not pcc1-native execution, not five-GC GPU
lifetime parity, not arbitrary dynamic loop bounds, not arbitrary stepped
`T.Pipelined`, and not whole-program GPU execution.
