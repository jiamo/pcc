# Investigation: object resurrection in __del__ (PEP 442) is mishandled by the two-phase tracing sweep

## Status
resolved 2026-05-31 — fixed by a PEP-442 post-finalizer reachability recheck in
`_sweep_unreachable` (port + C mirror); test_finalizer_resurrection.py now a hard
gate on 0..4. This was the documented follow-up to the cycle-finalizer PASS-0 fix
([[gc-5backend-cycle-finalizer-not-run-no-libpython]]) — that fix made __del__
run during the sweep, which is exactly what made resurrection observable. 5th gap
surfaced by the 5-GC contract suite; 4th fixed (object-lifetime, cycle-finalizer,
reentrancy, resurrection), leaving exception-referent (frontend) as the only open
contract xfail.

## Problem Description
If a finalizer (`__del__`) makes its object reachable again — e.g. appends
`self` to a still-reachable global list — the object must survive the collection
INTACT (PEP 442: after finalizers run, the collector re-checks reachability and
does not reclaim resurrected objects). CPython and pcc #0 (refcount+cycle) do
this. The tracing backends do not.

## Repro
```bash
cat > /tmp/resurrect.py <<'PY'
import gc
keeper = []
class R:
    def __init__(self, v):
        self.v = v
        self.peer = None
    def __del__(self):
        keeper.append(self)          # resurrect: store self in a reachable global
def main():
    a = R(42); b = R(43)
    a.peer = b; b.peer = a           # cycle -> reclaimed by the tracing collect
    a = 0; b = 0
    gc.collect()                     # PASS-0 __del__ resurrects into keeper
    print(len(keeper), sorted([keeper[i].v for i in range(len(keeper))]))
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/resurrect.py -o /tmp/resurrect_bin
for b in 0 1 2 3 4; do echo "#$b: $(PCC_GC_BACKEND=$b /tmp/resurrect_bin 2>&1; echo rc=$?)"; done
python3 /tmp/resurrect.py    # reference: 2 [42, 43]
```
Observed per-backend failure modes (the gap is NOT uniform):
- `#0` -> `2 [42, 43]` rc=0 (correct, matches CPython).
- `#1`/`#2` -> empty, **rc=134 (SIGABRT)** — resurrection + clear/free corrupts
  runtime state (assertion / heap).
- `#3`/`#4` -> `AttributeError: v` rc=1 — PASS-1 cleared the resurrected
  object's fields, so `keeper[i].v` is gone.

## Test [CONFIRMED]
`tests/python/gc_production_contract/test_finalizer_resurrection.py` — #0 asserts
`2 [42, 43]`; #1/#2/#3/#4 xfail(strict=False). Flips to xpass when fixed.

## Root cause
The two-phase tracing sweep (`_sweep_unreachable`, py_gc_backend.{py,c}) computes
the unreachable set during the mark, then: PASS-0 runs `py_user_del_dispatch`
(__del__) on each unreachable member, PASS-1 clears their referents, PASS-2 frees
them. A __del__ that resurrects an object (makes it reachable again) is NOT
reflected: PASS-1/PASS-2 still clear+free every member of the ORIGINAL
unreachable set, including resurrected ones. PEP 442 requires re-checking
reachability AFTER the finalizers run and excluding anything that became
reachable.

The #1/#2 abort was LLDB-confirmed as `malloc: Heap corruption detected, free
list is damaged` / `Incorrect guard value` — i.e. a resurrected object was FREED
by PASS-2 while the global still pointed at it, so the later access/decref hit
freed memory (double-free / free-list damage). #3/#4 instead survived the free
but saw cleared fields (AttributeError). Both are the same root cause (clearing+
freeing a resurrected, still-reachable object); the recheck fixes all four by
excluding resurrected objects.

## Proposals
- No.1 PEP-442 post-finalizer reachability recheck: after PASS-0 re-mark from
  roots and clear the 1024 sweep-candidate flag on any object that is now
  reachable, so PASS-1/PASS-2 skip it.  [CONFIRMED]

## No.1 PEP-442 post-finalizer reachability recheck
### Code Change
Added `_recheck_reachability_after_finalizers()` (port py_gc_backend.py / C
`pcc_gc_recheck_reachability_after_finalizers`), called in `_sweep_unreachable`
between PASS-0 (finalizers) and PASS-1 (clear). It runs `_seed_roots()` (whitens
all but PRESERVES the 1024 flag) + `_drain_all_gray_unlocked()` (so the trace
propagates through the container a __del__ mutated to the resurrected object),
then clears 1024 on every candidate that is now reachable (no longer white). The
non-resurrection path is unchanged by construction: a still-unreachable candidate
stays white|1024 and is reclaimed exactly as before.
### CONFIRMED
Repro returns `2 [42, 43]` on all five backends (was #1/#2 heap-corruption abort,
#3/#4 AttributeError). test_finalizer_resurrection.py flipped from xfail to a hard
gate on 0..4. Full gc_production_contract: 76 passed, 4 xfailed (the 4 = the
unrelated exception-referent gap; finalizer_cycle / reentrancy / object_lifetime
— the shared-sweep tests — did NOT regress). Full self-host bootstrap: 18 passed,
4 skipped (#0 byte-identical, 143.92s). gc finalizer/effectiveness/api suites: 60
passed on each of #0/#1/#3. Known cost: a second mark over the heap per collect
that has candidates; a targeted recheck over only the unreachable set would be
cheaper (follow-up optimization, noted in the code) — correctness first.

## Context
5th gap surfaced by the 5-GC common contract suite (after object-lifetime
use-after-free [fixed], cycle-finalizer-not-run [fixed], exception-referent-roots
[open, frontend], gc.collect-reentrancy [fixed]). Resurrection only became
observable BECAUSE the cycle-finalizer fix now runs __del__ during the sweep; the
two fixes share the same `_sweep_unreachable` core, so the PEP-442 recheck will
extend that same code path. The reentrancy guard
([[gc-5backend-reentrant-collect-during-finalizer-no-libpython]]) is orthogonal
but related (both are __del__-during-sweep hazards).
