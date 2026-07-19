# TileLang Simdgroup Four-2D Split-K Atomic Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for the
  first four-simdgroup 2-D tile with aligned split-k atomic output.
- Covered shape: `M=16`, `N=16`, `K=16`, `block_m=16`, `block_n=16`,
  `block_k=8`, `threads=128`, `split_k=2`.
- Source emission uses `tgid.z` for the split-k axis, `simdgroup_index_in_threadgroup`
  for the 2-D 8x8 subtile inside the 16x16 threadgroup tile, and
  `thread_index_in_simdgroup` plus `simdgroup_tile_offset` for per-simdgroup
  C staging before `atomic_fetch_add_explicit(...)`.
- Multi-simdgroup atomic edge/tail staging remains fail-closed. A regression
  asserts that `M=15,N=15,K=17,split_k=4,ceildiv` is rejected rather than
  silently claimed.
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_splitk_atomic_uses_per_simdgroup_c_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_splitk_atomic_edge_tail_still_fails_closed \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_four_simdgroups_splitk_atomic_runtime_source_matches_cpu_oracle
```

Result: 3 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 41 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first four-simdgroup 2-D tile with aligned split-k atomic
f32 output accumulation for the current opt-in f16/f16->f32 Metal simdgroup
GEMM runtime-source path. It does not prove multi-simdgroup atomic M/N edge
tiles, non-8-wide split spans, arbitrary larger TileLang block tiling, more
than four simdgroups per threadgroup, arbitrary indexed TileLang expressions,
performance, `.air/.metallib` production, pcc1-native GPU launch, five-GC GPU
lifetime parity, or whole-program GPU execution.
