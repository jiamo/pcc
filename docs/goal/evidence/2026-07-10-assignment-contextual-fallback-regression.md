# Assignment contextual fallback regression

Date: 2026-07-10

Task id: `M0-HEAD-FALLBACK-RATCHET-REGRESSION`

Changed files:

- `pcc/py_frontend/codegen/assignment_statement_lowering.py`
- `tests/python/test_fallback_baseline.py`

Failure boundary:

Current HEAD `6dc2e3c2b55f1390607301ecbb6630550a9015fc` emitted 17
`py_cpy_*` calls when compiling
`pcc.py_frontend.codegen.assignment_statement_lowering` with the ON-mode
contextual L1CodeGen host contract. The new literal self-method dispatch AST
walker called host `dataclasses.is_dataclass()` and `dataclasses.fields()`
inside the self-host compiler closure.

Implementation:

The walker now follows the established lowering-module convention: read field
names from `__dataclass_fields__.keys()` and access each field directly. This
changes only AST traversal mechanics; literal dispatch eligibility and emitted
IR behavior are unchanged.

Gates:

- Focused test before fix -> failed, `17 != 0` in `5.01s`.
- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/python/test_fallback_baseline.py::test_on_mode_assignment_statement_contextual_fallback_zero`
  after fix -> `1 passed in 5.16s`.
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/python/test_native_literal_method_dispatch.py tests/python/test_native_init_field_rhs_type.py::test_class_self_call_argument_types_reach_literal_dispatch_target`
  -> `3 passed in 31.21s`.
- Full fallback/no-libpython rerun after the fix -> 19 passed and one distinct
  failure: legacy scaffold-off `marshal` raw count `341` versus baseline `310`.
  ON-mode contextual checks, including assignment lowering, passed.

Claim:

The assignment-lowering contextual regression is fixed and the literal
self-method dispatch behavior is preserved. No fallback baseline was changed.

Open boundary:

The separate pre-existing/current-HEAD `marshal` scaffold-off raw ratchet
failure is tracked by `M0-HEAD-MARSHAL-RAW-FALLBACK-REGRESSION`. This evidence
does not claim the full fallback gate, bootstrap, or M0 is green.
