# GPU-P0-CANONICAL-KERNEL-IR-PATH closure evidence

## Outcome

The legacy direct AST-to-Metal compatibility lowering is removed.
`lower_function_to_metal()` now has one route only:

```text
@gpu.kernel AST -> validated Kernel IR -> plain-TIR freeze -> Metal source
```

The compact vector-add primitive remains. The broader finite syntax previously
accepted by the direct emitter is represented by structured Kernel IR ops for
typed scalar assignment, indexed buffer load/store, arithmetic, comparison,
and lexically scoped nested `if/else/pass`. Expression records are deeply
validated, require exact JSON-shaped fields and complete symbol references,
and reject hidden host objects. TIRx freezes the records to concrete plain-TIR
ops and the Metal finalizer consumes only those frozen records.

Unsupported statements/expressions, non-finite or out-of-range literals, and
non-plain parameter signatures fail closed with `GpuKernelError`; there is no
Metal fallback. TileLang's supported subset already imports to Kernel IR. ds4
remains a separately gated adapter target, not a frontend support claim.

## Gates

- Focused canonical/negative regressions — **5 passed in 0.26s**.
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_ir.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tilelang_import.py tests/python/test_gpu_metal.py`
  — **50 passed in 3.96s**.
- Focused module `py_compile` for the importer, IR validator, TIRx adapter, TVM
  projection, and Metal finalizer — **exit 0**.

No GCC or GC/bootstrap suite was run; this card changes only the GPU frontend
and kernel-only lowering stack, and its task-owned gates execute that boundary.

