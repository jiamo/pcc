# TileLang A/B/C Parallel Tile-Copy Runtime Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- The broader TileLang coverage now explicitly accepts the legal output
  staging copy as `T.Parallel(...)` metadata:
  - A global-to-shared tile copy into `A_shared`.
  - B global-to-shared tile copy into `B_shared`.
  - C fragment/local-to-global tile copy from `C_local` to `C`.
- The A+B+C tile-copy variant preserves `parallel_extents` and
  `parallel_vars` through import and TIRx/plain-TIR freeze.
- CPU reference and Metal source validation accept these staging copies only
  when the parallel extents match the destination/source tile shape:
  `A_shared`, `B_shared`, or `C_local`.
- Runtime-source Metal execution now covers the A+B+C `T.Parallel` tile-copy
  variant and compares device readback against the CPU oracle.

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
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

Result: 18 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 8 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 40 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 15 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 204 passed.

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

This proves only the legal global-to-shared A/B tile-copy staging and
fragment/local-to-global C tile-copy staging forms for the current strict
scalar tiled GEMM subset. It does not prove arbitrary `T.Parallel` loop bodies,
non-copy parallel loops, `T.vectorized`, real split-k atomic accumulation,
TileLang/TVM pass execution, `.air/.metallib` production, pcc1-native GPU
launch, five-GC GPU lifetime parity, or whole-program GPU execution.
