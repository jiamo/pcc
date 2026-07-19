# Investigation: valueclass constructors in comprehensions should box as ValueBox

## Status
resolved locally 2026-06-04

## Problem Description
The V2 value-model dynamic boundary should project valueclass constructor
results consistently whenever the value escapes to an object-shaped context.
`valuebox-valueclass-local-container-projection.md` fixed `Any` locals and
tuple/list/dict literals, and
`valuebox-valueclass-mutation-store-projection.md` fixed post-creation list/dict
mutation stores. The adjacent comprehension boundary is still open:
valueclass constructors stored by list and dict comprehensions should be boxed
as `ValueBox` objects, not ordinary identity instances.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_comprehension_projection_boxes_valuebox \
  -q -n0
```

Observed result before the fix:

```text
AssertionError: assert not <re.Match object; ... match='call ptr (ptr) @py_instance_new'>
```

The generated `main` IR calls `py_valuebox_new` for already-fixed escapes, but
still contains `py_instance_new`, confirming that at least one comprehension
element/value/key path lowers a `Segment(...)` constructor through ordinary
identity-instance construction.

A strict self-backend runtime reproducer is also present:

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_comprehension_projection_self_backend \
  -q -n0
```

Observed result before the fix:

```text
TypeError: unhashable type
```

The error occurs at the dict-key comprehension using `Segment(...)`, before
the equality/type/repr/readback checks can run. Treat this as the runtime
symptom of the unprojected comprehension value until the IR projection path is
fixed and retested.

## Test [CONFIRMED]

Both focused tests were added and failed:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_comprehension_projection_boxes_valuebox
  -> failed, `main` still contains `py_instance_new`
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_comprehension_projection_self_backend
  -> failed at runtime with `TypeError: unhashable type`
```

The first failing boundary is missing comprehension projection in frontend
lowering. The runtime hash failure should not be debugged as a ValueBox hash
bug until the comprehension path stores the same projection shape.

## Proposals

- No.1 Normalize valueclass constructor expressions before comprehension stores [CONFIRMED]

## No.1 Normalize valueclass constructor expressions before comprehension stores

### Code Change
`expr_helper_lowering._emit_comprehension_innermost(...)` now emits list/set
elements and dict keys/values through `_emit_expr_as_pcc_object(...)` before
storing them in the runtime container. That reuses the same object-boundary
projection path as literal containers and mutation stores, so `Segment(...)`
constructors in comprehensions box as `ValueBox` objects.

### CONFIRMED

Focused gates after the fix:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_comprehension_projection_boxes_valuebox
  -> 1 passed
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_comprehension_projection_self_backend
  -> 1 passed
```

The IR guard confirms `main` now calls `py_valuebox_new` /
`py_valuebox_set_field`, avoids `py_instance_new` and valueclass `__init__`
calls for constructor projections, and no longer emits `extractvalue ptr`.
The strict self-backend runtime test now compiles and runs, proving equality,
hash/dict lookup, list-comprehension storage, dict-value comprehension storage,
dict-key comprehension storage, type name, repr, and typed readback for the
focused `Segment(start: Point, end: Point)` shape.

## Report

No.1 landed. The root cause was the same projection family as the prior
`Any` return, literal-container, mutation-store, and module-global bugs, but at
comprehension stores: `_emit_comprehension_innermost(...)` emitted list/set
elements and dict keys/values through raw `_emit_expr(...)` plus
`marshal_to_object(...)`, which can construct an ordinary identity instance
before the object boundary. Routing those expression boundaries through
`_emit_expr_as_pcc_object(...)` reuses the existing payload-to-ValueBox bridge.

Verification:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_comprehension_projection_boxes_valuebox
  -> failed before the fix with residual `py_instance_new`; passed after the fix
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_comprehension_projection_self_backend
  -> failed before the fix with dict-key comprehension `TypeError: unhashable type`; passed after the fix
tests/python/data_model/test_value_class_runtime.py
  -> 20 passed
tests/python/test_py_value_class_unboxed.py
  -> 18 passed
V0/V1/status batch
  -> 35 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
py_compile for touched Python files
  -> passed
git diff --check
  -> passed before bootstrap
bootstrap: passed (tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -> 5 passed in 371.40s)
residual process check
  -> empty
```

Remaining value-model boundaries are unchanged: recursive valueclasses,
complete V2 marshal coverage, flattened object storage/dispatch, typed arrays,
monomorphization, typed-int projection repair, and full V-track completion
remain open.
