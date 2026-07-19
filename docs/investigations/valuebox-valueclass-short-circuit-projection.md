# Investigation: valueclass constructors in short-circuit expressions should box as ValueBox

## Status
resolved locally 2026-06-04

## Problem Description
V2 valueclass constructor projection covers `Any` returns, dynamic
locals/literals, module globals, mutation stores, comprehensions, call
arguments, dynamic callable-object arguments, dynamic attribute stores, and
conditional expressions. The adjacent short-circuit path still evaluates
`flag or Segment(...)` and `obj and Segment(...)` through `_emit_boolexpr(...)`
before the object-boundary ValueBox projection. That can leave ordinary
identity-instance semantics or raw valueclass payloads at an `Any` / dynamic
object boundary.

Predecessor investigations:

- `docs/investigations/valuebox-valueclass-local-container-projection.md`
- `docs/investigations/valuebox-valueclass-comprehension-projection.md`
- `docs/investigations/valuebox-valueclass-dynamic-callable-argument-projection.md`
- `docs/investigations/valuebox-valueclass-attribute-store-projection.md`
- `docs/investigations/valuebox-valueclass-conditional-expression-projection.md`

## Repro
Focused IR-shape repro:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_short_circuit_projection_boxes_valuebox -q -n0
```

Observed result before the fix: generated `main` contains `bool.rhs` /
`bool.end`, but has no `py_valuebox_new`, proving the short-circuit object
boundary does not project valueclass constructor arms into boxed ValueBox
objects.

Focused strict self-backend runtime repro:

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_short_circuit_projection_self_backend -q -n0
```

Observed result before the fix: strict `--backend self --python-libpython=off
--ir-scaffold=on` compilation succeeds, but the produced binary prints:

```text
False
False
True
```

then fails with `TypeError: unhashable type` / `KeyError` at dynamic dict
lookup, matching ordinary identity-instance semantics instead of boxed
ValueBox equality/hash semantics.

## Test [CONFIRMED]
Both focused tests above were run and observed failing on 2026-06-04 before
the source fix.

## Proposals
- No.1 Route object-boundary short-circuit expressions through boxed arm phis     [CONFIRMED]

## No.1 Route object-boundary short-circuit expressions through boxed arm phis
### Code Change
Add a short-circuit object projection helper that evaluates the left operand
once, tests truthiness, projects the returned left or right operand through
`_emit_expr_as_pcc_object(...)`, and joins with a `PyObject*` phi. Use it at
object-boundary sites so dynamic `and` / `or` expressions select boxed
ValueBox objects instead of materializing ordinary identity instances or
forming valueclass payload phis.

### CONFIRMED
`compare_membership_lowering.py` now has an object-boundary short-circuit
helper that projects the selected operand through `_emit_expr_as_pcc_object`
and joins with a `PyObject*` phi. `exact_int_lowering._emit_expr_as_pcc_object`
recognizes `BoolExpr`, and `assignment_statement_lowering.py` routes `Any` /
object-target `BoolExpr` assignments through the object helper.

Focused gates after the fix:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_short_circuit_projection_boxes_valuebox
  -> 1 passed
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_short_circuit_projection_self_backend
  -> 1 passed
```

The IR guard confirms `main` now emits the object-boundary short-circuit blocks
`bool.obj.rhs` / `bool.obj.end`, calls `py_valuebox_new` /
`py_valuebox_set_field`, and avoids `py_instance_new`, valueclass `__init__`
calls, and `extractvalue ptr`.

Broad validation and mandatory full self bootstrap are confirmed in the report
below.

## Report

No.1 landed. The root cause was the short-circuit expression's direct operand
join at an object boundary: `_emit_boolexpr(...)` evaluated `and` / `or` arms
through raw expression lowering, so `Any` assignment could receive ordinary
identity instances or raw valueclass payloads instead of boxed ValueBox
objects. Object-boundary short-circuit expressions now evaluate the left
operand once, use runtime truthiness on the boxed left object, project the
selected operand through `_emit_expr_as_pcc_object(...)`, and join with a
`PyObject*` phi.

Verification:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_short_circuit_projection_boxes_valuebox
  -> failed before the fix with no `py_valuebox_new`; passed after the fix
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_short_circuit_projection_self_backend
  -> failed before the fix with identity-like equality/hash and `KeyError`; passed after the fix
tests/python/data_model/test_value_class_runtime.py
  -> 25 passed
tests/python/test_py_value_class_unboxed.py
  -> 23 passed
V0/V1/status batch
  -> 40 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
py_compile for touched Python files
  -> passed
git diff --check
  -> passed before bootstrap
local ignored-doc trailing-whitespace check
  -> passed
bootstrap: passed (tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -> 5 passed in 413.29s)
residual process check
  -> empty
```

Remaining value-model boundaries are unchanged: recursive valueclasses,
complete V2 marshal coverage, flattened object storage/dispatch, typed arrays,
monomorphization, typed-int projection repair, and full V-track completion
remain open.
