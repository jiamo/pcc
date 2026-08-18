# Instance __eq__ wired into container key equality — 2026-08-26

Closes the fix proposed in
`docs/investigations/py-instance-eq-ignored-in-container-keys.md`
(`SEM-P1-INSTANCE-EQ-CONTAINER-KEYS`).

## Change

`py_obj_eq`'s fallthrough (both mirrors) now routes user instances to the
existing tri-state dispatcher instead of returning 0:

- `pcc/py_runtime/src/py_obj_ops_compare.c` — guard
  `ta/tb == PY_TYPE_INSTANCE || >= PY_TYPE_USER_CLASS_START`
  (the canonical instance predicate, py_class.c:112), then
  `py_user_eq_dispatch`; -1 (no `__eq__` / NotImplemented) keeps the
  identity fallback.
- `pcc/py_runtime/py/py_obj_ops_compare.py` — identical guard and call;
  the extern was already declared at line 129, confirming the dispatcher
  had been prepared for exactly this call site and never connected.

First attempt used only `== PY_TYPE_INSTANCE`, which never matches real
user classes (`py_type_of(instance)` is 104 = `PY_TYPE_USER_CLASS_START`,
per-class tags from there) — caught by the collapse repro still printing
len=2, then fixed to the range predicate.

## Gates

```text
instance_eq_collapse (new, all five backends x both mirrors)   10 passed
concurrent eq-key probe (resurrected, backends 0-3 x mirrors)   8 passed
backend 2 stability x3                                          3 x 2 passed
dict/set methods parity                                        23 passed
substrate collect_during + races_ families                     82 passed
test_bootstrap_gate_baseline.py                                 2 passed
```

The resurrected concurrent probe asserts the equality callback itself
observes a concurrent tracer step (independent sampler), so the eq-path
race is proven directly, not inferred from the hash path.

## Backend-4 production contract: observed reds, recorded not attributed

`scripts/run_gc_production_contract.sh` on this tree: backends 0-3 pass;
backend #4 = 2 failed, 10 errors, 164 passed. Decomposition:

- All 10 errors share one root cause at PROGRAM COMPILE stage:
  "self-link mode does not support native-extension export anchors"
  (PCC-PY-COMPILE-001) in test_extension_module_state_roots and
  test_direct_valueclass_pointer_payload setups. This is a fail-closed
  capability diagnostic in the python frontend/link mode; it fires before
  any runtime equality executes and cannot be caused by a runtime
  py_obj_eq edit.
- test_valueclass_pointer_payload_updates_after_optional_relocation[4]:
  relocation assertion; no `__eq__`/`py_obj_eq` involvement in that test.
- vthread_io_waitset_runtime[auto-2]: 30s subprocess timeout
  (load-sensitive shape).

No pre-change backend-4 contract baseline exists on this machine (last
recorded log is July 10). Attribution is therefore OPEN, not assumed:
recorded as new row `GC-P2-BACKEND4-CONTRACT-REDS-BISECT` with next step
= bisect these three clusters against sources preceding today's edits.

## Nonclaims

- The dispatcher honors only the LEFT operand's `__eq__` here (existing
  dispatcher behavior); CPython's reflected-operand dance is not
  implemented. Recorded as scope, silently inherited.
- Recursion depth guard of 64 inherited from the dispatcher.

## Update (same day, later): post-change ratchets

With both runtime mirrors patched, the mandatory commit-level ratchets
re-ran clean on the changed sources:

```text
test_bootstrap_gate_baseline.py                       2 passed
fallback baselines (both files)          40 passed in 510.79s
```

Fallback envelope note: second consecutive measurement above 500s
(previous session run 537.27s, recorded historical baseline 182.73s).
The sub-600s watchdog era for this gate is over on current sources; the
envelope re-record belongs to whichever slice next touches the fallback
path.
