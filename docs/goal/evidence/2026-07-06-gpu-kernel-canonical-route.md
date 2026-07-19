# GPU Kernel Canonical Kernel IR Route

Date: 2026-07-06

Task: `GPU-P0-CANONICAL-KERNEL-IR-PATH`

## What Changed

- Added `pcc.gpu_kernel.lower_function_to_kernel_ir(...)` for the first
  `@gpu.kernel` vector-add subset.
- Added Kernel IR / TIRx support for the new `elementwise_add` primitive.
- Added Metal source lowering for frozen `tir.elementwise_add`.
- Changed `lower_function_to_metal(...)` to try the Kernel IR route first for
  the supported vector-add shape, then use the old direct AST-to-Metal lowering
  only as a compatibility fallback.
- Added a regression proving the vector-add source imports to Kernel IR before
  Metal emission.

## Claim Scope

This closes the first `@gpu.kernel` canonical-route slice only:

```text
@pcc.gpu.kernel vector-add subset
  -> Kernel IR
  -> TIRx/plain-TIR freeze
  -> Metal source
```

It does not claim that all `@gpu.kernel` syntax is now Kernel IR-backed. The
legacy direct AST-to-Metal fallback still exists for compatibility while
unsupported shapes are migrated deliberately.

## Validation

```text
env -u LC_ALL uv run python -m py_compile \
  pcc/gpu_kernel.py \
  pcc/kernel_ir/ir.py \
  pcc/kernel_ir/tirx_adapter.py \
  pcc/kernel_ir/metal_finalize.py \
  tests/python/test_gpu_metal.py
passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gpu_metal.py::test_gpu_kernel_vector_add_imports_to_kernel_ir_before_metal \
  tests/python/test_gpu_metal.py::test_gpu_kernel_source_stripping_removes_device_only_surface
2 passed in 0.23s

env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_metal_finalize.py
15 passed in 0.05s

env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_kernel_ir.py \
  tests/kernel/test_tirx_adapter.py \
  tests/kernel/test_tilelang_import.py \
  tests/python/test_gpu_metal.py
35 passed, 5 skipped in 0.40s
```

## Remaining Boundary

This is `DONE_WEAK`, not `DONE_STRONG`.

Open work:

- Broader `@gpu.kernel` syntax still needs Kernel IR semantics before the
  legacy fallback can be removed.
- The old direct AST-to-Metal lowering still exists as a compatibility fallback.
- ds4 adapters are not implemented.
- `.air/.metallib`, pcc1 no-libpython launch, and five-GC GPU lifetime parity
  remain separate tasks.
