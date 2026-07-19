# TileLang Disabled Use-Swizzle Runtime Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- The TileLang importer now accepts `T.use_swizzle(...)` as an explicit Kernel
  IR `swizzle` op.
- The supported executable shape is deliberately narrow: `enable=False` only.
  That is a no-op rasterization metadata marker, so CPU oracle and Metal source
  lowering can preserve it through import/TIRx while keeping the existing tile
  mapping unchanged.
- `enable=True` remains fail-closed in CPU oracle and Metal source lowering
  because pcc does not yet implement the swizzled threadgroup/grid mapping.
- The plain-TIR freeze records disabled swizzle as `tir.use_swizzle` with
  `panel_size` and `enable` attrs instead of dropping it silently.
- Runtime-source Metal execution covers the disabled-swizzle GEMM variant and
  compares device readback against the CPU oracle.

## Reference Notes

Local TileLang reference usage is mostly:

- `T.use_swizzle(panel_size=10, enable=enable_rasterization)` in matmul/autotune
  examples and tests.
- Several local test configurations pass `enable_rasterization=False`, making
  disabled swizzle a real, non-synthetic no-op route.

The pcc importer still does not execute TileLang/TVM. This slice proves no-op
metadata preservation and fail-closed enabled-swizzle behavior only.

## Gates

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/ir.py \
  pcc/kernel_ir/tirx_adapter.py \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  pcc/kernel_ir/tilelang_compat.py \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_tilelang_compat.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_tilelang_compat.py
```

Result: 57 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 10 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 46 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 17 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 214 passed.

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

This proves only disabled `T.use_swizzle(..., enable=False)` metadata
preservation plus runtime-source execution of the unchanged tile mapping. It
does not prove enabled rasterization/swizzled threadgroup mapping,
`T.annotate_layout`, shared-memory bank swizzle layouts, arbitrary TileLang/TVM
pass execution, `.air/.metallib` production, pcc1-native GPU launch, five-GC
GPU lifetime parity, or whole-program GPU execution.
