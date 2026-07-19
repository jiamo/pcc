# 2026-07-06 GPU Level-4 TileLang Vectorized Nonzero-Serial Evidence

## Summary

The TileLang/TIRx coverage now proves a combined schedule shape instead of only
independent features. The imported TileLang GEMM uses legal tile-copy staging
with `T.Parallel` plus `T.vectorized` for A, B, and C tile copies, and the
same kernel runs under a nonzero K-loop range:

- `for ko in T.serial(1, T.ceildiv(K, block_K))`
- A copy under `T.Parallel(block_M)` plus `T.vectorized(block_K)`
- B copy under `T.Parallel(block_K)` plus `T.vectorized(block_N)`
- C copy under `T.Parallel(block_M)` plus `T.vectorized(block_N)`

For `M=5,N=7,K=24`, CPU oracle/source/runtime evidence proves the generated
Metal executes only K tiles `1..2` (`ko=1..3` in source), and the strict
hardware claim gate classifies the result as `GPU_LEVEL_4_DEVICE_RESULT`.
The device result is persisted and verified through
`metal_source_runtime_package_manifest.json`, including source-runtime
compilation, command-buffer completion, fence completion, CPU-oracle match,
no `.metallib`, and no whole-program GPU claim.

## Files

- `tests/kernel/test_tilelang_import_broader.py`
- `tests/kernel/test_metal_tilelang_gemm_runtime.py`
- `tests/gpu_hardware/test_metal_claim_levels.py`

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_vectorized_abc_tile_copy_combines_with_nonzero_serial_range
```

Result: `1 passed in 0.28s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_vectorized_nonzero_serial_runtime_source_matches_cpu_oracle
```

Result: `1 passed in 1.40s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py::test_gpu_level4_tilelang_vectorized_nonzero_serial_device_result_or_skip
```

Result: `1 passed in 1.57s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py
```

Result: `35 passed in 0.09s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: `18 passed in 12.34s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `6 passed in 4.13s`.

## Claim Boundary

This proves the strict runtime-source Level-4 hardware result plus manifest
round-trip for one combined TileLang schedule shape: legal
`T.Parallel`/`T.vectorized` A/B/C tile-copy staging together with a nonzero
static K-loop range.

This does not prove arbitrary executable `T.Parallel`/`T.vectorized` loop
bodies, arbitrary nested/multi-argument loop forms, arbitrary split-K index
expressions, TMA/wgmma layout lowering, `.air/.metallib` production,
metallib-backed command-buffer submission, pcc1-native GPU launch, five-GC GPU
lifetime parity, performance, or whole-program GPU execution.
