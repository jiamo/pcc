# 2026-07-06 GPU Level-4 TileLang Vectorized Manifest Evidence

## Summary

The strict Metal hardware claim-level gate now covers a broader TileLang/TIRx
scheduled-loop shape, not only the simple imported GEMM and simdgroup paths.

The new gate imports and runs a TileLang GEMM that uses legal tile-copy staging
with both `T.Parallel` and `T.vectorized` loops:

- A global-to-shared tile copy under `T.Parallel(block_M)` and
  `T.vectorized(block_K)`
- B global-to-shared tile copy under `T.Parallel(block_K)` and
  `T.vectorized(block_N)`
- C fragment-to-global copy under `T.Parallel(block_M)` and
  `T.vectorized(block_N)`

For `M=5,N=7,K=16`, the result is classified as
`GPU_LEVEL_4_DEVICE_RESULT`, then persisted and verified through
`metal_source_runtime_package_manifest.json`. The manifest round-trip checks
artifact hashes plus runtime-source execution, fence completion, CPU-oracle
match, no `.metallib` production, and no whole-program GPU claim.

## Files

- `tests/gpu_hardware/test_metal_claim_levels.py`

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: passed.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py::test_gpu_level4_tilelang_vectorized_abc_copy_runtime_source_device_result_or_skip
```

Result: `1 passed in 0.96s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `5 passed in 3.49s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `5 passed in 3.48s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `12 passed in 3.64s`.

## Claim Boundary

This proves strict runtime-source Level-4 hardware result plus manifest
round-trip for the legal TileLang `T.Parallel`/`T.vectorized` A/B/C tile-copy
staging subset.

This does not prove arbitrary executable `T.Parallel`/`T.vectorized` loop
bodies, arbitrary nested/multi-argument loop forms, TMA/wgmma layout lowering,
`.air/.metallib` production, metallib-backed command-buffer submission,
pcc1-native GPU launch, five-GC GPU lifetime parity, performance, or
whole-program GPU execution.
