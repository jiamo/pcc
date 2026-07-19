# Investigation: valueclass constructors in dict builtin keyword values should box as ValueBox

## Status
resolved locally; focused slice bootstrap-verified

## Problem Description
V2 valueclass constructor projection covers dict literals and several dynamic
escape boundaries, but builtin `dict(...)` keyword construction still stores
keyword values through raw expression lowering. `dict(left=Segment(...))` and
`dict(base, left=Segment(...))` should store boxed ValueBox objects, not
ordinary identity instances or raw payloads.

Predecessor investigations:

- `docs/investigations/valuebox-valueclass-local-container-projection.md`
- `docs/investigations/valuebox-valueclass-mutation-store-projection.md`
- `docs/investigations/valuebox-valueclass-comprehension-projection.md`
- `docs/investigations/valuebox-valueclass-dynamic-callable-argument-projection.md`
- `docs/investigations/valuebox-valueclass-conditional-expression-projection.md`
- `docs/investigations/valuebox-valueclass-short-circuit-projection.md`

## Repro
Focused IR-shape repro:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dict_builtin_keyword_projection_boxes_valuebox -q -n0
```

Observed result before the fix: generated `main` reaches `dict.new`, but has
no `py_valuebox_new`.

Focused strict self-backend runtime repro:

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dict_builtin_keyword_projection_self_backend -q -n0
```

Observed result before the fix: strict `--backend self --python-libpython=off
--ir-scaffold=on` compilation succeeds, the binary prints:

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
- No.1 Route builtin dict keyword values through object-boundary projection     [CONFIRMED]

## No.1 Route builtin dict keyword values through object-boundary projection
### Code Change
Change `_emit_dict_builtin(...)` keyword-value insertion loops to emit keyword
values through `_emit_expr_as_pcc_object(...)`, matching dict literal value
projection. Keep the existing source-dict copy behavior unchanged.

### CONFIRMED
`literal_lowering._emit_dict_builtin(...)` now emits keyword values through
`_emit_expr_as_pcc_object(...)` in both keyword-only `dict(k=v)` and
source-plus-keyword `dict(src, k=v)` paths.

Focused gates after the fix:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_dict_builtin_keyword_projection_boxes_valuebox
  -> 1 passed
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dict_builtin_keyword_projection_self_backend
  -> 1 passed
```

The IR guard confirms `main` now reaches `dict.new`, calls `py_valuebox_new` /
`py_valuebox_set_field`, and avoids `py_instance_new`, valueclass `__init__`
calls, and `extractvalue ptr`.

Broader validation and mandatory bootstrap on the final source state:

```text
tests/python/data_model/test_value_class_runtime.py
  -> 26 passed
tests/python/test_py_value_class_unboxed.py
  -> 24 passed
tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_source_shape.py tests/python/data_model/test_value_class_field_flattening.py tests/python/test_value_model_valhalla.py
  -> 41 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
py_compile for touched Python lowering/runtime/test files
  -> passed
git diff --check
  -> passed before bootstrap
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  -> 5 passed in 400.82s
residual process check
  -> empty
```

This resolves only the focused builtin `dict(...)` keyword-value projection
boundary for the non-recursive nested valueclass shape. It does not prove full
V2 marshal coverage, recursive valueclasses, flattened storage, typed arrays,
full V-track completion, or total goal completion.
