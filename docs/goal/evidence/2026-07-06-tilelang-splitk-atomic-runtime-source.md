# TileLang Split-K Atomic Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- `T.atomic_add(dst, src)` now imports as Kernel IR `atomic_add` and freezes
  through TIRx as `tir.atomic_add`.
- The current strict scalar tiled GEMM subset recognizes the real TileLang
  split-k shape: 3-D `T.Kernel(..., split_k, ...)`, per-`bz` K-slice A/B
  staging, `T.gemm(...)`, then `T.atomic_add(C[...], C_local[...])`.
- The CPU oracle treats `tir.atomic_add(C, C_local)` plus a 3-D grid as
  split-k accumulation. It requires `K % split_k == 0`, executes each `bz`
  partition over `K / split_k`, and accumulates the full C result.
- Metal scalar GEMM source emits the f32 output path with `uint3`
  thread/grid positions, `tgid.z` K-slice selection, `device atomic_float* C`,
  and `atomic_fetch_add_explicit(...)`.
- Runtime-source Metal execution covers the split-k atomic GEMM variant through
  `newLibraryWithSource`, command-buffer submit, fence completion, readback,
  and CPU-oracle comparison.

## Reference Notes

This mirrors the local TileLang examples under
`~/tilelang/examples/gemm_splitk/`, where split-k GEMM ends in
`T.atomic_add(...)` rather than a plain C copy.

The claim is intentionally limited to the current f16/f32 input, f32 output,
rank-2 scalar-GEMM path with static divisible `K/split_k`. It does not prove
arbitrary atomics, non-f32 atomic output, non-divisible split-K tails,
arbitrary indexed TileLang expressions, dynamic split counts, simdgroup
split-k, or performance.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/ir.py \
  pcc/kernel_ir/tirx_adapter.py \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/cpu_reference.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: passed.

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_splitk_atomic_add_survives_import_freeze_source_and_cpu_oracle
```

Result: 1 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_splitk_atomic_runtime_source_matches_cpu_oracle
```

Result: 1 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py
```

Result: 14 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py
```

Result: 30 passed.

```bash
gtimeout 480s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 15 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 57 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 22 passed.

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
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 231 passed.

## Claim Boundary

This proves real split-k atomic accumulation for the current strict scalar
TileLang/TIRx GEMM subset only: static rank-2 A/B/C, f16/f32 inputs, f32 C,
divisible `K/split_k`, one GEMM op, and `T.atomic_add(C, C_local)` as the
final output. It does not prove arbitrary atomics, non-f32 atomics,
non-divisible split-K tails, arbitrary TileLang indexed expressions, dynamic
constructs, simdgroup/tensorcore split-k, enabled `T.use_swizzle`
rasterization, arbitrary layout functions, TMA/wgmma descriptor lowering,
performance, `.air/.metallib` production, pcc1-native GPU launch, five-GC GPU
lifetime parity, or whole-program GPU execution.
