# TileLang Split-K Ceildiv Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- TileLang import now records explicit split-k copy span metadata for the
  current source-index shapes `bz * (K // split_k)` and
  `bz * T.ceildiv(K, split_k)`.
- The old floor-div split-k path still requires `K % split_k == 0`; when K is
  not divisible it fails closed in both CPU oracle and Metal source lowering.
- The new ceildiv split-k path supports non-divisible K tails for the current
  f32 atomic scalar-GEMM subset. CPU oracle uses `ceildiv(K, split_k)` as the
  per-split span and clamps each split to `min(split_k0 + span, K)`.
- Metal scalar GEMM source emits the same tail clamp:
  `uint split_k_end = min(split_k0 + span, K)`.
- Runtime-source Metal execution covers `K=17, split_k=4`, submits the command
  buffer, completes the fence, reads back C, and matches the CPU oracle.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_splitk_atomic_ceildiv_tail_survives_import_freeze_source_and_cpu_oracle \
  tests/kernel/test_tilelang_import_broader.py::test_splitk_atomic_floor_div_tail_fails_closed
```

Result: 2 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_splitk_atomic_ceildiv_tail_runtime_source_matches_cpu_oracle
```

Result: 1 passed.

```bash
gtimeout 360s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py
```

Result: 32 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 16 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 59 passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 23 passed.

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
gtimeout 720s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 234 passed.

## Claim Boundary

This proves non-divisible split-K tail handling only for the explicit
`T.ceildiv(K, split_k)` copy-index shape in the current strict scalar
TileLang/TIRx GEMM subset: static rank-2 A/B/C, f16/f32 inputs, f32 C, one
GEMM op, 3-D grid split axis, and final `T.atomic_add(C, C_local)`. It does
not prove arbitrary indexed TileLang expressions, floor-div non-divisible
semantics, arbitrary atomics, non-f32 atomics, dynamic split counts, simdgroup
split-k, enabled `T.use_swizzle` rasterization, arbitrary layout functions,
TMA/wgmma descriptor lowering, performance, `.air/.metallib` production,
pcc1-native GPU launch, five-GC GPU lifetime parity, or whole-program GPU
execution.
