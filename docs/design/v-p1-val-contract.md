# V-P1-VAL finite contract (M3 value model)

Decomposes goal task `V-P1-VAL` into implementable sub-slices with crisp
done-conditions, so the task moves from `TODO_NEEDS_DESIGN` to `TODO_READY`.
Source of truth for current state: `docs/reports/value-model-v0-v6-status.md`.

## Already DONE (verify before re-doing — do not re-implement)

- V0: `ValueClassType`, `@pcc.valueclass`, inference marking, frozen
  dataclass-compatible lowering, unsupported-form diagnostics.
- V1 scalar payload: aggregate payload for scalar-field valueclasses (local
  assign / field read via `extractvalue` / direct arg+return ABI / method
  receiver), fieldwise `==`/`!=`, selected box→pcc-instance and unbox-back with
  `TypeError` on wrong dynamic object, non-recursive nested payload
  (`Segment(Point, Point)`) direct call/return + recursive-leaf compare;
  recursive/mutually-recursive payload graphs rejected pre-codegen.
- Semantic-int projection (exit criterion 1, LEAVE half): typed-int unboxed
  i64 overflow promotes to bignum on LLVM + self backends — landed
  2026-06-17. Regressions: `tests/python/test_native_typed_int_overflow.py`,
  `tests/security/test_py_integer_safety.py`. (INT-P0-PROJ.)
- Partial V2: `py_valuebox_new/get_field/set_field`, selected object-pointer
  payload fields, constructor escape→`Any`/object via valuebox boxing, boxed
  eq/hash, GC-aware field/class loads, a 5-GC-locked nested ValueBox
  pointer-payload root shape, and broad constructor-projection `Any`-boundary
  coverage (see status report for the exact enumerated shapes).

## OPEN sub-slices (the finite V-P1-VAL contract)

Ordered; each closes with its listed gate. Do NOT weaken value/identity
semantics to pass a gate.

- **VP-S1 — range-proven raw-lane re-entry.** Prove int values can ENTER a raw
  machine lane on a proven range and safely LEAVE it (deopt/promote to bignum,
  never wrap) at every re-entry point, not just the arithmetic-overflow path
  already covered. Done: a raw-lane local that escapes to a dyn/object boundary
  or crosses a call re-boxes to a Python int with no wrap; IR-shape gate shows
  the deopt edge. Gate: `tests/python/test_native_typed_int_overflow.py` plus a
  new focused raw-lane-escape regression.
- **VP-S2 — complete self-backend scalar/aggregate valueclass ABI + recursive
  pointer-payload slot descriptions.** Extend the payload ABI beyond the
  currently-selected direct call/return sites to the full scalar+aggregate
  surface, and describe recursive pointer-bearing payloads through the ONE
  unified slot schema (`py_obj_visit_slots`/`py_obj_update_slot`) so all 5 GC
  backends trace them identically. Done: an IR-shape gate shows no
  `py_obj_alloc`/`py_instance_new` across the typed direct-call bodies in scope,
  and a pointer-bearing recursive payload is traced/updated under GC0..4. Gate:
  the value-class focused tests + a 5-GC root-shape regression.
- **VP-S3 — identity-escape completeness.** Every identity escape of a value
  payload either boxes (valuebox) or emits a stable identity-escape diagnostic —
  close the gap from "selected boundaries" to "every boundary". Done: an escape
  the current coverage misses is either boxed or produces the documented
  diagnostic (no silent identity leak, no `is`/`weakref`/`__dict__` on a raw
  payload). Gate: value-class runtime tests + a focused escape-diagnostic
  regression.

## Closing gate for DONE_STRONG (all three slices)

```
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_value_class_unboxed.py \
  tests/python/data_model/test_value_class_runtime.py
# then the bootstrap-class matrix (local CPU; no model cost):
gtimeout 900s env -u LC_ALL uv run pytest -q \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py ... gc4.py
```

DONE_STRONG requires all three sub-slices closed with their gates AND the 5-GC
bootstrap matrix green. Ordinary classes keep identity; value classes stay
opt-in identity-free payloads; `int` stays arbitrary-precision semantic with a
value-lane projection that promotes on overflow.
