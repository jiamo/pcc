# TileLang Swizzled Layout Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- Metal scalar GEMM source lowering now applies swizzled shared-memory layout
  metadata for the current rank-2 shared-local GEMM subset.
- The implemented index applier mirrors TileLang's default
  `make_swizzled_layout(...)` bank-swizzle choice:
  - f16/f32 element size decides the 128-bit vector width;
  - `continuous % (vector_size * 8/4/2)` selects 128B/64B/32B swizzle;
  - physical shared-memory offsets use the same XOR lane remap shape as
    TileLang's `MakeFull/Half/QuarterBankSwizzleLayout2D`.
- Row-major locals still use the existing row-major physical index through the
  same helper, so the old scalar GEMM path remains the fallback.
- Unsupported swizzled shapes that would require TileLang's padded layout still
  fail closed instead of silently emitting incorrect indexing.
- Runtime-source Metal execution now covers a swizzled
  `T.annotate_layout({A_shared/B_shared: make_swizzled_layout(...)})` GEMM
  variant and compares device readback against the CPU oracle.

## Reference Notes

The local TileLang reference for the supported swizzle formulas is
`~/tilelang/src/layout/gemm_layouts.cc`:

- `MakeQuarterBankSwizzleLayout2D`
- `MakeHalfBankSwizzleLayout2D`
- `MakeFullBankSwizzleLayout2D`
- `MakeSwizzledLayout`

This slice implements only the bank-swizzle cases selected by
`make_swizzled_layout` for statically shaped rank-2 shared locals. It does not
implement padded layouts, TMA descriptors, wgmma descriptors, or arbitrary
layout functions.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
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
  tests/kernel/test_tilelang_import_broader.py
```

Result: 26 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 12 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 53 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 19 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 224 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 240s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_tilelang_compat.py
```

Result: 77 passed.

```bash
gtimeout 120s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: OK: 22 tasks validated.

```bash
gtimeout 120s git diff --check
```

Result: passed.

## Claim Boundary

This proves runtime-source Metal execution for the current scalar GEMM subset
when rank-2 shared A/B tiles use TileLang-compatible bank-swizzled local
layouts. It does not prove padded layout support, arbitrary
`T.annotate_layout` functions, TMA/wgmma descriptor lowering, performance,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
