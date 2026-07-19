# Investigation: valueclass constructors in attribute stores should box as ValueBox

## Status
resolved locally 2026-06-04

## Problem Description
V2 valueclass constructor projection already covers `Any` returns, dynamic
locals/literals, module globals, mutation stores, comprehensions, direct call
arguments, and dynamic callable-object call arguments. The adjacent dynamic
attribute-store path still materializes stored values through raw
`marshal_to_object(...)` sites. That can turn a valueclass constructor payload
into ordinary identity instance semantics instead of a ValueBox before
`py_obj_setattr`.

Predecessor investigations:

- `docs/investigations/valuebox-nested-valueclass-dynamic-equality-hash.md`
- `docs/investigations/valuebox-valueclass-local-container-projection.md`
- `docs/investigations/valuebox-valueclass-mutation-store-projection.md`
- `docs/investigations/valuebox-valueclass-comprehension-projection.md`
- `docs/investigations/valuebox-valueclass-dynamic-callable-argument-projection.md`

## Repro
Focused IR-shape repro:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_attribute_store_projection_boxes_valuebox -q -n0
```

Observed result before the fix: the generated `main` reaches
`py_obj_setattr`, but the IR has no `py_valuebox_new` in the attribute-store
path.

Focused strict self-backend runtime repro:

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_attribute_store_projection_self_backend -q -n0
```

Observed result before the fix: strict `--backend self --python-libpython=off
--ir-scaffold=on` compilation succeeds, the binary prints `False`, `False`,
`True`, then raises `TypeError: unhashable type` / `KeyError` at
`table[holder.same]`. The equality/hash behavior is identity-like instead of
ValueBox fieldwise equality/hash.

## Test [CONFIRMED]
Both focused tests above were run and observed failing on 2026-06-04 before the
source fix.

## Proposals
- No.1 Route dynamic attribute stores through object-boundary projection     [confirmed]

## No.1 Route dynamic attribute stores through object-boundary projection
### Code Change
Use the existing `_emit_expr_as_pcc_object(...)` / valueclass constructor
projection path for pcc-native dynamic attribute-store values that are not
CPython fallback values. This should reuse the ValueBox projection logic
already used for locals/literals, mutation stores, comprehensions, and call
arguments.

### Result
Confirmed for the focused IR/runtime regressions on 2026-06-04:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_attribute_store_projection_boxes_valuebox -q -n0
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_attribute_store_projection_self_backend -q -n0
```

Result: 1 passed + 1 passed.

Broad non-bootstrap gates confirmed on 2026-06-04:

- `tests/python/data_model/test_value_class_runtime.py` -> 23 passed
- `tests/python/test_py_value_class_unboxed.py` -> 21 passed
- V0/V1/status batch -> 38 passed
- `tests/python/gc_production_contract` -> 130 passed
- fallback/no-libpython baselines -> 18 passed
- touched-file `py_compile` -> passed

## Report

No.1 landed. The root cause was another raw object-boundary marshal path:
ordinary attribute assignment evaluated `Segment(...)` through `_emit_expr(...)`
before boxing, so the constructor could materialize as an identity instance.
`_emit_attr_store(...)` now checks valueclass constructor expressions before
generic attribute stores and projects the payload to a ValueBox. The builtin
`setattr(..., value)` path now routes the value through
`_emit_expr_as_pcc_object(...)`, so the same projection applies there.

Verification:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_attribute_store_projection_boxes_valuebox
  -> failed before the fix with no `py_valuebox_new`; passed after the fix
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_attribute_store_projection_self_backend
  -> failed before the fix with identity-like equality/hash and `KeyError`; passed after the fix
tests/python/data_model/test_value_class_runtime.py
  -> 23 passed
tests/python/test_py_value_class_unboxed.py
  -> 21 passed
V0/V1/status batch
  -> 38 passed
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
bootstrap: passed (tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -> 5 passed in 413.63s)
residual process check
  -> empty
```

Remaining value-model boundaries are unchanged: recursive valueclasses,
complete V2 marshal coverage, flattened object storage/dispatch, typed arrays,
monomorphization, typed-int projection repair, and full V-track completion
remain open.
