# TileLang Simdgroup Transposed Operands Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM source path now uses normalized GEMM
  dimensions (`M/N/K` and `block_M/block_N/block_K`) plus
  `transpose_A` / `transpose_B` metadata instead of deriving the microkernel
  shape directly from raw operand shapes.
- For `transpose_A`, `simdgroup_load` reads A as a K-major source with stride
  `M` and passes `transpose=true`, matching the existing scalar GEMM and CPU
  oracle semantics for A(K,M).
- For `transpose_B`, `simdgroup_load` reads B as an N-major source with stride
  `K` and passes `transpose=true`, matching the existing scalar GEMM and CPU
  oracle semantics for B(N,K).
- Runtime-source Metal execution covers combined `transpose_A=True` and
  `transpose_B=True` for `M=16, N=24, K=8`, submits the command buffer,
  completes the fence, reads back C, and matches the CPU oracle.

## Reference Notes

The simdgroup operand transpose shape follows the local references already used
for this track:

- `~/pcc_refs/apache-tvm-full-depth1/python/tvm/backend/metal/op.py`
  exposes `simdgroup_load(..., transpose_matrix=False)`.
- `~/tilelang/src/metal/codegen/codegen_metal.cc` lowers
  `simdgroup_load(..., ..., transpose_flag)` in the Metal codegen path.

The pcc implementation still treats this as a strict Kernel IR source/runtime
slice, not as TileLang/TVM runtime execution and not as a `.metallib` claim.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_transposed_operands_use_transposed_simdgroup_loads \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_transposed_operands_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 15 passed.

```bash
gtimeout 720s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 39 passed.

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

Result: 248 passed.

## Claim Boundary

This proves combined `transpose_A` / `transpose_B` operand layout handling for
the current opt-in 8x8x8 f16/f16->f32 Metal simdgroup GEMM runtime-source path.
It does not prove larger TileLang block tiling, multiple simdgroups per
threadgroup, edge-tile predication, non-divisible simdgroup split-k tails,
arbitrary indexed TileLang expressions, arbitrary/dynamic loop ranges,
arbitrary swizzle placement, cluster-aware swizzle, arbitrary TileLang loop
bodies, arbitrary layout functions, TMA/wgmma descriptor lowering, performance,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
