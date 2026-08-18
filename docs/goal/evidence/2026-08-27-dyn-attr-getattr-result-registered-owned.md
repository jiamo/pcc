# DynType attribute reads: py_obj_getattr result registered as owned

Date: 2026-08-27
Task: `PY-P1-ATTR-GETATTR-OWNED-VALUE-UNREGISTERED` (the dyn-getattr row)
Claim level: host llvm-backend, red-first canary regressions green under
PCC_GC_BACKEND=0,1,2,3,4; closure equal-arm green; 83-test frontend sweep
green. NO stage/bootstrap claim (kernel-lane source-stability deferral).

## Fix

`attr_load_lowering` generic getattr site (the one measured to run for dyn
receivers in the 2026-08-25 investigation):

- object arm: `self._note_owned_dynamic_call_value(result)` — the emitter
  registers the NEW reference, exactly the mechanism the dynamic-call and
  exact-int paths already use; consumers release through
  `_gc_release_if_owned`, whose registry check overrides the AST classifier
  that answers not-owned for `Attr`.
- scalar arm: release the boxed result after `marshal_from_object` (marshal
  only unpacks; the 8-25 file proved it contains zero releases).

Neither denied approach was retried: the scalar-branch-only patch (measured
unreachable for dyn) and classifier-at-consumer (measured no-op) stay dead.

## Red-first regressions (tests/python/test_native_attr_getattr_ownership.py)

```text
print(o.c) dyn receiver            canary dies exactly once   was: leaked (0)
o.inner.c chain                    intermediate released      was: leaked (0)
typed field read (borrowed path)   NO new release, 1 del      stays green
x = o.c; x = None                  strict xfail -> P0 ledger row
```

The fourth shape is real and REMAINS OPEN: dyn local slots use the BORROWED
frame map with plain stores (verified in IR: `store ptr %attr.c -> %x.addr`,
rebind stores None without releasing), so an owned RHS transferred into the
slot is never dropped. That is slot-ownership classification —
`PY-P0-EXACT-CONTAINER-SUBSCRIPT-FULL-OWNERSHIP` — and is pinned as a strict
xfail that flips loudly when that ledger lands.

## Denied along the way (do not re-file)

- "Un-annotated params compile to a silent empty program": DENIED — the
  empty binaries came from a test-harness dedent accident that nested the
  whole body inside `Box.__init__` (legal Python, CPython equally silent).
- "Orphan indented block is silently dropped": DENIED — the parser fails
  closed with a diagnostic (rc!=0, no binary), matching CPython's
  IndentationError.

## Gates

```text
new canary file: 3 passed + 1 strict xfail, under GC backends 0..4
frontend sweep: error-paths + set-family + owned-method + refcount-variants
  + builtin-zip + multi_file_compile = 83 passed
closure (equal-arm /tmp): attr_load_lowering OK
```
