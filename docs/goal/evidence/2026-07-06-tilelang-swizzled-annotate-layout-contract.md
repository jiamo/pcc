# TileLang Swizzled Annotate-Layout Contract Evidence

Date: 2026-07-06

Task: `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

## What Changed

- The TileLang importer now accepts the real TileLang pattern
  `T.annotate_layout({buf: tilelang.layout.make_swizzled_layout(buf)})`.
- The annotation updates the corresponding `LocalBuffer.layout` to
  `swizzled`; it is not lowered as a no-op body operation.
- The accepted form is deliberately strict:
  - the key and `make_swizzled_layout(...)` target must name the same buffer;
  - the target must be a previously allocated shared local buffer;
  - non-default `make_swizzled_layout` options remain unsupported.
- TIRx/plain-TIR freeze and the TVM-shape oracle preserve the swizzled local
  layout metadata, so the contract is visible beyond the importer.
- The CPU oracle can still compute the semantic GEMM reference output for this
  variant.
- Metal source lowering fails closed for swizzled local layouts because pcc
  does not yet have a layout applier that rewrites shared-memory indices.

## Reference Notes

Local TileLang uses this pattern in real code, for example:

```python
T.annotate_layout({
    A_shared: tilelang.layout.make_swizzled_layout(A_shared),
    B_shared: tilelang.layout.make_swizzled_layout(B_shared),
})
```

TVM/TIRx models layout transforms as buffer layout metadata, not as an ordinary
executable op. This slice moves pcc in that direction: the layout belongs to
the local buffer record and survives TIRx/TVM projection.

## Gates

```bash
gtimeout 120s env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/ir.py \
  pcc/kernel_ir/tvm_oracle.py \
  pcc/kernel_ir/tilelang_import.py \
  pcc/kernel_ir/metal_finalize.py \
  pcc/kernel_ir/tilelang_compat.py \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_compat.py
```

Result: passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_tvm_oracle.py
```

Result: 44 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_import_broader.py
```

Result: 52 passed.

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_source_runtime.py \
  tests/kernel/test_metal_tilelang_gemm_runtime.py
```

Result: 18 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_ir.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tvm_oracle.py
```

Result: 28 passed.

```bash
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/kernel
```

Result: 222 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 240s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -n0 \
  tests/gpu_hardware/test_metal_claim_levels.py -rs
```

Result: 3 passed.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import.py \
  tests/kernel/test_tilelang_import_broader.py \
  tests/kernel/test_tvm_oracle.py \
  tests/kernel/test_tilelang_compat.py
```

Result: 69 passed.

```bash
gtimeout 120s env -u LC_ALL uv run python scripts/goal_state.py validate
```

Result: OK: 22 tasks validated.

```bash
gtimeout 120s git diff --check
```

Result: passed.

## Claim Boundary

This proves swizzled `T.annotate_layout` import, Kernel IR local-layout
metadata, TIRx/plain-TIR preservation, TVM-shape oracle projection, CPU
semantic oracle compatibility, and Metal fail-closed behavior. It does not
prove Metal source layout application, shared-memory bank-swizzle index
rewriting, TMA/wgmma layout lowering, runtime-source execution of a swizzled
layout, `.air/.metallib` production, pcc1-native GPU launch, five-GC GPU
lifetime parity, or whole-program GPU execution.
