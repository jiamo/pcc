# Investigation: weakref on a valueclass payload — identity-escape gap closed at compile time

## Status
resolved (compile-time diagnostic; dynamic-path runtime rejection recorded as follow-up)

## Problem Description

The V-track design question "weak-dict key policy" reduced to a probe:
what does `weakref.ref(Pt(1, 2))` do today for a `@pcc.valueclass`?
Observed under strict no-libpython self-backend: it SUCCEEDED — the
constructor projected to a payload, the object-boundary projection
boxed it, and the runtime created a weakref to the ValueBox
(`weakref-ok 1`). That is an identity-semantics theft: value
projections are identity-free (north-star obligation 7), weak
references observe identity LIFETIME, and the box created at the call
boundary has an unpredictable lifetime (any later boxing makes a NEW
box, so `r()` can die at an arbitrary point). The CPython analogue is
`weakref.ref(3)` -> TypeError.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_value_class_unboxed.py::test_valueclass_weakref_rejected_at_compile_time' -q -n0
```

Observed pre-fix (2026-06-10): the strict-mode binary printed
`weakref-ok 1`. The probe's first cut also exercised `r() is p` and
was correctly stopped by the EXISTING identity-comparison diagnostic —
evidence the `is` guard works; the weakref hole was beside it.

## Test [CONFIRMED]

Pre-fix success observed; post-fix the same source fails to compile
with `cannot create a weak reference to a valueclass payload`.

## Proposals
- No.1 Compile-time diagnostic beside the `is` identity-escape check   [CONFIRMED]

## No.1 Compile-time diagnostic

### Code Change

`type_infer.py` Call branch: a statically-known valueclass first
argument to `weakref.ref` / `weakref.proxy` (Attr form on the
`weakref` module name) raises the frontend error
"cannot create a weak reference to a valueclass payload" with a hint
to use an ordinary class — same mechanism and placement family as the
`is` diagnostic in the Compare branch.

### CONFIRMED

Observed (2026-06-10 late night): probe now rejected at compile time;
pin test 1 passed; V suites 87 passed; fallback baselines 18
(type_infer is a self-host closure module — dialect clean); five-GC
matrix on this source state recorded in `docs/current-goal-state.md`.

## Report

Recorded follow-ups, not claimed: (a) the DYNAMIC path — a ValueBox
reaching `weakref.ref` through a Dyn variable — still succeeds at
runtime; the runtime-side rejection (TypeError on PY_TYPE_VALUEBOX in
the weakref constructor, both tiers + 5-GC gates) is a separate slice.
(b) WeakKeyDictionary/WeakValueDictionary keys/values via the cpy or
native paths are part of the same family. (c) from-import
`from weakref import ref` is not covered by the Attr-form match
(narrow on purpose; the bare name `ref` is too generic to match
safely) — record, revisit if real code hits it.

## Update: dynamic-path runtime rejection landed (bootstrap-verified)

Follow-up (a) is closed: `py_weakref_new` rejects PY_TYPE_VALUEBOX
targets with TypeError ("cannot create weak reference to a valueclass
payload") in BOTH tiers (C + pcc-Python port, mirrored). Two
development findings:

1. **Stale port object**: the first default-tier probe still printed
   `weakref-ok` because the OBJDIR_PY `py_weakref.o` predated the port
   edit — deleting `libpy_runtime*.a` alone is NOT enough when a port
   .py changes; the cached .o must be invalidated too (extends the
   recorded stale-archive lesson).
2. **Missing err-check at the emission site**: with the runtime raise
   in place, the binary printed `weakref-ok` AND THEN an uncaught
   traceback — the native `weakref.ref`/`weakref.proxy` lowering
   (`native_weakref.py`) never emitted `_emit_post_call_err_check`, so
   the pending exception skipped the enclosing try/except and surfaced
   at a later check point. Both sites now check (the classic
   "no Itanium unwinding — miss the check and exceptions teleport"
   failure class from the runtime-holes investigation).

Observed: Dyn-path probe prints `typeerror` (caught by try/except) on
BOTH tiers == CPython; new parametrized regression
`tests/python/test_valueclass_weakref_runtime.py` (port + cc) + the
compile-time pin -> 3 passed; GC contract (incl. weakref bricks) 130;
fallback 18; five-GC matrix -> 5 passed in 543.00s.

## Update: Weak*Dictionary family closed (matrix pending at write time)

Follow-up (b): WeakKeyDictionary keys and WeakValueDictionary values
INHERIT the `py_weakref_new` rejection automatically (the runtime set
paths construct their weakref through it), and the probe showed the
SAME missing-err-check class at the subscript-store emission sites
(`subscript_lowering.py` weak-dict branches printed `set-ok` then an
uncaught double traceback). Both branches now emit
`_emit_post_call_err_check`. Observed: probe prints
`typeerror / vtypeerror` rc=0 on both tiers == CPython
(`d[3] = 5` analogue); parametrized regression
`test_weak_dicts_reject_valueclass_payloads` (port + cc); weakref
suite 4 passed; GC contract 130; fallback 18; five-GC matrix result
recorded in `docs/current-goal-state.md`. Remaining family item:
from-import `ref` form only (recorded, deliberately narrow).
