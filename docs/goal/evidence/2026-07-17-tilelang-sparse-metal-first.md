# First pcc-owned TileLang sparse Metal execution

Date: 2026-07-17

Task: `GPU-P1-TILELANG-SPARSE-METAL-FIRST`

## Proven slice

- The existing strict importer and TIRx freeze retain TileLang `T.gemm_sp` as
  the owner/pass identity for one fixed `M=5, N=7, K=16`, `block=8x8x16`,
  32-thread shape.
- pcc Metal source lowering validates that exact operation sequence, shapes,
  scopes, dtypes, disabled swizzle, one K tile, no transpose, and copy-back
  output. Drift fails closed.
- The emitted pcc-owned scalar Metal kernel decodes signed int16-packed 2:4
  metadata and performs f16 x f16 multiplication with f32 accumulation. It
  does not call TileLang/TVM and does not present scalar decoding as a sparse
  MMA or performance path.
- The generic Metal matrix-transfer seam now supports i8/u8/i16/u16 POD
  matrices, allowing the metadata buffer to cross the real native boundary.
- A real runtime-source Metal command buffer completes and its f32 device
  readback exactly matches the independent CPU 2:4 oracle.

## Gates

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_sparse_metal_runtime.py \
  tests/kernel/test_kernel_cpu_reference.py -rs
```

Result: `9 passed in 1.53s` (no skips).

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_local_tilelang_sparse_matmul_benchmark_imports_tirx_cpu_oracle_and_metal_fail_closed -rs
```

Result: `1 passed in 0.54s`.

## Claim boundary

This proves pcc ownership and real Metal execution only for the exact scalar
correctness shape above. It does not prove arbitrary sparse shapes, other
metadata widths or encodings, transpose, multiple K tiles, sparse Metal
hardware intrinsics, simdgroup/tensor-core lowering, pcc1-native launch,
prebuilt metallib production, five-GC parity, performance, TileLang/TVM
runtime ownership, or whole-program GPU execution.
