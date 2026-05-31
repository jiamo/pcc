# Investigation: PCC_PYTHON_IR_PASSES=on should stay bounded on huge modules

## Status

active

## Problem Description

`PCC_PYTHON_IR_PASSES=off` is only a bounded bootstrap default. It does not
fix the explicit `PCC_PYTHON_IR_PASSES=on` performance path. The `on` spelling
expands to the fast default preset:

```text
mem2reg, sroa, early-cse, instsimplify, function-attrs, adce, dce
```

The single-module path auto-selects the memory transport for this preset.
Before this slice, the huge-module skip guard did not apply to memory
transport because `_skip_status_for_pass()` returned early for memory
transport before checking `_skip_for_huge_module(...)`.

That made `on` able to send huge IR modules into the LLVM memory pipeline even
though the text transport already treated the same huge-module fast preset as
skipped.

## Code Change

`pcc/py_frontend/ir_pass_pipeline.py` now applies the huge-module skip before
the memory-transport early return:

```text
skip_unsafe
skip_huge
memory transport normal path
medium / large text-only skip guards
```

This keeps explicit `all` / `full` behavior intact. In memory transport,
`all` still expands to `default<O2>` and remains an explicit optimization
request. The bounded skip applies to the `on` / `default` fast preset on huge
modules.

## Validation

Focused new regression:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 120 \
  uv run pytest tests/python/test_py_frontend_ir_pass_pipeline.py::test_huge_module_default_memory_transport_skips_fast_preset_without_parse -q -n0
```

Observed result: `1 passed in 0.23s`.

Related IR-pass policy group:

```bash
env -u LC_ALL -u LC_CTYPE /usr/bin/perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pytest \
    tests/python/test_py_frontend_ir_pass_pipeline.py::test_python_ir_pass_default_fast_auto_selects_memory_transport \
    tests/python/test_py_frontend_ir_pass_pipeline.py::test_huge_module_default_skips_fast_preset_without_parse \
    tests/python/test_py_frontend_ir_pass_pipeline.py::test_huge_module_default_memory_transport_skips_fast_preset_without_parse \
    tests/python/test_py_frontend_ir_pass_pipeline.py::test_python_ir_pass_memory_transport_all_uses_llvm_default_o2 \
    tests/python/test_py_frontend_ir_pass_pipeline.py::test_python_ir_pass_memory_transport_keeps_llvm_default_on_large_modules \
    -q -n0
```

Observed result: `5 passed in 0.52s`.

## Open Boundary

This is a bounded-policy optimization for huge modules under
`PCC_PYTHON_IR_PASSES=on` / `default`. It does not prove full bootstrap speed,
does not optimize explicit `all` / `full`, and does not remove the need to
profile individual slow IR passes.
