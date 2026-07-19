# TileLang Zero-Start Loop Range Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- `import_tilelang_source(...)` now accepts zero-start two-argument loop ranges
  for the supported GEMM body subset:
  - `T.serial(0, extent)` is normalized to the same `serial_extent` metadata as
    `T.serial(extent)`.
  - `T.Pipelined(0, extent, num_stages=...)` is normalized to the same
    `pipeline_extent` metadata as `T.Pipelined(extent, num_stages=...)`.
- Non-zero starts still fail closed. Current Kernel IR/CPU oracle/Metal source
  lowering has no loop-offset representation, so accepting `T.serial(1, ...)`
  or `T.Pipelined(1, ...)` would be an overclaim.
- Runtime-source Metal execution now covers a zero-start two-argument
  `T.Pipelined(0, T.ceildiv(K, block_K), num_stages=0)` imported GEMM and
  compares device readback against the CPU oracle.

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 15 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 6 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 37 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 13 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 199 passed.

```bash
gtimeout 120s env -u LC_ALL uv run python scripts/goal_state.py validate
gtimeout 120s git diff --check
```

Result: goal state validated 22 tasks; diff check clean.

## Claim Boundary

This proves only zero-start two-argument loop syntax for the current strict
scalar tiled GEMM subset. It is not general loop-range lowering, not non-zero
loop offsets, not arbitrary nested `T.serial`, not general executable
`T.Parallel` / `T.vectorized`, not TileLang/TVM pass execution, not
`.air/.metallib` production, and not whole-program GPU execution.
