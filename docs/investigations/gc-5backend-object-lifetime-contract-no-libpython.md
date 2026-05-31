# Investigation: GC backends #1/#2/#3 fail the basic object-lifetime contract under no-libpython self-backend

## Status
resolved 2026-05-31 — the object-lifetime/cycle/container contract now passes
under ALL FIVE backends (0..4). One two-phase clear-then-free sweep fix in
py_gc_backend.{py,c} fixed #1/#2/#3 together (they shared the tracing-sweep
path); #0/#4 unaffected. Gates green: test_object_lifetime.py 5 passed
(xfail markers dropped); gc suites #3/#1/#0 no regression; full stage1->2->3
bootstrap 18 passed/4 skipped (132s, #0 self-host intact). See Report. (The
BROADER 5-GC contract suite — finalizers/weakref/coroutine-roots/valuebox/etc.,
README list — is a continuing G-track program; this doc covers the
object-lifetime contract only.)

## Problem Description
The 5-GC Production Equality Rule says all five GC backends must pass the SAME
Python semantic contract. The first common contract test
(`tests/python/gc_production_contract/test_object_lifetime.py` — basic object
lifetime + a reference cycle + nested-container reachability, each followed by
`gc.collect()`) compiled ONCE under strict no-libpython self-backend and run
under `PCC_GC_BACKEND=0..4` shows the "five production-equal backends" claim is
currently FALSE on this path:

- `#0` refcount+cycle — PASS
- `#4` relocating/ZGC — PASS
- `#3` generational — CRASH (`[BAD_INCREF] o=... tag=-1`, SIGTRAP rc=133) on a
  BASIC reference cycle `x.ref=y; y.ref=x; x=y=None; gc.collect()` — i.e. an
  incref of a corrupted/freed object during/after cycle collection.
- `#1` incremental — ABORT (SIGABRT) on the nested-container-of-instances step
  `box=[Node('n0'),[Node('n1')]]; gc.collect()` (the basic cycle alone passes).
- `#2` concurrent — ABORT (SIGABRT) on the same nested-container step (the basic
  cycle alone passes; may ADDITIONALLY require a `PCC_WITH_THREADS` build — the
  AGENTS.md gc commands run #2 with `PCC_WITH_THREADS=1`, which this single
  default-build harness does not set; needs disambiguation).

## Repro
```bash
# minimal #3 crash (basic cycle):
printf 'import gc\nclass N:\n    def __init__(self,v):\n        self.v=v\n        self.r=None\ndef main():\n    x=N(1);y=N(2)\n    x.r=y;y.r=x\n    x=None;y=None\n    gc.collect()\n    print("ok")\nmain()\n' > /tmp/gctest.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/gctest.py -o /tmp/gctest_bin
for b in 0 1 2 3 4; do echo "#$b:"; PCC_GC_BACKEND=$b /tmp/gctest_bin; done
#   #0 ok | #1 ok | #2 ok | #3 [BAD_INCREF] o=... tag=-1 (rc=133) | #4 ok
# full contract (adds nested-container step) -> #1/#2 also abort.
```

## Test [CONFIRMED]
`tests/python/gc_production_contract/test_object_lifetime.py` — parametrized
0..4; #0/#4 assert-pass, #1/#2/#3 xfail(strict=False) with the reasons above
(`2 passed, 3 xfailed`). They flip to xpass when the respective backend is
fixed — drop the marker then. This is the first brick of the common contract
suite (README lists the rest); runner `scripts/run_gc_production_contract.sh`.

## Interpretation
This is exactly what the 5-GC Production Equality Rule exists to catch: an
intent-vs-reality gap. The repo's GC selection matrix frames #1–#4 as
production/perf candidates; under the STRICT no-libpython self-backend path
(the pcc1 production path), only #0 and #4 currently uphold the most basic
object-graph contract (cycle reclamation + container reachability without
corruption). #1/#2/#3 are BACKEND_PARTIAL on this contract. Note the existing
`test_gc_*` suites largely exercise a C-level harness and/or specific build
configs; this is the first test of the *Python-level* common contract under the
no-libpython self-backend artifact, which is why the crashes were not previously
visible.

## Proposals (per-backend follow-ups — one backend per investigation/PR per AGENTS.md)
- No.1 #3 generational: fix the `[BAD_INCREF] tag=-1` on basic cycle collect  [pending]
- No.2 #1 incremental: fix the abort on nested-container reachability + gc.collect  [pending]
- No.3 #2 concurrent: disambiguate (needs PCC_WITH_THREADS build?) then fix the abort  [pending]

### No.1 #3 generational `[BAD_INCREF] tag=-1` (highest priority — crashes on the SIMPLEST cycle)
`tag=-1` means an object whose type tag is -1 (a freed / poisoned header) is
being incref'd. Under the generational backend, a basic two-node cycle dropped
to unreachable then `gc.collect()`-ed triggers an incref of a
collected/forwarded object. Candidate loci (read the reference impl first per
AGENTS.md — `docs/refs_docs/gc-research/`): the generational promotion / eager
owned-slot rewrite path in `py_gc_backend.c` (the `pcc_gc_load_ptr` /
`pcc_gc_store_ptr` barriers for backend #3), or a missing write barrier when the
cycle's slots are cleared. Needs an LLDB backtrace at the BAD_INCREF (per the
AGENTS.md LLDB playbook) to find the first bad pointer. Must mirror the fix in
the pcc-Python runtime port and re-run the contract test + keep #0 green.

## Report (when closing)
Pending — this doc records the finding; the three backend fixes are separate
focused GC investigations (each: read reference impl, LLDB the crash, fix C +
pcc-Python mirror, re-run the contract test under that backend + keep #0/#4
green + full bootstrap).

## Update 2026-05-31 (b) — #3 BAD_INCREF root cause LLDB-pinpointed + fix design

LLDB backtrace at `pcc_debug_bad_incref` under `PCC_GC_BACKEND=3` on the minimal
cycle (`x.r=y; y.r=x; x=y=None; gc.collect()`):
```
py_decref (new_rc < 0 -> BAD_INCREF tag=-1)
 <- user_py_gc_backend__clear_slot
 <- user_py_gc_backend__clear_referents
 <- user_py_gc_backend__finalize_unreachable
 <- user_py_gc_backend__sweep_unreachable
 <- pcc_gc_collect_tracing  <- pcc_gc_collect
```

### Root cause (classic cycle-collector use-after-free; NOT backend-#3-specific code)
`_finalize_unreachable(o)` (py_gc_backend.py:1702 + C mirror) does CLEAR **and**
FREE in a single pass per object: `_clear_referents(o)` -> `_clear_slot` ->
`py_decref(referent)`, then `refcount_forget` + `dealloc`. For an unreachable
cycle x<->y (each rc=1): `finalize(x)` -> `clear_referents(x)` -> `py_decref(y)`
-> y.rc 1->0 -> y is FREED immediately (normal decref->dealloc). The sweep loop
then reaches y's node and calls `finalize(y)` on the ALREADY-FREED y ->
`clear_referents(y)` reads y's (poisoned/freed) slots -> decrefs a stale pointer
-> refcount underflow (`new_rc < 0` -> BAD_INCREF tag=-1). This is the textbook
reason CPython's gc keeps the unreachable set ALIVE during the clear phase and
frees only AFTER all referents are cleared. `pcc_gc_collect_tracing` clears+frees
eagerly per object instead.

### Why #0/#4 pass, and why #1/#2 likely share the root
#0 (refcount+cycle) does not use `pcc_gc_collect_tracing`'s eager
finalize-per-object path; #4 has the `py_obj` decref guard (py_obj.py:530,
`backend==4 and not object_is_known -> return`) that suppresses the stray
decref. #1/#2/#3 all route gc.collect through `pcc_gc_collect_tracing` ->
`sweep_unreachable`; #3 trips on the basic 2-node cycle, #1/#2 trip on the
nested-container shape (more objects -> the eager-free hits a still-to-sweep
object). So ONE fix (two-phase clear-then-free, or keep-alive during clear) in
`sweep_unreachable`/`finalize_unreachable` likely fixes #1/#2/#3 together.

### Fix design (Proposal No.1, refined) — two-phase sweep (keep-alive during clear)
In `_sweep_unreachable`: PASS 1 over the unreachable set — `py_incref` each
object (keep-alive) then `_clear_referents` (breaks cycles; referent decrefs no
longer reach 0-and-free because every unreachable object holds an extra ref);
PASS 2 over the set — `py_weakref_invalidate` + `refcount_forget` + untrack +
dealloc (drop the keep-alive and free). Mirror in C `py_gc_backend.c`. Gate:
`tests/python/gc_production_contract/test_object_lifetime.py` #3 (and ideally
#1/#2) flip to xpass; keep #0/#4 green; run `PCC_GC_BACKEND=3 pytest -n0
tests/python/test_gc_*.py` (+ #1, and #2 with PCC_WITH_THREADS=1); full
stage1->2->3 bootstrap (default backend #0 must stay byte-identical). Read
docs/refs_docs/gc-research/ (the tracing/cycle reference) before implementing;
do not regress backend #0. This is a focused GC change — implement on its own
turn, not rushed; revert if the bootstrap or #0/#4 regress.


## Report
Landed the two-phase clear-then-free sweep (Proposal No.1, refined). Root cause
was a classic cycle-collector use-after-free: `pcc_gc_collect_tracing`'s sweep
cleared+freed each unreachable object in ONE pass, so clearing object x's slot
to a sibling cycle member y decref'd y to 0 and freed it immediately; when the
sweep then reached y it finalized already-freed memory -> refcount underflow
(`[BAD_INCREF] tag=-1`). Fix: PASS 1 (`_clear_unreachable` /
`pcc_gc_clear_unreachable`) clears all pending objects' referents while KEEPING
the 1024 sweep-candidate flag (so `_clear_slot`'s `_is_sweep_candidate` guard
skips the decref of every still-pending sibling), PASS 2 frees them. Mirrored in
the C runtime (`py_gc_backend.c`) and the pcc-Python port (`py_gc_backend.py`).
One fix closed #1 (incremental), #2 (concurrent), and #3 (generational) — they
all route gc.collect through the shared tracing sweep. Evidence: all five
backends produce `a|cycle-collected|n0 n1` under PCC_GC_BACKEND=0..4;
test_object_lifetime.py 5 passed; PCC_GC_BACKEND=3 generational suite 29 passed,
#1 incremental 6 passed, #0 test_gc_api 16 passed; full bootstrap 18 passed/4
skipped. Proposals No.2/No.3 (separate #1/#2 fixes) are SUBSUMED by this single
shared-path fix. The 5-GC Production Equality Rule earned its keep: its first
contract test found a real 3-backend crash, and one root-caused fix resolved it.
