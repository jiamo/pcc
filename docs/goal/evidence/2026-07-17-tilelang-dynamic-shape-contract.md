# First bounded TileLang dynamic-shape contract

Date: 2026-07-17

Task: `GPU-P2-TILELANG-DYNAMIC-SHAPE-CONTRACT`

## Proven slice

- `TileLangDynamicShapeContract` accepts one outer-function symbol `N` only in
  the exact first-slice forms `T.Tensor((N,), dtype)` and
  `T.ceildiv(N, threads)`.
- Launch-time specialization validates an inclusive positive bound, uint64
  byte-size multiplication, an explicit maximum buffer size, and uint32 grid
  extent before invoking the strict TileLang source importer.
- Successful specialization produces ordinary fully static Kernel IR. No
  symbolic value is admitted into Kernel IR, TIRx, Metal source, or launch ABI.
- The deterministic cache identity binds contract version, source SHA-256,
  function and symbol names, bounds, resource limits, target, canonical static
  constants, concrete `N`, required bytes, and grid extent.
- `N*N`, `N+1`, using `N` as a fill value, missing shape use, boolean/floating
  values, out-of-bound values, byte overflow, buffer-limit overflow, and grid
  overflow fail closed.

## Gate

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_dynamic_shape_contract.py -rs
```

Result: `13 passed in 0.39s`.

## Claim boundary

This is a contract and static-specialization proof only. It does not prove
runtime dispatch, cache persistence or eviction, multiple symbolic dimensions,
rank-2 symbolic shapes, symbolic arithmetic, dynamic loops, dynamic shared
memory, GPU execution, pcc1 ownership, performance, TVM/TileLang runtime
ownership, or whole-program GPU execution.
