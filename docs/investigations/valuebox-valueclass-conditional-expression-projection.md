# Investigation: valueclass constructors in conditional expressions should box as ValueBox

## Status
resolved locally 2026-06-04

## Problem Description
V2 valueclass constructor projection already covers `Any` returns, dynamic
locals/literals, module globals, mutation stores, comprehensions, call
arguments, dynamic callable-object arguments, and dynamic attribute stores. The
adjacent conditional-expression path still evaluates `Segment(...) if cond
else Segment(...)` arms before the object-boundary ValueBox projection. That can
leave either ordinary identity-instance construction or a nested valueclass
payload `phi` at a dynamic/object boundary.

Predecessor investigations:

- `docs/investigations/valuebox-nested-valueclass-dynamic-equality-hash.md`
- `docs/investigations/valuebox-valueclass-local-container-projection.md`
- `docs/investigations/valuebox-valueclass-mutation-store-projection.md`
- `docs/investigations/valuebox-valueclass-comprehension-projection.md`
- `docs/investigations/valuebox-valueclass-dynamic-callable-argument-projection.md`
- `docs/investigations/valuebox-valueclass-attribute-store-projection.md`

## Repro
Focused IR-shape repro:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_conditional_expr_projection_boxes_valuebox -q -n0
```

Observed result before the fix: generated `main` contains the dynamic
`ternary_true` / `ternary_false` blocks and some `py_valuebox_new` calls, but
still contains residual `py_instance_new`.

Focused strict self-backend runtime repro:

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_conditional_expr_projection_self_backend -q -n0
```

Observed result before the fix: strict `--backend self --python-libpython=off
--ir-scaffold=on` compilation fails during self-backend native emission with
unsupported nested aggregate phi:

```text
self backend does not support instruction in 'main'/'ternary_end...':
phi { { i64, i64 }, { i64, i64 } } ...
```

## Test [CONFIRMED]
Both focused tests above were run and observed failing on 2026-06-04 before the
source fix.

## Proposals
- No.1 Route object-boundary conditional expressions through boxed arm phis     [confirmed]

## No.1 Route object-boundary conditional expressions through boxed arm phis
### Code Change
Add a conditional-expression object projection helper that emits the condition
diamond, projects each selected arm through `_emit_expr_as_pcc_object(...)`,
and joins with a `PyObject*` phi. Use it at object-boundary sites so dynamic
conditional expressions select boxed ValueBox objects instead of forming a
nested valueclass payload aggregate phi or materializing ordinary identity
instances.

### Result
Confirmed for the focused IR/runtime regressions on 2026-06-04:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_conditional_expr_projection_boxes_valuebox -q -n0
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_conditional_expr_projection_self_backend -q -n0
```

Result: 1 passed + 1 passed.

Broad non-bootstrap gates confirmed on 2026-06-04:

- `tests/python/data_model/test_value_class_runtime.py` -> 24 passed
- `tests/python/test_py_value_class_unboxed.py` -> 22 passed
- V0/V1/status batch -> 39 passed
- `tests/python/gc_production_contract` -> 130 passed
- fallback/no-libpython baselines -> 18 passed
- touched-file `py_compile` -> passed

## Report

No.1 landed. The root cause was the conditional expression's direct payload
join: `_emit_if_expr(...)` formed a nested valueclass payload phi before the
surrounding dynamic/object assignment could box it, and selected arms could
still contain ordinary identity-instance construction. Object-boundary
conditional expressions now use a dedicated helper that projects each arm
through `_emit_expr_as_pcc_object(...)` and joins the selected objects with a
`PyObject*` phi. `Any` assignment of a valueclass-typed conditional expression
routes through that object helper.

Verification:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_conditional_expr_projection_boxes_valuebox
  -> failed before the fix with residual `py_instance_new`; passed after the fix
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_conditional_expr_projection_self_backend
  -> failed before the fix with unsupported nested aggregate payload phi; passed after the fix
tests/python/data_model/test_value_class_runtime.py
  -> 24 passed
tests/python/test_py_value_class_unboxed.py
  -> 22 passed
V0/V1/status batch
  -> 39 passed
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
bootstrap: passed (tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -> 5 passed in 408.92s)
residual process check
  -> empty
```

Remaining value-model boundaries are unchanged: recursive valueclasses,
complete V2 marshal coverage, flattened object storage/dispatch, typed arrays,
monomorphization, typed-int projection repair, and full V-track completion
remain open.
