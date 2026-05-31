# Investigation: reentrant gc.collect() from a cycle member's __del__ segfaults on the tracing backends

## Status
resolved 2026-05-31 — fixed by a reentrancy guard in `pcc_gc_collect`. 4th gap
surfaced by the 5-GC common contract suite (after object-lifetime use-after-free,
cycle-finalizer-not-run, and exception-referent-roots).

## Problem Description
A finalizer (`__del__`) that calls `gc.collect()` while a collection is already
in progress must be safe on all five backends (CPython `gc.collecting`
semantics: the reentrant collect is a no-op; the outer collect still runs every
finalizer and reclaims every unreachable object). Under `--backend self
--python-libpython=off`, when the finalizers belong to an unreachable reference
CYCLE — so they are reclaimed by the tracing collect itself and their `__del__`
runs DURING the outer sweep — the reentrant `gc.collect()` segfaulted on
#1/#2/#3/#4. #0 (refcount+cycle) was correct.

## Repro
```bash
cat > /tmp/reentrancy2.py <<'PY'
import gc
done = []
class Fin:
    def __init__(self, tag):
        self.tag = tag
        self.peer = None
    def __del__(self):
        done.append(self.tag)
        gc.collect()
def main():
    a = Fin(1); b = Fin(2)
    a.peer = b; b.peer = a      # cycle -> reclaimed only by the collector
    a = 0; b = 0
    gc.collect()                # collects the cycle -> __del__ runs DURING the sweep -> reentrant collect
    print(sorted(done))
main()
PY
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/reentrancy2.py -o /tmp/reentrancy2_bin
for b in 0 1 2 3 4; do echo "#$b: $(PCC_GC_BACKEND=$b /tmp/reentrancy2_bin; echo rc=$?)"; done
# BEFORE fix: #0 -> [1, 2] rc=0 ; #1/#2/#3/#4 -> (empty) rc=139 (SIGSEGV)
# AFTER  fix: #0..#4 -> [1, 2] rc=0
```

## Test [CONFIRMED]
`tests/python/gc_production_contract/test_gc_collect_reentrancy.py` — two shapes
(plain finalizers + cycle finalizers), each asserts `[1, 2]` on backends 0..4
(hard gate, no xfail). The cycle shape reproduced the segfault before the fix.

## Root cause (LLDB-confirmed)
Crash: `EXC_BAD_ACCESS` in `user_py_gc_backend__sweep_unreachable + 152` (#1),
dereferencing a freed/garbage node. `pcc_gc_collect` (py_obj.py:426 port /
py_obj.c:343) is NOT reentrancy-safe on the tracing backends:
- the outer `gc.collect()` enters the tracing branch (STW ->
  `pcc_gc_begin_explicit_tracing_collect` -> mark step loop ->
  `pcc_gc_collect_tracing` -> `_sweep_unreachable`);
- `_sweep_unreachable` PASS-0 dispatches `py_user_del_dispatch(o)` (the
  cycle-finalizer fix) for each unreachable member, running its `__del__`
  WHILE the outer sweep is mid-iteration over the object list;
- that `__del__` calls `gc.collect()` again -> a reentrant `pcc_gc_collect`
  runs the WHOLE collect again: the reentrant mark (`_seed_roots`) re-whitens
  every object (clobbering the outer sweep's `GC_SWEEP_CANDIDATE`/1024 flags),
  and the reentrant `_sweep_unreachable` FREES nodes the outer sweep still
  holds a `node`/`nxt` pointer to -> use-after-free when the outer sweep
  resumes -> segfault.
#0 is safe because its `pcc_gc_collect` branch is `py_gc_collect()` (refcount +
cycle), which does not use the tracing object-list sweep.

## Proposals
- No.1 reentrancy guard in `pcc_gc_collect`: a tracing collect already in progress -> reentrant call returns 0  [CONFIRMED]

## No.1 reentrancy guard in pcc_gc_collect
### Code Change
The outer collect keeps `pcc_gc_explicit_collect_active` set across its entire
`begin..end` (mark + sweep) window, so a non-zero value at `pcc_gc_collect`
entry means we are reentrant. Port (py_obj.py, the default-mode runtime, and the
mode the self-host bootstrap uses) — read the shared flag directly:
```python
def pcc_gc_collect(reason: int) -> int:
    backend: int = pcc_gc_backend()
    if backend != 0:
        if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0:
            return 0      # reentrant: no-op, before STW/callbacks
    ...
```
Returned before STW so the reentrant call never re-stops an already-stopped
world. #0 never sets the flag, so it is unaffected. C mirror in py_obj.c via a
`pcc_gc_explicit_collect_is_active()` getter (the flag is `static _Thread_local`
in py_gc_backend.c, so py_obj.c cannot read it directly).
### CONFIRMED
After wipe+rebuild, the cycle repro returns `[1, 2]` on all five backends (was
SIGSEGV on #1/#2/#3/#4); #0 unchanged. The reentrant collect is a correct no-op:
the outer collect still runs both finalizers (`done == [1, 2]`) and reclaims the
cycle. Gated by the contract test + the full stage1->2->3 bootstrap (#0
byte-identical) — see ## Report.

## Report
LANDED + FULLY GATED 2026-05-31. Proposal No.1 (reentrancy guard) is the fix.
Port `pcc_gc_collect` (py_obj.py) reads `pcc_gc_explicit_collect_active` via
global_addr and returns 0 when reentrant; C mirror (py_obj.c) reads it via the
new `pcc_gc_explicit_collect_is_active()` getter (py_gc_backend.c, declared in
py_internal.h) since the flag is `static _Thread_local`.

Evidence (all green):
- cycle repro: `[1, 2]` on backends 0..4 (was SIGSEGV rc=139 on #1/#2/#3/#4).
- `tests/python/gc_production_contract/test_gc_collect_reentrancy.py` (2 shapes
  x 5 backends = 10 cases) all pass; full gc_production_contract suite =
  56 passed, 4 xfailed (the 4 xfail are the unrelated exception-referent gap;
  no regression in object_lifetime / weakref / finalizer_cycle / root_graphs /
  container_graphs).
- full self-host bootstrap: `18 passed, 4 skipped` (stage1->2->3, #0
  byte-identical) — pcc_gc_collect change does not perturb the fixed point.
- gc finalizer/effectiveness suites (test_gc_finalizer_corner + test_gc_g2_finalizers
  + test_gc_effectiveness): 44 passed on each of #0/#1/#3 — the guard does not
  no-op any legitimate (non-reentrant) collect.

This is the 4th gap the 5-GC common contract suite found AND fixed (after
object-lifetime use-after-free, cycle-finalizer-not-run, and — still open —
exception-referent-roots). Pros vs alternatives: reusing
`pcc_gc_explicit_collect_active` needed no new global (avoids the pcc-Python
module-const-zeroing pitfall) and covers BOTH the reentrant mark and reentrant
sweep (a sweep-only guard would still let the reentrant mark re-whiten the outer
sweep's candidates). No follow-up issues.
