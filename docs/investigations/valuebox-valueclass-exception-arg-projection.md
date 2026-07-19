# Investigation: valueclass constructor exception-message arguments bypass object projection

## Status
resolved

## Problem Description

Second RED from the four-boundary probe sweep that produced the
compare-operand slice (`valuebox-valueclass-compare-operand-projection.md`
is the sibling/predecessor). `raise ValueError(Segment(...))` lowers the
builtin-exception message through
`exception_lowering._build_exception_value._message_cstr`, which emitted a
non-StrLit first argument via `_emit_as_object` — a direct valueclass
constructor materialized as a legacy identity instance before `py_obj_str`.

Scope note (honest): pcc's builtin-exception model is message-only (no
live `e.args[0]` object is preserved), and the identity instance's str()
output happened to EQUAL the valuebox str() output, so runtime VALUES were
already CPython-equal — the focused runtime probe passed before the fix.
The slice's payoff is allocation-model consistency (no identity-instance
allocation for valueclass constructors anywhere in the contract) plus the
IR-shape guard.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_exception_arg_projection_boxes_valuebox' \
  'tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_exception_arg_projection_self_backend' \
  -q -n0
```

Observed red (2026-06-10): IR guard failed with
`call ... @user_value_mod_Segment___init__` in `@main`; the runtime probe
already printed the CPython-equal message
`Segment(start=Point(x=1, y=2), end=Point(x=3, y=4))`.

## Test [CONFIRMED]

IR guard observed red under the command above (the runtime probe was
already green — recorded as such, not claimed as a behavior fix).

## Proposals
- No.1 Route the exception message argument through `_emit_expr_as_pcc_object(...)`   [CONFIRMED]

## No.1 Route the exception message argument through `_emit_expr_as_pcc_object(...)`

### Code Change

One line in `_message_cstr`: the non-StrLit message argument now emits via
`_emit_expr_as_pcc_object` (direct constructors project to boxed
valueboxes; all other expressions marshal as before).

### CONFIRMED

Observed (2026-06-10): focused pair -> 2 passed; full unboxed guard -> 39
passed; full valueclass runtime -> 41 passed; V batch -> 56 passed; GC
contract -> 130 passed; fallback baselines -> 18 passed; `py_compile` +
`git diff --check` clean; five-GC bootstrap matrix -> 5 passed in 406.16s.

## Report

Proposal No.1 landed. The four-boundary probe sweep is now fully
dispositioned: f-string interpolation GREEN (pre-existing), generator
`yield` GREEN (pre-existing), compare operands FIXED (sibling file),
exception-message args FIXED (this file). Known remaining V2 boundaries
stay as listed in the audit (weak-dict key policy as a design question,
recursive valueclasses, flattened storage, typed arrays). Not total-goal
completion.
