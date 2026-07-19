# TileLang Simdgroup Eight-N Split-K Atomic Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for the
  first eight-simdgroup-per-threadgroup aligned split-k atomic output shape.
- Covered shape: `M=16`, `N=32`, `K=16`, `block_m=16`, `block_n=32`,
  `block_k=8`, `threads=256`, `split_k=2`, `output_atomic=True`.
- Source emission maps eight simdgroups onto a 2x4 grid of 8x8 C subtiles,
  uses `tgid.z` for the split-k axis, and uses per-simdgroup C staging:
  `C_tile[512]`, `thread_index_in_simdgroup`, and `simdgroup_tile_offset`.
- Runtime-source Metal execution submits the command buffer, completes the
  fence, reads back C, and matches the CPU oracle.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_finalize.py \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_splitk_atomic_uses_per_simdgroup_c_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 52 passed.

## Claim Boundary

This proves only the first eight-simdgroup aligned split-k atomic f32 output
accumulation shape on the current opt-in f16/f16->f32 Metal simdgroup
runtime-source path. It does not prove more than eight simdgroups per
threadgroup, eight-simdgroup split-k with M/N edge tiles or K tails,
eight-simdgroup transposed split-k atomic output, arbitrary larger TileLang
block tiling, arbitrary split-K expressions, performance, `.air/.metallib`
production, pcc1-native GPU launch, five-GC GPU lifetime parity, or
whole-program GPU execution.
