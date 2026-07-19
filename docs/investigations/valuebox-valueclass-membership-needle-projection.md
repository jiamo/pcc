# Investigation: valueclass constructors in membership needles should box as ValueBox

## Status
resolved

## Problem Description
V2 valueclass constructor projection covers many object-boundary positions, but
direct constructors used as membership needles still bypass the object-boundary
projection helper. In `Segment(...) in items`, `Segment(...) in table`, or
`Segment(...) in tuple_items`, membership lowering currently emits the needle
with raw `_emit_expr(expr.lhs)` and only then calls
`_emit_value_as_pcc_object_or_bridge(...)`. For direct valueclass constructors,
that can materialize an ordinary user instance before the ValueBox projection
has a chance to run.

Adjacent predecessor investigations:

- `valuebox-valueclass-local-container-projection.md`
- `valuebox-valueclass-mutation-store-projection.md`
- `valuebox-valueclass-comprehension-projection.md`
- `valuebox-valueclass-conditional-expression-projection.md`
- `valuebox-valueclass-short-circuit-projection.md`
- `valuebox-valueclass-dataclasses-replace-keyword-projection.md`

## Repro

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_membership_needle_projection_boxes_valuebox -q -n0
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_membership_needle_self_backend -q -n0
```

## Test [CONFIRMED]
The focused IR guard fails because generated `main` still contains a call to
`user_value_mod_Segment___init__` for direct `Segment(...)` membership needles.

The strict self-backend runtime test compiles, prints:

```text
False
True
False
False
```

then raises `TypeError: unhashable type` followed by `KeyError` when the direct
constructor is used as a dict lookup key. That is ordinary identity-instance
behavior, not boxed ValueBox equality/hash behavior.

## Proposals
- No.1 Route membership needles through object-boundary projection [CONFIRMED]
- No.2 Route dict subscript lookup keys through object-boundary projection [CONFIRMED]

## No.1 Route membership needles through object-boundary projection
### Code Change
In `compare_membership_lowering._emit_membership(...)`, emit non-string
membership needles with `_emit_expr_as_pcc_object(expr.lhs)` at the object
boundary instead of raw `_emit_expr(expr.lhs)` followed by
`_emit_value_as_pcc_object_or_bridge(...)`. Keep the existing specialized string
membership path unchanged because it expects a pcc string/substring object.
For tuple literal membership, update `_emit_membership_tuple_literal(...)` so
literal elements are emitted through `_emit_expr_as_pcc_object(...)` before
`py_obj_eq`.

### CONFIRMED
After the code change, the focused IR guard passes:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_membership_needle_projection_boxes_valuebox -q -n0
```

Observed result: `1 passed`.

The strict self-backend runtime regression now prints:

```text
True
True
True
True
```

before failing at the downstream `table[Segment(...)]` lookup with
`TypeError: unhashable type` / `KeyError`. That confirms membership needles
are fixed and separates a second object-boundary failure: direct valueclass
constructors used as dict subscript lookup keys.

## No.2 Route dict subscript lookup keys through object-boundary projection
### Code Change
`subscript_lowering.py` now has `_emit_subscript_key_object(...)`, which first
checks direct valueclass constructor payloads and boxes them as `ValueBox`
objects, then falls back to the previous `_emit_as_object(...)` behavior. Both
the ordinary subscript load path and the object-form subscript helper in
`exact_int_lowering.py` use this helper for dict/object getitem keys.

### CONFIRMED
After the code change, the strict self-backend runtime regression passes:

```bash
env -u LC_ALL uv run pytest tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_membership_needle_self_backend -q -n0
```

Observed result: `1 passed`. The regression now proves list/dict/tuple
membership plus downstream dict lookup with a direct `Segment(...)` key.

## Report
Proposal No.1 and No.2 landed together because the downstream-sensitive runtime
regression intentionally used a direct valueclass constructor as a dict lookup
key after proving list/dict/tuple membership. The first fix corrected
membership needles but exposed the second getitem-key boundary; the second fix
made both the ordinary dict/object getitem path and the object-form subscript
helper use the same direct-constructor ValueBox projection.

Final validation for the focused slice:

- Focused IR guard -> 1 passed.
- Focused strict self-backend runtime regression -> 1 passed.
- Full valueclass runtime -> 34 passed.
- Full `test_py_value_class_unboxed.py` -> 32 passed.
- V0/V1/status batch -> 49 passed.
- Native subscript/format-map touched-path batch -> 3 passed.
- Full GC common contract -> 130 passed.
- Fallback/no-libpython baselines -> 18 passed.
- Touched-file `py_compile` passed.
- `tests/python/test_value_model_valhalla.py` -> 4 passed.
- Mandatory full self bootstrap -> 5 passed in 436.50s.

This resolves only direct valueclass constructor membership needles and
dict/object subscript getitem keys for the non-recursive nested
`Segment(start: Point, end: Point)` shape. It does not prove complete
membership protocol support, recursive valueclasses, complete V2 marshal
coverage, flattened storage, typed arrays, full V-track, or total-goal
completion.
