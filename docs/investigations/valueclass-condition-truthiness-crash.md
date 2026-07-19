# Investigation: `if Segment(...):` crashed codegen — ClassType truthiness unsupported

## Status
resolved

## Problem Description

Found by the round-2 V2 boundary probe sweep: a valueclass-typed value in
a boolean condition (`if Segment(Point(1, 2), Point(3, 4)):` or
`if s:` for a typed valueclass local) crashed code generation with
`NotImplementedError: Layer 1 cannot compute truthiness of ClassType`
(`coercion_lowering._truthy` had no ClassType branch). CPython: instances
are truthy unless `__bool__`/`__len__` says otherwise.

The same sweep found two more REDs recorded for follow-up slices:
`%`-formatting operands (`"v=%s" % Segment(...)` emits an identity
instance through a print/str-mod path that is NOT the generic binop
dispatcher — a dispatcher-level projection edit was attempted, proved to
be dead code for both the print and assignment forms, and was REVERTED)
and walrus targets (`(s := Segment(...))` emits an identity instance).
Six other candidates were already green (assert-message, user-fn kwargs,
list.append, dict.get default, star-args, default parameter values).

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_constructor_condition_truthiness_self_backend' \
  -q -n0
```

Observed red (2026-06-10): codegen raised
`Layer 1 cannot compute truthiness of ClassType` (probe sweep ERROR row).

## Test [CONFIRMED]

The probe sweep observed the codegen crash; the focused regression above
was added and passes post-fix.

## Proposals
- No.1 Add a ClassType branch to `_truthy` (box payloads, dispatch `py_obj_truthy`)   [CONFIRMED]

## No.1 ClassType truthiness branch

### Code Change

`coercion_lowering._truthy`: before the NotImplementedError tail, a
ClassType value boxes a non-pointer payload via
`_emit_valueclass_payload_to_object` (pointer values pass through) and
dispatches `py_obj_truthy`, which honours user `__bool__`/`__len__`.
Honest note: a DIRECT constructor in condition position is emitted by the
condition emitter before `_truthy` runs, so the degenerate
always-truthy-constant form still allocates an identity instance — the
crash is fixed and values are CPython-correct; full constructor
projection in condition position is recorded as a remaining boundary,
not claimed.

### CONFIRMED

Observed (2026-06-10): focused regression -> 1 passed (prints
truthy/var-truthy); combined V suites -> 81 passed; GC contract -> 130
passed; fallback baselines -> 18 passed; `git diff --check` clean;
five-GC bootstrap matrix -> 5 passed in 426.13s.

## Report

Proposal No.1 landed. Remaining enumerated boundaries from the two probe
sweeps: `%`-format operands (needs the print/str-mod emission site, not
the binop dispatcher), walrus targets, condition-position constructor
projection. Not total-goal completion.

## Update (2026-06-10): %-format operand slice landed (same sweep family)

The `%`-format RED is fixed and bootstrap-verified. Root cause located:
`str % valueclass` was hijacked by the REFLECTED-dunder route in
`expr_dispatch_lowering` (`__rmod__` dynamic receiver — materializing the
constructor as an identity instance); valueclasses define no user
`__rmod__`. Fix: the reflected route now skips `op == "%"` with a
valueclass-payload rhs, falling through to the binop emission where the
rhs is projected via `_emit_expr_as_pcc_object`. The earlier
dispatcher-level projection edit (reverted as dead code) became live once
the hijack was removed. IR-guard note: the %-lowering does not use
`py_str_mod` for this shape (payload/format-helper path) — the guard pins
the essential contract only (valuebox present, no identity ctor calls).

Observed: focused pair -> 2 passed (runtime prints
`v=Segment(start=Point(x=1, y=2), end=Point(x=3, y=4))`, byte-equal to
the CPython frozen-dataclass analog); combined V suites -> 83 passed; GC
contract -> 130; fallback baselines -> 18; `git diff --check` clean;
five-GC bootstrap matrix -> 5 passed in 461.34s.

Remaining enumerated boundaries: walrus targets, condition-position
constructor projection.

## Update (2026-06-10): walrus-target slice landed — the probe-sweep boundary set is fully dispositioned

`(s := Segment(...))` emitted an identity instance through
`stmt_misc_lowering._emit_walrus`'s plain `_emit_expr`. Fix mirrors the
plain-assignment lowering: `_maybe_emit_valueclass_constructor_payload`
first, so the LOCAL stores a payload; because the walrus EXPRESSION is
Dyn-typed, the surrounding-expression value returns the BOXED payload
(`_emit_valueclass_payload_to_object`) — the first attempt returned the
raw payload struct and crashed Dyn marshaling
(`marshal_to_object: DynType with IR {{i64,i64},{i64,i64}}`), which the
focused tests caught immediately.

Observed: focused pair -> 2 passed (payload local field reads `5` +
`type(s).__name__` == `Segment`; IR guard: no ctor calls, no
`py_instance_new`); combined V suites -> 85 passed; GC contract -> 130;
fallback baselines -> 18; `git diff --check` clean; five-GC bootstrap
matrix -> 5 passed in 464.98s.

Sweep disposition complete: truthiness crash FIXED, %-format FIXED,
walrus FIXED; condition-position direct-ctor projection remains the one
recorded leftover (degenerate always-truthy form, identity allocation
only, values correct).
