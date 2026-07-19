# TileLang Simdgroup Split-K Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM source path now handles non-8-wide split-k
  atomic K spans for the current 8x8x8 f16/f16->f32 microkernel slice instead
  of failing closed.
- Divisible 8-wide split-k shapes keep the existing direct global
  `simdgroup_load` path.
- Non-8-wide split spans and explicit `ceildiv(K, split_k)` tails use a
  correctness-first staged path: `split_k0` and `split_k_end` define the legal
  K range for the current `tgid.z`; each threadgroup fills `threadgroup half`
  A/B tiles with bounds-checked source loads or `half(0.0)`, runs the
  simdgroup matrix multiply, stores the accumulator into `threadgroup float
  C_tile[64]`, then atomically adds the valid C elements.
- Runtime-source Metal execution covers both `K=24, split_k=2`
  (`k_span=12`) and explicit `K=17, split_k=4, ceildiv(K, split_k)=5`,
  submits command buffers, completes fences, reads back C, and matches the CPU
  oracle.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_non_8wide_split_span_uses_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_ceildiv_tail_uses_min_split_end \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_non_8wide_span_runtime_source_matches_cpu_oracle \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_splitk_atomic_ceildiv_tail_runtime_source_matches_cpu_oracle
```

Result: 4 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 22 passed.

```bash
gtimeout 720s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 46 passed.

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

Result: 255 passed.

```bash
gtimeout 180s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: OK, 27 tasks validated.

## Claim Boundary

This proves non-8-wide split-k atomic K-span and explicit ceildiv split-k tail
handling for the current opt-in 8x8x8 f16/f16->f32 Metal simdgroup GEMM
runtime-source path, with M/N still aligned to 8. It does not prove split-k
atomic M/N edge tiles, larger TileLang block tiling, multiple simdgroups per
threadgroup, arbitrary indexed TileLang expressions, arbitrary/dynamic loop
ranges, arbitrary swizzle placement, cluster-aware swizzle, arbitrary TileLang
loop bodies, arbitrary layout functions, TMA/wgmma descriptor lowering,
performance, `.air/.metallib` production, pcc1-native GPU launch, five-GC GPU
lifetime parity, or whole-program GPU execution.
