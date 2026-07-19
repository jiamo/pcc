# TileLang Simdgroup Four-2D Edge/Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now covers the first four-simdgroup
  2-D tile with non-atomic copy-output M/N edge tiles plus a K tail.
- Covered shape: `M=15`, `N=15`, `K=9`, `block_m=16`, `block_n=16`,
  `block_k=8`, `threads=128`.
- Multi-simdgroup edge/tail source uses per-simdgroup threadgroup staging:
  `A_tile[256]`, `B_tile[256]`, `C_tile[256]`,
  `thread_index_in_simdgroup`, and `simdgroup_tile_offset`.
- Each simdgroup zero-pads only its own 8x8 A/B sub-tile, loads from its own
  staging region, stores C into its own staging region, and bounds-checks
  final C writeback.
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_edge_tail_uses_per_simdgroup_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_edge_tail_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 36 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first four-simdgroup 2-D tile combined with non-atomic
copy-output M/N edge tiles and a K tail for the current opt-in f16/f16->f32
Metal simdgroup GEMM runtime-source path. It does not prove arbitrary larger
TileLang block tiling, more than four simdgroups per threadgroup,
multi-simdgroup atomic output, arbitrary indexed TileLang expressions,
performance, `.air/.metallib` production, pcc1-native GPU launch, five-GC GPU
lifetime parity, or whole-program GPU execution.
