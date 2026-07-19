# TileLang Simdgroup Split-K Atomic Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM source path now supports divisible split-k
  atomic f32 output accumulation for the current 8x8x8 f16/f16->f32
  microkernel slice.
- The source uses `tgid.z` as the split-k axis, shifts A/B simdgroup loads by
  `split_k0`, stores the simdgroup accumulator into a threadgroup `float[64]`
  tile, then uses 32 threads to `atomic_fetch_add_explicit(...)` each f32 C
  element.
- Non-8-wide per-split K spans still fail closed. This prevents the simdgroup
  path from reading across split boundaries or pretending the existing scalar
  ceildiv-tail proof also covers simdgroup.
- Runtime-source Metal execution covers `K=16`, `split_k=2`, command-buffer
  submit, fence completion, readback, and CPU-oracle match.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_source_uses_tgid_z_and_atomic_add \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_rejects_non_8wide_split_span \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_runtime_source_matches_cpu_oracle
```

Result: 3 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 13 passed.

```bash
gtimeout 660s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 37 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 61 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 900s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 246 passed.

## Claim Boundary

This proves divisible split-k atomic f32 output accumulation for the current
opt-in 8x8x8 f16/f16->f32 Metal simdgroup GEMM runtime-source path. It does
not prove non-divisible simdgroup split-k tails, larger TileLang block tiling,
multiple simdgroups per threadgroup, edge-tile predication, arbitrary/non-f32
atomics, arbitrary indexed TileLang expressions, arbitrary/dynamic loop ranges,
arbitrary swizzle placement, cluster-aware swizzle, arbitrary TileLang loop
bodies, arbitrary layout functions, TMA/wgmma descriptor lowering, performance,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
