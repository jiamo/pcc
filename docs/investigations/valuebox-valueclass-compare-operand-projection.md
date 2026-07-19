# Investigation: valueclass constructor compare operands bypass object projection

## Status
resolved

## Problem Description

V2 escape-boundary continuation (predecessors: the subscript store-key
family, membership needles, builtin object-boundary). The generic
object-vs-object compare branch in
`compare_membership_lowering.py::_emit_compare` emits both operands with
plain `_emit_expr` + `marshal_to_object`, so a DIRECT valueclass
constructor used as a comparison operand against an object-typed value —
`Segment(Point(1, 2), Point(3, 4)) == other` where `other: Any` —
materializes as a legacy identity instance
(`user_value_mod_Segment___init__`) instead of a boxed valuebox, breaking
value-equality semantics (CPython frozen-dataclass analog: `s == 1` is
False via `__eq__`, `s == same_value` is True).

Discovered by an IR probe sweep over four candidate boundaries
(f-string interpolation and generator `yield` were already green;
exception-constructor arguments `ValueError(Segment(...))` are a second
RED recorded for a follow-up slice).

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_compare_operand_projection_boxes_valuebox' \
  'tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_compare_operand_projection_self_backend' \
  -q -n0
```

Observed red (2026-06-10): IR guard shows
`call ... @user_value_mod_Segment___init__` in `@main` with no
`py_valuebox_new` for the compare operands.

## Test [CONFIRMED]

Both focused regressions above observed red under the listed command.

## Proposals
- No.1 Route the object-vs-object compare operands through `_emit_expr_as_pcc_object(...)`   [CONFIRMED]

## No.1 Route the object-vs-object compare operands through `_emit_expr_as_pcc_object(...)`

### Code Change

In the object-vs-object branch of `_emit_compare` (the `py_obj_eq` /
`py_obj_lt/le/gt/ge` delegation), replace the two
`_emit_expr` + conditional `marshal_to_object` emissions with
`self._emit_expr_as_pcc_object(expr.lhs/rhs)`, the same projection helper
used by every closed escape-boundary slice (it intercepts direct
valueclass constructors and boxes the payload; all other expressions
marshal exactly as before).

### CONFIRMED

Recorded after the gates below ran green (same day):

- Focused pair -> 2 passed (probe prints False/True/True/False/False,
  equal to the CPython frozen-dataclass ground truth).
- Full `tests/python/test_py_value_class_unboxed.py` -> 38 passed.
- Full `tests/python/data_model/test_value_class_runtime.py` -> 40 passed
  (halves 14 + 26).
- V0/V1/status batch -> 55 passed.
- GC common contract -> 130 passed; fallback baselines -> 18 passed;
  `py_compile` + `git diff --check` clean.
- Mandatory five-GC bootstrap matrix -> 5 passed (time recorded in
  `docs/current-goal-state.md`).

## Report

Proposal No.1 landed. Remaining enumerated sibling from the probe sweep:
exception-constructor arguments (`ValueError(Segment(...))`). This does
not prove complete escape-boundary coverage, recursive valueclasses, or
total-goal completion.
