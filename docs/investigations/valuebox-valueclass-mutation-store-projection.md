# Investigation: valueclass constructors in mutation stores should box as ValueBox

## Status
resolved locally 2026-06-04

## Problem Description
The V2 value-model dynamic boundary should project valueclass constructor
results consistently whenever the value escapes to an object-shaped context.
`valuebox-valueclass-local-container-projection.md` fixed `Any` locals and
tuple/list/dict literals, and the following module-global slice locked a
module-level `Any` boundary. The adjacent mutation-store boundary is still
open: a valueclass constructor stored into an existing list or dict through
`list.append(...)`, list subscript assignment, or dict subscript assignment
should store a boxed `ValueBox`, not an ordinary identity instance.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_mutation_store_projection_boxes_valuebox \
  -q -n0
```

Observed result before the fix:

```text
AssertionError: assert not <re.Match object; ... match='call ptr (ptr) @py_instance_new'>
```

The generated `main` IR calls `py_valuebox_new` for some constructor escapes,
but still contains `py_instance_new`, confirming that at least one
post-creation mutation-store path lowers a `Segment(...)` constructor through
ordinary identity-instance construction.

A strict self-backend runtime reproducer is also present:

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_mutation_store_projection_self_backend \
  -q -n0
```

Observed result before the fix:

```text
AssertionError: assert ['False', '99', 'Segment', ...] == ['True', '99', 'Segment', ...]
```

`items[0] == items[1]` is `False` after `items[1] = Segment(...)`, while the
dict/hash and typed readback checks still print the expected values. Treat
this as the runtime symptom of the unprojected list replacement value until
the IR projection path is fixed and retested.

## Test [CONFIRMED]

Both focused tests were added and failed:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_mutation_store_projection_boxes_valuebox
  -> failed, `main` still contains `py_instance_new`
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_mutation_store_projection_self_backend
  -> failed at runtime because `items[0] == items[1]` printed `False`
```

The first failing boundary is missing mutation-store projection in frontend
lowering. The runtime equality failure should not be debugged as a ValueBox
dispatch bug until the store path stores the same projection shape.

## Proposals

- No.1 Normalize valueclass constructor expressions before list/dict mutation stores [CONFIRMED]

## No.1 Normalize valueclass constructor expressions before list/dict mutation stores

### Code Change
`subscript_lowering._emit_subscript_store(...)` now emits the RHS expression
through `_emit_expr_as_pcc_object(...)` before passing it to
`_emit_subscript_store_value(...)`. That reuses the same object-boundary
projection path as literal containers, so `items[1] = Segment(...)` and
`mapping["seg"] = Segment(...)` box valueclass constructor payloads as
`ValueBox` objects before runtime storage.

`list_method_lowering._list_method_box(...)` now uses
`_emit_expr_as_pcc_object(...)` instead of raw `_emit_expr(...)` plus
`marshal_to_object(...)`. That gives `list.append(Segment(...))` the same
ValueBox projection behavior.

### CONFIRMED

Focused gates after the fix:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_mutation_store_projection_boxes_valuebox
  -> 1 passed
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_mutation_store_projection_self_backend
  -> 1 passed
```

The IR guard confirms `main` now calls `py_valuebox_new` /
`py_valuebox_set_field`, avoids `py_instance_new` and valueclass `__init__`
calls for constructor projections, and no longer emits `extractvalue ptr`.
The strict self-backend runtime test now compiles and runs, proving equality,
hash/dict lookup, list append/list replacement/dict store, type name, repr, and
typed readback for the focused `Segment(start: Point, end: Point)` shape.

## Report

No.1 landed. The root cause was the same projection family as the prior
`Any` return and literal-container bugs, but at post-creation mutation stores:
subscript assignment and list method argument boxing emitted valueclass
constructor expressions through raw `_emit_expr(...)` plus `marshal_to_object`,
which can construct an ordinary identity instance before the object boundary.
Routing those expression boundaries through `_emit_expr_as_pcc_object(...)`
reuses the existing payload-to-ValueBox bridge.

Verification:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_mutation_store_projection_boxes_valuebox
  -> failed before the fix with residual `py_instance_new`; passed after the fix
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_mutation_store_projection_self_backend
  -> failed before the fix with `items[0] == items[1]` printing `False`; passed after the fix
tests/python/data_model/test_value_class_runtime.py
  -> 19 passed
tests/python/test_py_value_class_unboxed.py
  -> 17 passed
V0/V1/status batch
  -> 34 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
py_compile for touched Python files
  -> passed
git diff --check
  -> passed
bootstrap: passed (tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -> 5 passed in 386.25s)
residual process check
  -> empty
```

Remaining value-model boundaries are unchanged: recursive valueclasses,
complete V2 marshal coverage, flattened object storage/dispatch, typed arrays,
monomorphization, typed-int projection repair, and full V-track completion
remain open.
