# 2026-07-08 GPU Level-5 pcc1 TileLang Vectorized Annotations Evidence

## Summary

`GPU-P1-BROADER-TILELANG-TIRX-PASSES` now has pcc1-native Level-5 proof for
the TileLang vectorized annotation metadata slice.

The covered source form is the current legal A/B/C tile-copy staging subset
with:

```python
for i in T.Parallel(block_M):
    for kk in T.vectorized(0, block_K, annotations={"pragma_unroll": True}):
        T.copy(A[by * block_M + i, ko * block_K + kk], A_shared)
```

The imported Kernel IR records `vectorized_extent=8`, `vectorized_var="kk"`,
and `vectorized_annotations={"pragma_unroll": True}`. The pcc1-compiled
no-libpython executable creates real A/B/C native MTLBuffers, writes A(5,16)
and B(16,7) f16 payloads, launches the generated scalar tiled GEMM Metal source
through `pcc_metal_source_runtime_call_prebuilt(...)`, reads C(5,7) f32 output
back, checks the CPU oracle, and releases native buffers after launch.

The test classifies the result as `GPU_LEVEL_5_PCC1_NATIVE`.

## Gates

```bash
gtimeout 60s env -u LC_ALL \
  uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py \
  tests/gpu_hardware/test_metal_5gc_lifetime_real.py
```

Result: passed.

```bash
gtimeout 300s env -u LC_ALL \
  uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_vectorized_annotations_runtime_source_matches_cpu_oracle -rs
```

Result: `1 passed in 0.78s`.

```bash
gtimeout 1200s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_pcc1_launch_real.py::test_level5_pcc1_compiled_program_runs_tilelang_vectorized_annotations -rs
```

Result: `1 passed in 2.18s`.

```bash
gtimeout 1800s env -u LC_ALL \
  PCC_CURRENT_PCC1=build/bootstrap-gpu-level5-pcc1-shim/pcc1 \
  uv run pytest -q -n0 tests/gpu_hardware/test_metal_pcc1_launch_real.py -rs
```

Result: `35 passed in 65.75s`.

## Claim Boundary

This proves `GPU_LEVEL_5_PCC1_NATIVE` for one legal
`T.vectorized(0, extent, annotations={...})` tile-copy staging form in the
current scalar tiled GEMM subset. It is not arbitrary vectorized loop-body
execution, not nonzero vectorized starts, not vectorized step support, not
`.air/.metallib` production, not metallib-backed launch, not performance
evidence, and not whole-program GPU execution.
