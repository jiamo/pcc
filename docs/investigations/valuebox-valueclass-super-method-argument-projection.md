# Investigation: valueclass constructors in super method arguments should box as ValueBox

## Status
resolved

## Problem Description
The V2 valueclass projection matrix covers direct function arguments, dynamic
callable-object arguments, direct pcc-native user method arguments, and several
container object boundaries, but pcc-native parent method calls reached through
`super()` were not covered. Code inspection showed
`method_call_lowering._emit_static_method_ptr_call(...)` and
`_emit_direct_method_ptr_call(...)` emitting unannotated method-pointer
arguments through raw `_emit_expr(...)` followed by `marshal_to_object(...)`.

That sequence can materialize a valueclass constructor argument as an ordinary
instance before the parent method receives a dynamic object, producing
identity/unhashable behavior at later object boundaries.

Predecessors:
- `valuebox-valueclass-user-method-argument-projection.md`
- `valuebox-valueclass-dynamic-callable-argument-projection.md`
- `valuebox-valueclass-sequence-builtin-literal-source-projection.md`

## Repro
```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_super_method_argument_projection_boxes_valuebox -q -n0
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_super_method_argument_self_backend -q -n0
```

Expected failing markers before the fix:
- generated IR still calls `user_value_mod_Segment___init__` for
  `super().pick(Segment(...))` / `super().pick_static(Segment(...))` arguments;
- the strict self-backend runtime probe exits with `TypeError: unhashable type`
  and `KeyError` when `super()`-returned values are used as dict keys.

## Test [CONFIRMED]
Both focused regressions were run on 2026-06-05 and failed as expected:

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
- No.1 Use the generic PyObject projection helper for unannotated super method-pointer arguments     [confirmed]

## No.1 Use the generic PyObject projection helper for unannotated super method-pointer arguments
### Code Change
In `pcc/py_frontend/codegen/method_call_lowering.py`, change unannotated /
`DynType` argument emission in `_emit_static_method_ptr_call(...)` and
`_emit_direct_method_ptr_call(...)` from raw `_emit_expr(...)` plus
`marshal_to_object(...)` to `_emit_expr_as_pcc_object(...)`. Keep concrete
non-Dyn annotations on the existing `_coerce(...)` path.

### CONFIRMED
Confirmed. The direct method-call branches had already been changed to treat
missing and `DynType` parameter annotations as object boundaries, but the
method-pointer branches used by `super()` still evaluated the argument first
with `_emit_expr(...)`. That allowed valueclass constructors to become ordinary
instances before `_coerce(..., DynType)` or `marshal_to_object(...)` saw them.

The fix applies the same object-boundary rule to the static and direct
method-pointer branches: missing / `DynType` method-pointer arguments are
emitted through `_emit_expr_as_pcc_object(...)`; concrete non-Dyn annotations
keep the existing `_coerce(...)` path.

Focused gates:

```
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_super_method_argument_projection_boxes_valuebox -q -n0
# 1 passed in 0.23s

env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_super_method_argument_self_backend -q -n0
# 1 passed in 26.58s
```

Broad gates:

```
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py -q -n0
# 32 passed in 21.59s

env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py -q -n0
# 30 passed in 5.44s

env -u LC_ALL uv run pytest tests/python/test_python_class_features_parity.py -q -n0
# 11 passed in 9.00s

env -u LC_ALL uv run pytest tests/python/test_descriptor_protocol.py -q -n0
# 12 passed in 9.26s

env -u LC_ALL uv run pytest tests/python/test_self_host_oracle_diff.py -k super -q -n0
# 45 passed, 352 deselected in 113.10s

env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_source_shape.py tests/python/data_model/test_value_class_field_flattening.py tests/python/test_value_model_valhalla.py -q -n0
# 47 passed in 6.47s

env -u LC_ALL uv run pytest tests/python/gc_production_contract -q -n0
# 130 passed in 24.94s

env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py -q -n0
# 18 passed in 119.58s

env -u LC_ALL uv run python -m py_compile pcc/py_frontend/codegen/method_call_lowering.py tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_runtime.py
# passed

git diff --check
# passed before docs sync
```

Mandatory bootstrap:

```
env -u LC_ALL uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0
# 5 passed in 417.47s
```

## Report
No.1 landed. The important detail is that `super()` dispatch reaches
`_emit_static_method_ptr_call(...)` or `_emit_direct_method_ptr_call(...)`,
not the direct method-call branches fixed in the previous slice. Applying the
same missing/Dyn annotation object-boundary rule there makes valueclass
constructor arguments box as `ValueBox` before the parent method receives
them.

Non-claim: this proves the focused unannotated `super()` instance-method and
staticmethod method-pointer argument projection for the non-recursive nested
`Segment(start: Point, end: Point)` shape. It does not prove annotated-method
coercion, all method-pointer sources, async method-value calls, recursive
valueclasses, complete V2 marshal coverage, flattened storage, typed arrays,
full V-track, or total-goal completion.
