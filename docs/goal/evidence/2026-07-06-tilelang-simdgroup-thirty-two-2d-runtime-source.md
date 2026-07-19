# TileLang Simdgroup Thirty-Two 2D Runtime-Source Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Related task: `GPU-P0-SIMDGROUP-TENSORCORE-GEMM`

## What Changed

- The opt-in Metal simdgroup GEMM path now supports and proves the first
  thirty-two-simdgroup-per-threadgroup direct-copy 2D tile.
- Covered shape: `M=32`, `N=64`, `K=8`, `block_m=32`, `block_n=64`,
  `block_k=8`, `threads=1024`.
- Source emission maps thirty-two simdgroups onto a 4x8 grid of 8x8 C subtiles
  inside the 32x64 threadgroup tile using `simdgroup_index_in_threadgroup`.
- The conservative emitter cap was raised from sixteen to thirty-two
  simdgroups per threadgroup; larger shapes still fail closed.
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
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_thirty_two_simdgroups_per_threadgroup_cover_wide_2d_tile \
  tests/kernel/test_metal_simdgroup_gemm.py::test_simdgroup_gemm_thirty_two_simdgroups_per_threadgroup_runtime_source_matches_cpu_oracle
```

Result: 2 passed.

```bash
gtimeout 600s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_simdgroup_gemm.py
```

Result: 70 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_cpu_reference.py \
  tests/kernel/test_metal_finalize.py
```

Result: 11 passed.

## Claim Boundary

This proves only the first thirty-two-simdgroup direct-copy f16/f16->f32 Metal
simdgroup GEMM runtime-source tile. It does not prove more than thirty-two
simdgroups per threadgroup, thirty-two-simdgroup edge/tail staging,
thirty-two-simdgroup split-k atomic output, arbitrary larger TileLang block
tiling, arbitrary split-K expressions, arbitrary/non-f32 atomics, performance,
`.air/.metallib` production, pcc1-native GPU launch, five-GC GPU lifetime
parity, or whole-program GPU execution.
