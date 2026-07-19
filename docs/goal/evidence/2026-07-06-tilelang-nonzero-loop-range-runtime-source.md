# TileLang Nonzero Loop Range Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- `T.serial(start, end)` and `T.Pipelined(start, end, ...)` now preserve
  start/end K-loop metadata for the current strict scalar tiled GEMM subset.
- The importer rejects negative or boolean starts and empty/reversed ranges
  instead of silently normalizing them.
- The CPU oracle executes only the selected K-tile range, so a nonzero start is
  observable in numeric output rather than just metadata.
- Metal scalar GEMM source emits the same selected K-tile range, for example
  `for (uint ko = 1u; ko < 3u; ++ko)`.
- Runtime-source Metal execution covers a nonzero-start pipelined GEMM variant
  through command-buffer submit, fence completion, readback, and CPU-oracle
  comparison.

## Reference Notes

This is still a bounded TileLang/TIRx slice. It covers serial/pipelined K-loop
range metadata for the current copy/gemm scalar-GEMM body shape only. It does
not implement arbitrary executable loop bodies, split-k atomic accumulation,
dynamic loop bounds, arbitrary nested loop forms, or TileLang/TVM pass
execution.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_nonzero_start_serial_range_survives_import_freeze_source_and_cpu_oracle \
  tests/kernel/test_tilelang_import_broader.py::test_nonzero_start_pipelined_range_survives_import_freeze_source_and_cpu_oracle \
  tests/kernel/test_tilelang_import_broader.py::test_nonzero_start_serial_and_pipelined_bad_ranges_fail_closed
```

Result: 3 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_nonzero_start_pipelined_runtime_source_matches_cpu_oracle
```

Result: 1 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 29 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 14 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 56 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 21 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 420s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 229 passed.

## Claim Boundary

This proves nonzero serial/pipelined K-loop range import, TIRx/plain-TIR
metadata preservation, CPU-oracle execution, Metal source emission, and
runtime-source Metal readback for the current scalar tiled GEMM subset. It
does not prove arbitrary executable loop bodies, real split-k atomic
accumulation, dynamic loop bounds, arbitrary nested/multi-argument loop forms,
enabled `T.use_swizzle` rasterization, arbitrary `T.annotate_layout` functions,
TMA/wgmma descriptor lowering, performance, `.air/.metallib` production,
pcc1-native GPU launch, five-GC GPU lifetime parity, or whole-program GPU
execution.
