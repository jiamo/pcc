# 2026-07-06 GPU Level-4 Thirty-Two Simdgroup Manifest Evidence

## Summary

The strict Metal hardware claim-level gate now covers the latest
thirty-two-simdgroup simdgroup GEMM shape, not only the original 8x8
microkernel.

The new gate runs the combined hard shape:

- `M=31,N=63,K=17`
- `block_m=32,block_n=64`
- `threads=1024`
- `split_k=4`
- `split_k_span_mode=ceildiv`
- `output_atomic=True`
- `transpose_A=True`
- `transpose_B=True`

The result is classified through `classify_metal_source_runtime_package_result`
as `GPU_LEVEL_4_DEVICE_RESULT`, then persisted and verified through
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
  tests/gpu_hardware/test_metal_claim_levels.py::test_gpu_level4_simdgroup_gemm_32_splitk_transpose_edge_tail_device_result_or_skip
```

Result: `1 passed in 1.07s`.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `4 passed in 3.04s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `4 passed in 3.33s`.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/gpu_hardware/test_metal_claim_levels.py
```

Result: `11 passed in 3.15s`.

## Claim Boundary

This proves a strict runtime-source Level-4 hardware result plus manifest
round-trip for the latest thirty-two-simdgroup transposed split-k atomic
edge/tail shape.

This does not prove more than thirty-two simdgroups per threadgroup, arbitrary
TileLang block tiling, arbitrary split-K expressions, non-f32 atomics,
`.air/.metallib` production, metallib-backed command-buffer submission,
pcc1-native GPU launch, five-GC GPU lifetime parity, performance, or
whole-program GPU execution.
