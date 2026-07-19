# TileLang Transposed GEMM Runtime Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- `execute_scalar_tiled_gemm_reference(...)` now accepts the static transposed
  GEMM layouts used by TileLang-style calls:
  - `transpose_A=True`: `A` is shaped `(K, M)` and `A_shared` is shaped
    `(block_K, block_M)`.
  - `transpose_B=True`: `B` is shaped `(N, K)` and `B_shared` is shaped
    `(block_N, block_K)`.
  - `transpose_A=True` and `transpose_B=True`: both transposed storage layouts
    are accepted together.
- `emit_metal_source(...)` now emits matching Metal source for those same
  layouts instead of treating all transposed GEMM as unsupported.
- Runtime-source Metal execution now covers small `transpose_A` and
  `transpose_B` TileLang/TIRx GEMM variants, including the combined
  `transpose_A+transpose_B` variant, through `newLibraryWithSource`,
  command-buffer submit, fence completion, device readback, and CPU-oracle
  comparison.

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

Result: 11 passed.

Follow-up combined-transpose extension:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 12 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 5 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 34 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 12 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 4 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 33 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 11 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: initially 193 passed; after the combined transpose extension, 195 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
gtimeout 240s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed in both modes.

```bash
gtimeout 120s env -u LC_ALL uv run python scripts/goal_state.py validate
gtimeout 120s git diff --check
```

Result: goal state validated 22 tasks; diff check clean.

## Claim Boundary

This proves only the strict static scalar tiled transposed GEMM subset through
runtime-source Metal, including `transpose_A`, `transpose_B`, and combined
`transpose_A+transpose_B` storage layouts. It is not real split-k atomic
accumulation, not general TileLang/TIRx pass execution, not arbitrary
`T.Parallel` / `T.vectorized` lowering, not `annotate_layout` / `use_swizzle`,
not `.air/.metallib` production, not pcc1-native GPU launch, and not
whole-program GPU execution.
