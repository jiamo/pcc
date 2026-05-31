# Investigation: __del__ finalizers on reference-cycle members are not run by the tracing collect (#1/#2/#3/#4)

## Status
resolved 2026-05-31 — added a PASS-0 finalizer dispatch to the tracing sweep so
cycle-member `__del__` runs (with intact fields) under ALL FIVE backends. Gates
green: all 5 backends produce `a,b`; the 5-GC contract suite is 15 passed
(object_lifetime + weakref_finalizer + finalizer_cycle, xfail dropped); gc #3/#0
no regression; full stage1->2->3 bootstrap 18 passed/4 skipped (152s). See
Report. (Resurrection inside __del__ remains a documented follow-up — PASS 1
still clears, so a resurrected member is mishandled; pre-existing, not exercised
by this contract.)

## Problem Description
A reference cycle whose members define `__del__` — when it becomes unreachable
and `gc.collect()` reclaims it — must run each member's `__del__` (CPython
PEP 442 "Safe object finalization"). Under `--backend self
--python-libpython=off`:
- `#0` (refcount+cycle): runs both finalizers -> `a,b`  ✓ (matches CPython)
- `#1`/`#2`/`#3`/`#4` (tracing collect): finalizers NOT run -> `` (empty)  ✗

## Repro
```bash
printf 'import gc\n_log = []\nclass Node:\n    def __init__(self, name):\n        self.name = name\n        self.ref = None\n    def __del__(self):\n        _log.append(self.name)\ndef main():\n    a = Node("a"); b = Node("b")\n    a.ref = b; b.ref = a\n    a = None; b = None\n    gc.collect()\n    _log.sort()\n    print(",".join(_log))\nmain()\n' > /tmp/cycfin.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/cycfin.py -o /tmp/cycfin_bin
for b in 0 1 2 3 4; do echo "#$b: $(PCC_GC_BACKEND=$b /tmp/cycfin_bin)"; done
#   #0 -> a,b   |   #1/#2/#3/#4 -> (empty)
python3 /tmp/cycfin.py   # a,b
```

## Test [CONFIRMED]
`tests/python/gc_production_contract/test_finalizer_cycle.py` — parametrized
0..4; #0 asserts `a,b`, #1/#2/#3/#4 xfail(strict=False) (`1 passed, 4 xfailed`).
They flip to xpass when the tracing collect dispatches cycle-member finalizers.

## Root cause (LLDB-confirmed; the flag-64 hypothesis below was WRONG)
LLDB under #3 showed `py_user_del_dispatch` WAS reached (via `py_instance_dealloc`
<- `_finalize_unreachable`, i.e. PASS 2) for both cycle members — but AFTER PASS 1
(`_clear_referents`) had already cleared their fields, so `__del__` ran against
cleared state (`self.name` nulled). And the flag check is `64=PY_FLAG_GC_PINNED`,
`16384=PY_FLAG_GC_FRESH_ALLOC` (NOT a finalizer flag — the guess below was wrong).
`py_user_del_dispatch` (py_dunder.c:307) is idempotent: it checks/sets
`PY_FLAG_FINALIZED`, so dispatching it earlier and once is safe.

## Original likely-cause note (DISPROVEN — kept for the record)
`_sweep_unreachable` (py_gc_backend.{py,c}) treats objects with
`flags & (64 | 16384)` specially: it just CLEARS the 1024 sweep-candidate flag
(`store_i32(o,12, flags & ~1024)`) instead of finalizing them. If `64` is the
"has __del__ / has-finalizer" flag, then cycle members with `__del__` are
de-flagged and never have their `__del__` dispatched by the tracing sweep — they
are reclaimed silently. #0's refcount+cycle path runs `py_user_del_dispatch`
(see AGENTS.md "py_user_del_dispatch ... PY_FLAG_FINALIZED") on the cycle
members; the tracing sweep skips that step. (Confirm the `64` flag meaning + the
#0 finalizer path before fixing.)

## Proposals
- No.1 Dispatch __del__ on unreachable cycle members in the tracing collect, PEP-442 style  [pending — focused GC effort]

### No.1 (design, not implemented)
Before freeing an unreachable set, run `py_user_del_dispatch` (set
`PY_FLAG_FINALIZED` after, to prevent resurrection re-entry) on each member that
has a finalizer, in a phase BEFORE the free pass (so finalizers see valid
objects + can resurrect). This interacts with the just-landed two-phase sweep:
likely a PASS-0 finalizer-dispatch (with resurrection re-check) before PASS-1
clear / PASS-2 free. Must match #0's finalizer policy (runs once; object valid
during finalizer; resurrection safe) and not regress the object-lifetime
contract or the bootstrap. Read CPython PEP 442 + docs/refs_docs/gc-research/
before implementing; gate with test_finalizer_cycle.py (#1/#2/#3/#4 -> xpass) +
test_object_lifetime.py (still 5 passed) + gc suites + full bootstrap. One
backend-shared path; like the two-phase fix, one change likely fixes #1/#2/#3/#4
together (shared tracing sweep).

## Context
Found while extending the 5-GC common contract suite after the object-lifetime
two-phase fix landed (gc-5backend-object-lifetime-contract-no-libpython.md). The
weakref + non-cycle-finalizer contract (test_weakref_finalizer.py) passes on all
five backends; only the CYCLE-member finalizer case diverges. This is the second
real gap the 5-GC contract suite has surfaced — the suite is doing its job.


## Report
Landed a PASS-0 finalizer dispatch in the tracing sweep (`_sweep_unreachable` /
`pcc_gc_sweep_unreachable`, py_gc_backend.{py,c}): before PASS 1 clears any
referents, walk the unreachable set and call `py_user_del_dispatch(o)` on each
member. `__del__` now runs with intact fields; `py_user_del_dispatch`'s
`PY_FLAG_FINALIZED` guard makes the existing PASS-2 dispatch (via
`py_instance_dealloc`) a no-op, so each finalizer runs exactly once. Added the
`py_user_del_dispatch` extern to the pcc-Python port. ONE fix closed
#1/#2/#3/#4 (shared `pcc_gc_collect_tracing` sweep), like the object-lifetime
two-phase fix. Evidence: all five backends produce `a,b` under
PCC_GC_BACKEND=0..4; 5-GC contract suite 15 passed (finalizer_cycle xfail
dropped); #3 generational + #0 test_gc_api green; full bootstrap 18 passed/4
skipped. Proposal No.1 landed. Follow-up (separate): resurrection inside __del__
(PASS 1 still clears a resurrected member) — a re-check after PASS 0 would be
needed for full PEP-442 resurrection safety; not exercised by this contract.
