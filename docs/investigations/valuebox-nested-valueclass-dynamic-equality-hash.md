# Investigation: nested ValueBox dynamic equality and hash

## Status
resolved locally 2026-06-04

## Problem Description
The V2 value-model dynamic boundary should preserve fieldwise behavior for a
non-recursive nested valueclass after it crosses an `Any` / object boundary.
The focused strict self-backend regression for
`Segment(start: Point, end: Point)` shows that boxed nested values do not yet
share the direct payload equality/hash behavior.

This is related to, but distinct from,
`self-backend-nested-valueclass-payload-equality.md`: that resolved direct
payload equality before boxing. The current failure is runtime object dispatch
for boxed `ValueBox` objects.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dynamic_equality_hash_and_type_self_backend \
  -q -n0
```

Observed result before the fix:

```text
stdout before abort:
False
False
True

stderr:
TypeError: unhashable type
...
KeyError
```

The first `False` is wrong: two boxed `Segment(Point(1, 2), Point(3, 4))`
values compare unequal after dynamic boxing. The dict lookup then fails because
the equal-shape key is not considered equal and hash handling reports an
unhashable object on the lookup path.

## Test [CONFIRMED]

The test was added and failed under strict self-backend mode:

```text
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dynamic_equality_hash_and_type_self_backend
  -> failed
```

The compiled binary is produced successfully. The failure is runtime behavior
after boxing, not self-backend native emission.

## Proposals

- No.1 Normalize valueclass constructor returns to ValueBox at Dyn boundaries [CONFIRMED]

## No.1 Normalize valueclass constructor returns to ValueBox at Dyn boundaries

### Code Change

`return_lowering.py` now handles a valueclass constructor returned from an
object/Dyn-annotated function by building the constructor payload with the
expression's own valueclass type before coercing to the declared return type.
The existing `_coerce(... -> Dyn)` path then boxes that payload into a
`ValueBox`.

Before this change, `return Segment(...)` in `def make_dyn(...) -> Any`
emitted a normal object pointer. `_coerce(... -> Dyn)` saw a pointer and
returned it unchanged, so equality compared `ValueBox` from `to_dyn(...)`
against a normal instance from `make_dyn(...)`.

### CONFIRMED

Focused gate after the fix:

```text
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dynamic_equality_hash_and_type_self_backend
  -> 1 passed in 26.94s
```

This confirms that the strict self-backend binary now treats the
`Any`-returned nested valueclass as the same boxed value projection as
`to_dyn(...)`: boxed equality, dict lookup/hash, tuple dynamic storage,
`type(...).__name__`, repr, and typed payload readback all produce the expected
output.

## Report

No.1 landed. The root cause was not the runtime's ValueBox-vs-ValueBox
recursive equality/hash dispatch; it was inconsistent object projection at a
return boundary. A valueclass constructor returned from an `Any`-annotated
function used to be emitted as a normal instance pointer, while the direct
`to_dyn(...)` path emitted a `ValueBox`. Once return lowering builds the
constructor payload with the expression's own valueclass type and lets `_coerce`
box it, both dynamic paths use the same ValueBox projection.

Verification:

```text
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dynamic_equality_hash_and_type_self_backend
  -> failed before the fix with `False`, `False`, `True`, then `TypeError: unhashable type` / `KeyError`
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_dynamic_equality_hash_and_type_self_backend
  -> 1 passed after the fix
tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_returning_dyn_boxes_valuebox_in_return_body
  -> 1 passed
tests/python/data_model/test_value_class_runtime.py
  -> 16 passed
tests/python/test_py_value_class_unboxed.py
  -> 14 passed
V0/V1/status batch
  -> 31 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
py_compile for touched Python files
  -> passed
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  -> 5 passed in 390.02s on the final source state
```

Remaining value-model boundaries are unchanged: recursive valueclasses,
complete V2 marshal coverage, flattened object storage/dispatch, typed arrays,
monomorphization, and full V-track completion remain open.
