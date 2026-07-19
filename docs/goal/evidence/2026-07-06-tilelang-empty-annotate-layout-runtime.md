# TileLang Empty Annotate-Layout Runtime Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- The TileLang importer now accepts `T.annotate_layout({})` as an explicit
  Kernel IR `layout_annotation` op.
- The supported executable shape is deliberately narrow: the mapping must be
  empty. That is a no-op layout annotation, so CPU oracle and Metal source
  lowering can preserve it through import/TIRx while keeping the existing
  memory layout and tile mapping unchanged.
- Non-empty `T.annotate_layout({...})` remains fail-closed at import time
  because pcc does not yet implement shared-memory bank-swizzle layouts or
  arbitrary TileLang layout functions.
- The plain-TIR freeze records the no-op annotation as `tir.annotate_layout`
  with `entries=0` instead of silently dropping it.
- Runtime-source Metal execution covers the empty-annotation GEMM variant and
  compares device readback against the CPU oracle.

## Reference Notes

Local TileLang code uses `T.annotate_layout(...)` to carry layout transforms
such as swizzled shared-memory layouts. This slice does not implement those
layout transforms. It only makes the empty annotation explicit in pcc Kernel IR
so the importer has a precise no-op route while non-empty layout mappings stay
unsupported.

The pcc importer still does not execute TileLang/TVM. This slice proves no-op
metadata preservation and fail-closed non-empty-layout behavior only.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
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
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_tilelang_compat.py
```

Result: 61 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 11 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 49 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 18 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 219 passed.

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

This proves only empty `T.annotate_layout({})` metadata preservation plus
runtime-source execution of the unchanged layout/tile mapping. It does not
prove non-empty layout mappings, shared-memory bank-swizzle layouts, enabled
`T.use_swizzle` rasterization, arbitrary TileLang/TVM pass execution,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
