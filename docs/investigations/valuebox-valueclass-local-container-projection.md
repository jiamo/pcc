# Investigation: valueclass constructors in Any locals and containers should box as ValueBox

## Status
resolved locally 2026-06-04

## Problem Description
The V2 value-model dynamic boundary should project valueclass constructor
results consistently whenever the value escapes to an object-shaped context.
The previous nested ValueBox dynamic return fix normalized `return
Segment(...)` from an `Any`-annotated function. The adjacent local/container
boundary is still open: `local: Any = Segment(...)` and tuple/list/dict
literals containing `Segment(...)` should store boxed `ValueBox` objects rather
than ordinary class instances.

This is related to, but distinct from,
`valuebox-nested-valueclass-dynamic-equality-hash.md`: that fixed `Any` return
projection. The current failure is assignment and literal-container projection.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_local_and_container_dyn_projection_boxes_valuebox \
  -q -n0
```

Observed result before the fix:

```text
AssertionError: assert None
where None = re.search('\\bcall\\b[^\\n]*@py_valuebox_new\\b', main_ir)
```

The generated `main` IR for the focused program contains no
`py_valuebox_new`, so the valueclass constructor is not projected through the
ValueBox path at the local/container boundary.

A stricter runtime reproducer is also present:

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_local_and_container_projection_self_backend \
  -q -n0
```

Observed result before the fix:

```text
pcc.backend.BackendUnavailable:
aggregate member requested on non-aggregate void*
```

Treat this as a downstream self-backend symptom until the IR projection is
fixed and retested.

## Test [CONFIRMED]

Both focused tests were added and failed:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_local_and_container_dyn_projection_boxes_valuebox
  -> failed, no py_valuebox_new in main IR
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_local_and_container_projection_self_backend
  -> failed during strict self-backend native emission
```

The first failing boundary is missing assignment/literal projection, not
ValueBox equality/hash runtime dispatch.

## Proposals

- No.1 Normalize valueclass constructor expressions before object-shaped assignment and container stores [CONFIRMED]

## No.1 Normalize valueclass constructor expressions before object-shaped assignment and container stores

### Code Change

`assignment_statement_lowering.py` now uses the expression's own valueclass
type as the constructor-payload target when the assignment target is object/Dyn.
That makes `local: Any = Segment(...)` build the same payload first and then
use the existing coercion path to box it as a `ValueBox`.

`literal_lowering.py` now gives tuple/list/dict element stores the same
constructor-payload opportunity before falling back to ordinary `_emit_expr`.
The existing `_emit_value_as_pcc_object_or_bridge` path then boxes the payload
for container storage.

`exact_int_lowering._emit_expr_as_pcc_object()` now boxes valueclass payloads
when an expression is explicitly emitted for an object boundary.

The focused runtime test also exposed a second, downstream problem:
valueclass-typed names can hold pointer-shaped boxed values after `Any`
assignment. `compare_membership_lowering._emit_valueclass_payload_eq()` now
checks the actual IR shape and uses `py_obj_eq` when either side is already a
pointer, avoiding invalid `extractvalue ptr ...` IR while preserving direct
fieldwise equality for non-pointer payloads.

### CONFIRMED

Focused gates after the fix:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_local_and_container_dyn_projection_boxes_valuebox
  -> 1 passed
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_local_and_container_projection_self_backend
  -> 1 passed
```

The IR guard confirms `main` now calls `py_valuebox_new` /
`py_valuebox_set_field`, avoids `py_instance_new` and valueclass `__init__`
calls for the constructor projections, and no longer emits `extractvalue ptr`.
The strict self-backend runtime test now compiles and runs, proving equality,
hash/dict lookup, tuple/list/dict storage, type name, repr, and typed readback
for the focused `Segment(start: Point, end: Point)` shape.

## Report

No.1 landed. The root cause was the same projection family as the prior
`Any` return bug, but at different escape sites: assignment and literal
container lowering emitted valueclass constructor expressions through ordinary
object instantiation unless the target itself was a valueclass payload. After
normalizing constructor expressions to payload first, the existing
payload-to-object bridge creates the correct `ValueBox`.

The strict runtime regression also exposed an adjacent frontend IR-shape bug:
the static type of a name can remain valueclass-shaped while the actual value
stored through an `Any` boundary is a pointer-shaped `ValueBox`. Direct payload
equality must not run on that pointer. The landed comparison fix keeps
fieldwise equality for non-pointer payloads and delegates pointer-shaped
valueclass equality to `py_obj_eq`.

Verification:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_local_and_container_dyn_projection_boxes_valuebox
  -> failed before the fix with no `py_valuebox_new`; passed after the fix
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_local_and_container_projection_self_backend
  -> failed before the fix with invalid `extractvalue ptr`; passed after the fix
tests/python/data_model/test_value_class_runtime.py
  -> 17 passed
tests/python/test_py_value_class_unboxed.py
  -> 15 passed
V0/V1/status batch
  -> 32 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
py_compile for touched Python files
  -> passed
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  -> 5 passed in 353.91s on the final source state
```

Remaining value-model boundaries are unchanged: recursive valueclasses,
complete V2 marshal coverage, flattened object storage/dispatch, typed arrays,
monomorphization, typed-int projection repair, and full V-track completion
remain open.
