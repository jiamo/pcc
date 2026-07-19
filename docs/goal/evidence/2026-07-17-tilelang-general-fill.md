# GPU-P1 TileLang general fill evidence — 2026-07-17

Mode: pcc TileLang source-subset AST importer -> Kernel IR -> plain TIRx ->
pcc runtime-source Metal. No TileLang/TVM runtime import or execution.

Implemented contract:

- `T.fill(buffer, static_finite_literal)` preserves the literal in Kernel IR
  and `tir.fill_loop`;
- CPU oracle and Metal emitter consume one shared POD coercion contract:
  bool is distinct, integers are range checked, and f16/f32/f64 values are
  finite and quantized to their destination representation;
- Metal source emits explicit destination constructors such as
  `half(1.099609375)` and `float(-3.25)`;
- a static global-buffer shape supplies the fill extent/guard, preventing the
  tail threads of a partial threadgroup from writing out of bounds;
- non-static, non-finite, keyword, and unrepresentable literals fail closed
  before device execution.

Device evidence:

- 3x5 f32 fill with `-3.25`: real command buffer, completed fence, exact CPU
  oracle readback;
- 3x5 f16 fill with source `1.1`: explicit half quantization to
  `1.099609375`, real command buffer, exact CPU oracle readback;
- both release native allocations after completion.

Gates:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_general_fill.py tests/kernel/test_metal_tilelang_general_fill_runtime.py -rs
8 passed in 3.01s

gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_finalize.py
8 passed in 0.34s
```

This is not dynamic fill, expression-valued fill, arbitrary-rank fill,
TileLang runtime execution, pcc1, five-GC device parity, or a performance claim.
Real device proof in this slice is f16/f32 only.
