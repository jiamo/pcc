# Investigation: valueclass constructors in dict update keyword values should box as ValueBox

## Status
resolved locally; focused slice bootstrap-verified

## Problem Description
V2 valueclass constructor projection covers dict literals, dict subscript
stores, and builtin `dict(...)` keyword construction, but dict method
`update(...)` keyword construction still stores keyword values through raw
expression lowering. `mapping.update(left=Segment(...))` and
`mapping.update(source, left=Segment(...))` should store boxed ValueBox
objects, not ordinary identity instances or raw payloads.

Predecessor investigations:

- `docs/investigations/valuebox-valueclass-mutation-store-projection.md`
- `docs/investigations/valuebox-valueclass-dict-builtin-keyword-projection.md`

## Repro
Focused IR-shape repro:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dict_update_keyword_projection_boxes_valuebox -q -n0
```

Observed result before the fix: generated `main` reaches native
`py_dict_set`, but has no `py_valuebox_new`.

Focused strict self-backend runtime repro:

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dict_update_keyword_projection_self_backend -q -n0
```

Observed result before the fix: strict `--backend self --python-libpython=off
--ir-scaffold=on` compilation succeeds, but the binary fails with
`TypeError: unhashable type` while constructing or looking up a dict keyed by
the updated value, matching ordinary identity-instance semantics instead of
boxed ValueBox equality/hash semantics.

## Test [CONFIRMED]
Both focused tests above were run and observed failing on 2026-06-04 before
the source fix.

## Proposals
- No.1 Route dict method object arguments through object-boundary projection     [CONFIRMED]

## No.1 Route dict method object arguments through object-boundary projection
### Code Change
Change the dict method argument boxing helper to emit values through
`_emit_expr_as_pcc_object(...)`, matching the projection used by dict literals,
builtin `dict(...)` keyword values, mutation stores, comprehensions, call
arguments, attributes, conditionals, and short-circuit expressions.

### CONFIRMED
`dict_lowering._dict_method_box(...)` now emits method argument values through
`_emit_expr_as_pcc_object(...)`.

Focused gates after the fix:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dict_update_keyword_projection_boxes_valuebox
  -> 1 passed
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dict_update_keyword_projection_self_backend
  -> 1 passed
```

The IR guard confirms `main` now reaches native `py_dict_set`, calls
`py_valuebox_new` / `py_valuebox_set_field`, and avoids `py_instance_new`,
valueclass `__init__` calls, and `extractvalue ptr`.

Broader validation and mandatory bootstrap on the final source state:

```text
tests/python/data_model/test_value_class_runtime.py
  -> 27 passed
tests/python/test_py_value_class_unboxed.py
  -> 25 passed
tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_source_shape.py tests/python/data_model/test_value_class_field_flattening.py tests/python/test_value_model_valhalla.py
  -> 42 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
py_compile for touched Python lowering/runtime/test files
  -> passed
git diff --check
  -> passed before bootstrap
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  -> 5 passed in 408.70s
residual process check
  -> empty
```

This resolves only the focused dict method `update(...)` keyword-value
projection boundary for the non-recursive nested valueclass shape. It does not
prove full V2 marshal coverage, recursive valueclasses, flattened storage,
typed arrays, full V-track completion, or total goal completion.
