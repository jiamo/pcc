# PCC_DEBUG_BOOTSTRAP_TRACE generalized to any module

Date: 2026-08-01

Task: `AUD-P2-SELF-MODULE-SPECIAL-CASES-IN-CODEGEN` (the trace-probe half)

## The gap

`docs/debugging-playbook.md` documents `PCC_DEBUG_BOOTSTRAP_TRACE` as a
general codegen probe. The implementation disagreed: four probe sites were
wrapped in `if self.module.name == "pcc.parse.py_lift":`, so setting the
variable produced nothing for any other module. A documented tool that
silently no-ops for 99% of inputs is worse than no tool — it makes a debug
session conclude "the probe says nothing happens here".

## The change

`pcc/py_frontend/codegen/bootstrap_trace.py` (new) puts the module filter
into the variable itself:

```text
PCC_DEBUG_BOOTSTRAP_TRACE=1                     every module
PCC_DEBUG_BOOTSTRAP_TRACE=pcc.parse.py_lift     that module only
PCC_DEBUG_BOOTSTRAP_TRACE=pcc.parse.,pcc.x      comma-separated prefixes
unset/empty                                     disabled (unchanged)
```

The four hardcoded gates in `call_expression_lowering.py` (3) and
`tuple_zip_lowering.py` (1) now call `bootstrap_trace_enabled(self.module.name)`.
The previous behavior is still reachable — by naming the module — so no
debugging workflow is lost.

## Commands and results

```text
tests/python/test_bootstrap_trace_filter.py (new)   10 passed
  - unset/empty disables; 1/true/yes/on/all/* trace everything
  - a module name restricts to that module; comma-separated prefixes work
  - an AST check fails if `self.module.name == "pcc.parse.py_lift"` returns
    as real code anywhere under pcc/py_frontend/codegen (prose in a docstring
    does not count — the first version of this check flagged its own
    documentation, which is why it parses instead of grepping)

tests/python/test_py_multi_file_compile.py
tests/python/test_py_class_export_schema.py
tests/python/test_libc_import_baseline.py           43 passed, 1 deselected
stage1 bootstrap: S1=0, libc imports still 64
```

## Supported claim

The documented debug variable now does what the documentation says for any
module, with a regression that fails if the name-keyed gate returns.

## Not proven

- The row's other half is untouched: two semantic self-compile special cases
  in `class_gen.py` (`PY_AST_FIELD_NAME_OVERRIDES` keyed on
  `pcc.py_frontend.py_ast`, and the `L1_CODEGEN_HOST_ATTRS` injection keyed on
  layer1) still privilege pcc's own modules by name. Replacing them with an
  explicit per-module export annotation — or arguing why a name-keyed table is
  the honest design — is bootstrap-critical and stays open on the row.
