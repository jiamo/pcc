# GPU-P1 broader TileLang/TIRx phase-1 closure — 2026-07-17

Mode: pcc TileLang source-subset importer (AST only; no runtime import or
execution of TileLang/TVM) -> pcc Kernel IR -> plain TIRx -> pcc Metal routes.

Final slice:

- `T.fill(buffer, 0)` and `T.fill(buffer, 0.0)` now lower to the same explicit
  zero-valued Kernel IR `fill` contract as `T.clear`;
- zero fill survives the TIRx freeze, CPU GEMM oracle, pcc Metal source, and
  real runtime-source Metal device readback;
- nonzero, non-finite, and dynamic fill values remain fail-closed rather than
  being silently treated as clear.

Phase-1 claim boundary:

This closes the accumulated finite broader-matmul subset recorded in the task
row: static split-K/ceildiv forms, serial/pipelined range forms, legal scheduled
copy metadata, swizzle/layout annotations, f32 atomic output, exact output
staging, static transpose variants, sparse `gemm_sp` import + CPU oracle, and
the runtime/metallib/pcc1/five-GC slices explicitly recorded by their prior
evidence. It does not claim arbitrary TileLang or TVM pass execution.

The former infinite open boundary was split into finite follow-up rows:

- `GPU-P1-TILELANG-GENERAL-FILL`
- `GPU-P1-TILELANG-EXECUTABLE-LOOP-BODIES`
- `GPU-P1-TILELANG-SPARSE-METAL-FIRST`
- `GPU-P2-TILELANG-DYNAMIC-SHAPE-CONTRACT`
- `GPU-P2-TILELANG-ADVANCED-LAYOUT-LOWERING`

Required gates:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_tilelang_import_broader.py -rs
93 passed in 0.74s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py -rs
29 passed in 35.83s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_metallib_runtime.py -rs
10 passed in 15.56s
```

No full bootstrap, five-GC matrix, GCC suite, or external TileLang runtime was
run for this closure. Existing pcc1/five-GC claims remain limited to the exact
previously evidenced variants.
