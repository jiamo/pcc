# TileLang executable scheduled-loop bodies

Date: 2026-07-17

Task: `GPU-P1-TILELANG-EXECUTABLE-LOOP-BODIES`

## Proven slice

- The strict TileLang importer accepts a canonical indexed assignment inside
  `T.Parallel(M, N)` when every buffer index is exactly `(i, j)`.
- It accepts the corresponding one-dimensional `T.vectorized(N)` form with
  `buffer[i]` or the explicitly shaped `buffer[0, i]` spelling.
- Right-hand sides may contain same-index buffer loads, finite numeric
  literals, and `+`, `-`, `*`, or `/`. Non-canonical indices, scalar-target
  assignment, and unsupported expressions fail closed.
- Kernel IR/TIRx records the static flattened extent and indexed expression;
  the CPU oracle and Metal source use that same bounded operation.
- Real runtime-source Metal execution and device readback match the CPU oracle
  for a 3x5 f32 parallel add and a seven-element f32 vectorized scale.

## Gates

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_executable_loop_bodies.py \
  tests/kernel/test_metal_tilelang_executable_loop_runtime.py -rs
```

Result: `8 passed in 4.17s`.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py
```

Result: `64 passed in 0.85s`.

## Claim boundary

This proves only the static canonical indexed elementwise subset above under
host Python import, CPU oracle execution, and runtime-source Metal execution.
It does not prove arbitrary TileLang loop bodies, broadcasting, dynamic
extents, reductions, nested scheduled loops, non-POD values, TVM/TileLang
runtime ownership, pcc1-native launch, prebuilt metallib execution, five-GC
parity, performance, or whole-program GPU execution.
