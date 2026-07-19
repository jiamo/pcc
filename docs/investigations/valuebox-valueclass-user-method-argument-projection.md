# Investigation: valueclass constructors in user method arguments should box as ValueBox

## Status
resolved

## Problem Description
The V2 valueclass projection matrix covers direct function arguments,
dynamic callable-object arguments, container stores, and several builtin/method
container boundaries, but pcc-native user method calls with unannotated
parameters were not covered. Code inspection showed
`method_call_lowering._emit_direct_method_call(...)` and adjacent static/direct
pointer method-call branches emitting unannotated method arguments through raw
`_emit_expr(...)` followed by `marshal_to_object(...)`.

That sequence can materialize a valueclass constructor argument as an ordinary
instance before the callee receives a dynamic object, producing
identity/unhashable behavior at later object boundaries.

Predecessors:
- `valuebox-valueclass-dynamic-callable-argument-projection.md`
- `valuebox-valueclass-mutation-store-projection.md`
- `valuebox-valueclass-sequence-builtin-literal-source-projection.md`

## Repro
```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_user_method_argument_projection_boxes_valuebox -q -n0
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_user_method_argument_self_backend -q -n0
```

Expected failing markers before the fix:
- the generated `main` IR still calls `user_value_mod_Segment___init__` for
  `keeper.pick(Segment(...))` / `Keeper.pick_static(Segment(...))` arguments;
- the strict self-backend runtime probe exits with `TypeError: unhashable type`
  and `KeyError` when method-returned values are used as dict keys.

## Test [CONFIRMED]
Both focused regressions were run on 2026-06-04 and failed as expected:

```
AssertionError: assert not <re.Match ... @user_value_mod_Segment___init__ ...>
```

and:

```
TypeError: unhashable type
...
KeyError
```

## Proposals
- No.1 Use the generic PyObject projection helper for unannotated user method arguments     [confirmed]

## No.1 Use the generic PyObject projection helper for unannotated user method arguments
### Code Change
In `pcc/py_frontend/codegen/method_call_lowering.py`, change unannotated
method-call argument emission from raw `_emit_expr(...)` plus
`marshal_to_object(...)` to `_emit_expr_as_pcc_object(...)` for expression-based
method-call branches. Keep annotated-parameter coercion intact.

### CONFIRMED
Confirmed. Missing annotations are resolved to `DynType` by type inference, so
the method-call lowering must treat both missing and `DynType` method parameter
annotations as object boundaries before evaluating the argument. The fix routes
those argument expressions through `_emit_expr_as_pcc_object(...)` in the direct
staticmethod and instance-method branches, while keeping concrete non-Dyn
annotations on the existing `_coerce(...)` path.

Focused gates:

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_user_method_argument_projection_boxes_valuebox -q -n0
# 1 passed in 0.24s

env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_user_method_argument_self_backend -q -n0
# 1 passed in 26.67s
```

Broad gates:

```
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py -q -n0
# 31 passed in 20.21s

env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py -q -n0
# 29 passed in 5.40s

env -u LC_ALL uv run pytest tests/python/test_python_class_features_parity.py -q -n0
# 11 passed in 6.80s

env -u LC_ALL uv run pytest tests/python/test_descriptor_protocol.py -q -n0
# 12 passed in 7.44s

env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_source_shape.py tests/python/data_model/test_value_class_field_flattening.py tests/python/test_value_model_valhalla.py -q -n0
# 46 passed in 6.14s

env -u LC_ALL uv run pytest tests/python/gc_production_contract -q -n0
# 130 passed in 25.82s

env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -n0
# 18 passed in 125.48s

env -u LC_ALL uv run python -m py_compile pcc/py_frontend/codegen/method_call_lowering.py tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_runtime.py
# passed

git diff --check
# passed before docs sync
```

Mandatory bootstrap:

```
env -u LC_ALL uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0
# 5 passed in 429.39s
```

## Report
No.1 landed. The important detail is that missing annotations are already
represented as `DynType` by the time method-call lowering zips call arguments
against the method signature. Calling `_emit_expr(...)` first can therefore
produce an ordinary valueclass instance pointer, and `_coerce(..., DynType)`
passes that pointer through unchanged. Evaluating the expression through
`_emit_expr_as_pcc_object(...)` preserves the intended ValueBox projection at
the object boundary.

Non-claim: this proves the focused unannotated instance-method and staticmethod
argument projection for the non-recursive nested `Segment(start: Point,
end: Point)` shape. It does not prove annotated-method coercion, method
pointer/super/async coverage, recursive valueclasses, complete V2 marshal
coverage, flattened storage, typed arrays, full V-track, or total-goal
completion.
