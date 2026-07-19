# TileLang Vectorized Tile-Copy Runtime Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- The TileLang importer now accepts `for ... in T.vectorized(...)` and
  `for ... in T.vectorized(0, extent)` as one-dimensional schedule metadata.
- The supported executable shape is deliberately narrow: legal tile-copy
  staging only, with outer `T.Parallel(...)` dimensions plus the inner
  `T.vectorized(...)` extent matching the full tile shape.
- CPU reference and Metal source validation now accept the A/B/C tile-copy
  staging pattern:
  - A global-to-shared copy into `A_shared`.
  - B global-to-shared copy into `B_shared`.
  - C fragment/local-to-global copy from `C_local` to `C`.
- Non-copy, non-staging, or extent-mismatched vectorized metadata still fails
  closed. This is schedule metadata and validation, not a claim of emitted SIMD
  vector instructions.
- Runtime-source Metal execution covers the vectorized A/B/C tile-copy variant
  and compares device readback against the CPU oracle.

## Reference Notes

Local reference search found the same shape family in the pinned source trees:

- `~/tilelang/src/transform/loop_vectorize.cc`
- `~/tilelang/testing/python/kernel/test_tilelang_kernel_gemm_simt.py`
- `~/pcc_refs/apache-tvm-full-depth1/tests/python/tirx/transform/test_transform_lower_tirx.py`
- `~/pcc_refs/apache-tvm-full-depth1/tests/python/tvmscript/test_tvmscript_syntax_sugar.py`

The pcc importer still does not execute TileLang/TVM; these references only
calibrate accepted syntax and claim boundaries.

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 21 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 9 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 43 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 16 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_compat.py
```

Result: 23 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 209 passed.

```bash
gtimeout 240s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 120s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: OK: 22 tasks validated.

```bash
gtimeout 120s git diff --check
```

Result: passed.

## Claim Boundary

This proves only one-dimensional `T.vectorized` metadata as part of legal
tile-copy staging for the current strict scalar tiled GEMM subset. It does not
prove arbitrary vectorized loop bodies, vector instruction emission, non-copy
vectorized loops, real split-k atomic accumulation, TileLang/TVM pass
execution, `.air/.metallib` production, pcc1-native GPU launch, five-GC GPU
lifetime parity, or whole-program GPU execution.
