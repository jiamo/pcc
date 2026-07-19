# TileLang Simdgroup Eight-N Edge/Tail Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now has runtime-source proof for the
  first eight-simdgroup-per-threadgroup edge/tail direct-copy tile.
- Covered shape: `M=15`, `N=31`, `K=9`, `block_m=16`, `block_n=32`,
  `block_k=8`, `threads=256`.
- Source emission uses `simdgroup_index_in_threadgroup` to map eight simdgroups
  onto a 2x4 grid of 8x8 C subtiles and uses per-simdgroup A/B/C staging:
  `A_tile[512]`, `B_tile[512]`, `C_tile[512]`, `thread_index_in_simdgroup`,
  and `simdgroup_tile_offset`.
- Each simdgroup zero-pads its own A/B 8x8 subtile and bounds-checks final C
  writeback for the M/N edge and K tail.
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_edge_tail_uses_per_simdgroup_staging \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_eight_simdgroups_edge_tail_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 540s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 48 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first eight-simdgroup direct-copy f16/f16->f32 Metal
simdgroup GEMM runtime-source edge/tail shape, `M=15,N=31,K=9`, with one 2x4
grid of 8x8 simdgroup subtiles inside a threadgroup. It does not prove more
than eight simdgroups per threadgroup, eight-simdgroup transposed edge/tail
staging, eight-simdgroup split-k atomic output, arbitrary larger TileLang block
tiling, performance, `.air/.metallib` production, pcc1-native GPU launch,
five-GC GPU lifetime parity, or whole-program GPU execution.
