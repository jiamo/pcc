# TileLang Swizzled Padded Layout Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- Metal scalar GEMM source lowering now handles the TileLang
  `make_swizzled_layout(...)` padded-layout fallback for bank-incompatible
  rank-2 shared A/B locals.
- The layout helper still chooses TileLang-compatible 128B/64B/32B bank
  swizzles when the static `continuous` dimension is compatible. Otherwise it
  emits the TileLang padded row-major physical stride:
  `padded = continuous; if element_bits * continuous % 256 == 0: padded += vector_size`.
- Threadgroup storage allocation now uses the physical local layout extent,
  not only the logical tile element count, so a padded physical stride cannot
  index beyond the declared shared array.
- Source tests cover a bank-incompatible f32 12-wide shared tile and assert
  that the emitted source uses padded row-major physical strides rather than
  XOR bank-swizzle indexing.
- Runtime-source Metal execution covers the current f16 padded-fallback
  scalar-GEMM variant through `newLibraryWithSource`, command-buffer submit,
  fence completion, readback, and CPU-oracle comparison.

## Reference Notes

The local TileLang reference is `~/tilelang/src/layout/gemm_layouts.cc`,
especially `MakeGemmABLayoutPadded(...)` and the `MakeSwizzledLayout(...)`
fallback order. This slice mirrors only the current scalar-GEMM rank-2 shared
A/B local subset. It does not implement arbitrary layout functions, TMA/wgmma
descriptors, dynamic layouts, or general TVM/TIR pass lowering.

For the currently supported f16/f32 input dtypes, the extra-vector padding case
is mostly dominated by bank-swizzle-compatible widths. The implementation still
routes allocation size through the physical-layout helper so future supported
element sizes or shapes cannot silently under-allocate.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_swizzled_annotate_layout_padded_shape_uses_padded_physical_stride \
  tests/kernel/test_tilelang_import_broader.py::test_swizzled_annotate_layout_padded_f32_uses_bank_incompatible_stride
```

Result: 2 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_swizzled_padded_annotate_layout_runtime_source_matches_cpu_oracle
```

Result: 1 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 27 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 13 passed.

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 54 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 20 passed.

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

Result: 226 passed.

## Claim Boundary

This proves TileLang-compatible padded-layout physical indexing for the current
rank-2 shared A/B scalar-GEMM subset, plus runtime-source Metal execution for a
covered padded-fallback GEMM variant. It does not prove arbitrary
`T.annotate_layout` functions, enabled `T.use_swizzle` rasterization,
TMA/wgmma descriptor lowering, performance, `.air/.metallib` production,
pcc1-native GPU launch, five-GC GPU lifetime parity, or whole-program GPU
execution.
