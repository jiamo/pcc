# 2026-07-08 TileLang Vectorized Annotations Runtime-Source Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now preserves explicit TileLang
`T.vectorized(..., annotations={...})` metadata for the current legal
A/B/C tile-copy staging subset.

The covered source form is:

```python
for i in T.Parallel(block_M):
    for kk in T.vectorized(0, block_K, annotations={"pragma_unroll": True}):
        T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)
```

The importer records:

- `parallel_extents=[8]`
- `parallel_vars=["i"]`
- `vectorized_extent=8`
- `vectorized_var="kk"`
- `vectorized_annotations={"pragma_unroll": True}`

Plain-TIR freeze preserves the same metadata on the `tir.copy_loop` op. The
CPU reference and Metal source validators accept the annotations only as dict
metadata on already-legal tile-copy staging; they do not reinterpret it as a
new execution mode.

Runtime-source Metal execution still compiles through `newLibraryWithSource`,
submits a command buffer, reads C(5,7) back, and matches the CPU oracle.

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_vectorized_annotations_survive_import_freeze_source_and_cpu_oracle -rs
```

Result: `1 passed in 0.13s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_vectorized_annotations_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.94s`.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs
```

Result: `41 passed in 0.09s`.

```bash
gtimeout 360s env -u LC_ALL \
  uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
```

Result: `23 passed in 14.76s`.

## Claim Boundary

This proves metadata preservation plus host-harness runtime-source Metal
execution for one legal `T.vectorized(0, extent, annotations={...})` tile-copy
staging form in the current scalar tiled GEMM subset. It is not pcc1-native
Level-5 proof, not five-GC Level-6 proof, not arbitrary vectorized loop-body
execution, not nonzero vectorized starts, not vectorized step support, not
`.air/.metallib` production, not metallib-backed launch, not performance
evidence, and not whole-program GPU execution.
