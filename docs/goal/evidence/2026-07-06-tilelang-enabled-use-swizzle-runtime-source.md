# TileLang Enabled Use-Swizzle Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- `T.use_swizzle(..., enable=True)` now imports as enabled Kernel IR swizzle
  metadata for the current scalar GEMM subset. Missing `order` defaults to
  `"row"` to match TileLang.
- The importer accepts TileLang's row and column rasterization orders and
  rejects unsupported orders.
- CPU reference validates enabled swizzle metadata as a threadblock tile
  permutation. The numeric oracle is unchanged because GEMM tile execution
  order does not affect the output.
- Metal scalar GEMM source now emits the TileLang row/column threadblock
  rasterization formula and uses the resulting logical tile ids for
  `tile_col0` and `tile_row0`.
- Runtime-source Metal execution covers enabled row swizzle on a non-trivial
  3x3 tile grid (`M=17, N=19, block_M=8, block_N=8`) and matches the CPU
  oracle after command-buffer submit, fence completion, and readback.

## Reference Notes

The implemented mapping follows local TileLang:

- `~/tilelang/tilelang/language/annotations.py` maps
  `T.use_swizzle(panel_size, order="row")` to `rasterization2DRow` and
  `order="col"` to `rasterization2DColumn`.
- `~/tilelang/src/tl_templates/cuda/threadblock_swizzle.h` and
  `~/tilelang/tilelang/contrib/cutedsl/threadblock_swizzle.py` define the
  row/column formulas. The mapping is a 2-D permutation of block ids and
  preserves `blockIdx.z`.

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
  tests/kernel/test_tilelang_import_broader.py::test_enabled_use_swizzle_row_rasterization_survives_source_and_cpu_oracle \
  tests/kernel/test_tilelang_import_broader.py::test_enabled_use_swizzle_column_rasterization_survives_source_and_cpu_oracle \
  tests/kernel/test_tilelang_import_broader.py::test_enabled_use_swizzle_bad_order_fails_closed
```

Result: 3 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_enabled_swizzle_runtime_source_matches_cpu_oracle
```

Result: 1 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py
```

Result: 34 passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 17 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 61 passed.

```bash
gtimeout 660s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 24 passed.

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
gtimeout 780s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 237 passed.

## Claim Boundary

This proves enabled TileLang row/column threadblock rasterization only for the
current strict scalar GEMM Metal source path. It does not prove arbitrary
threadblock swizzle placement, cluster-aware swizzle, swizzle on the opt-in
simdgroup path, arbitrary TileLang loop bodies, arbitrary layout functions,
TMA/wgmma descriptor lowering, performance, `.air/.metallib` production,
pcc1-native GPU launch, five-GC GPU lifetime parity, or whole-program GPU
execution.
