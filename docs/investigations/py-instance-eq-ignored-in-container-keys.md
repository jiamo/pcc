# Investigation: container key equality never dispatches user-instance __eq__

## Status
resolved — fixed 2026-08-26 via SEM-P1-INSTANCE-EQ-CONTAINER-KEYS
    (evidence: docs/goal/evidence/2026-08-26-instance-eq-container-keys.md)

## Problem Description

Discovered while writing the concurrent equality-callback probe for
`GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT`. The probe required the
equality callback to run inside dict lookups; it hung because
`overlap_eq_hits > 0` was unreachable. Source reading and a minimal repro
show the runtime never calls a user instance's `__eq__` during container
key equality at all.

User-visible form (CPython semantics vs pcc runtime):

```python
class K:
    def __hash__(self): return 7
    def __eq__(self, other): return True
d = {}
d[K()] = 1
d[K()] = 2
# CPython: len(d) == 1 (second insert replaces, keeps FIRST stored key)
# pcc runtime: len(d) == 2 (equality ignored, identity-only fallback)
```

The same gap applies to set membership via `py_obj_eq`.

## Repro

`/tmp/eqrepro/eq_repro.c` pattern (full source preserved below), compiled
against the cached threaded strict runtime archive:

```text
cc -std=c11 -pthread -I<runtime>/include -I<runtime>/src repro.c \
    <runtime>/libpy_runtime_pcc_py.a -lm -o repro && ./repro
observed: eq_calls=0 len=2   exit=20   (expected eq_calls>0 len==1)
```

Confirmed on backend 1 (INCREMENTAL_TRICOLOR); the code path
(`py_obj_eq`) is backend-independent.

## Root cause

`pcc/py_runtime/src/py_obj_ops_compare.c::py_obj_eq` handles str / int /
float / bytes / tuple / list / dict / set / valuebox / none and ends with
`return 0` for everything else - so `PY_TYPE_INSTANCE` keys compare by
the earlier identity fast path only (`entry_key == key`, py_dict.c:418)
and never reach `lookup_dunder(a, "__eq__")`.

A separate dispatcher EXISTS (`pcc/py_runtime/src/py_protocol.c:268`
calls `lookup_dunder(a, "__eq__")`) but container key equality does not
route through it.

Strict mirror: `pcc/py_runtime/py/` has the same split; both mirrors must
be fixed together (5-GC Production Equality Rule).

## Test [CONFIRMED]

The repro above was run personally on 2026-08-26 and printed
`eq_calls=0 len=2`, exit 20 - the failure is observed, not inferred.

Ready-made concurrent regression (written for this slice, currently
removed from `tests/python/test_gc_threading_substrate.py` because it
cannot pass until this defect is fixed): probe
`test_concurrent_tracer_races_eq_key_preserved` - two equal-but-distinct
keys under a real concurrent tracer with per-op epoch bracket and
required hash-callback AND equality-callback x tracer-step intersections;
post-drain asserts len==1, refcounts proving k0 stayed the stored key
(rc 2) while k1 never became stored (rc 1), exact finalization counts,
root balance. Resurrect it verbatim when landing the fix; its full text
is in this investigation's git history and in the session record of
2026-08-26.

## Proposals

- No.1 Route PY_TYPE_INSTANCE (and generally the final fallthrough)
  through the existing protocol dispatcher in py_obj_eq     [pending]

## No.1 Route the instance case through the protocol dispatcher

### Code Change

In `py_obj_eq`'s fallthrough, before `return 0`: if either side is
`PY_TYPE_INSTANCE`, call the dunder dispatcher used by the `==` operator
(`lookup_dunder(a, "__eq__")` + binary call, truthiness-normalized),
preserving the reflected-operand rule if the runtime has one. Mirror the
same change in the strict port. Then land the resurrected concurrent
regression plus a single-threaded parity test
(equal-but-distinct instances collapse to one entry, stored key is the
FIRST inserted).

### pending

Not attempted yet in this slice; filed as task row
`SEM-P1-INSTANCE-EQ-CONTAINER-KEYS` so it lands with its own gates
(dict/set parity, substrate probes, bootstrap baseline) rather than as a
side effect of probe work.

## Nonclaims

- Only dict key equality and the repro were exercised. Set membership
  reads the same helper but was not separately measured.
- No claim about which mirror (C vs strict) diverged first.

## Update (2026-08-26, closing)

No.1 CONFIRMED and landed. Both mirrors route the instance fallthrough
through `py_user_eq_dispatch`; the guard uses the canonical predicate
(`== PY_TYPE_INSTANCE or >= PY_TYPE_USER_CLASS_START`) because real user
classes carry per-class tags from 104 — the single-tag first attempt
still printed len=2 and was caught by the collapse repro before gates.
Gates and the resurrected concurrent regression are recorded in the
evidence file. The reflected-operand limitation moves to scope notes on
SEM-P1-INSTANCE-EQ-CONTAINER-KEYS.
