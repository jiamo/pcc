# Fixed: an explicit collect returned while a sweep was still owed

`GC-P1-COLLECT-INSIDE-LIST-REMOVE-LEAVES-CYCLE-UNCOLLECTED` is closed.  A
`pcc_gc_collect` driven from a user callback during `py_list_remove` left an
unreachable cycle unreclaimed on every backend that uses the rooted remove
path; it now reclaims it.

## Root cause

`pcc_gc_collect` drained with `for (;;) { if (!pcc_gc_step(1024)) break; }` and
then swept once, `if (pcc_gc_has_tracing_sweep() != 0)`.

A zero-progress step does **not** mean the cycle is finished.  Measured on
backend 1, stepping by hand from inside the callback:

```text
k=0  sweep=0  -> step=1  sweep=0
k=1  sweep=0  -> step=0  sweep=0    <- loop breaks here, sweep flag also 0
k=2  sweep=0  -> step=6  sweep=1    <- one more step: 6 units, sweep now owed
k=3  sweep=1  -> step=0  sweep=1
```

The step returns zero at a **phase boundary**: one call finishes a phase
without producing countable work and the next call starts the following one.
Breaking on the first zero exited at k=1, where the sweep flag was still 0, so
the one-shot sweep check did not fire either — the collect returned with a
sweep owed and the cycle survived.

## Why the obvious repairs do not work

- `while (stepped || sweep_pending)` **spins**: at k=3 onward the sweep flag
  stays set while the step reports zero.
- "break only when nothing is owed" breaks at k=1 exactly as before, because
  nothing is owed *yet* at k=1.

Crossing the phase boundary is unavoidable, so the loop must tolerate one
zero-progress step.

## The fix

Both mirrors now require **two consecutive** zero-progress steps, and perform a
pending sweep inside the loop so the phase after the sweep still runs:

```c
int idle_rounds = 0;
for (;;) {
    int64_t stepped = pcc_gc_step(1024);
    if (stepped > 0) { idle_rounds = 0; continue; }
    if (pcc_gc_has_tracing_sweep() != 0) {
        int64_t swept = pcc_gc_collect_tracing();
        collected += swept;
        if (swept == 0) break;
        idle_rounds = 0;
        continue;
    }
    idle_rounds++;
    if (idle_rounds >= 2) break;
}
```

The strict mirror is the same shape written as a done-flag loop, since the
pcc-Python runtime subset has no `break`.

## Gates

```text
[c-INCREMENTAL_TRICOLOR-remove]     was FAILED  ->  1 passed in 9.03s
[c-CONCURRENT_MARK_SWEEP-remove]    was FAILED  ->  1 passed in 0.57s
[c-GENERATIONAL_MINOR_MAJOR-remove] was FAILED  ->  1 passed in 0.59s
[c-COLORED_RELOCATING-remove]       was FAILED  ->  1 passed in 0.58s
collect_during_list_op, all 20 arms                20 passed in 156.68s
```

## Honest limits

- **The `2` is a threshold, not a proof.**  It is the smallest value that
  crosses the measured one-step boundary.  If some phase transition ever costs
  two consecutive zero-progress steps, this breaks early again — quietly, in
  exactly the way the original bug did.  The principled condition is collector
  idleness (`!mark_active && !cycle_requested && !has_sweep_candidate`), which
  is what the step's own gate tests; that needs a new exported predicate
  because the first two are file-static in `py_gc_backend.c` while
  `pcc_gc_collect` lives in `py_obj.c`.  Worth doing, and deliberately not
  bundled into this change.
- The k=1 zero itself may be a defect (a step that reports nothing while it has
  already found the next phase's work).  Not investigated.
- No bootstrap, stage or fixed-point gate was run.

---

## [DENIED] Draining on collector state instead of the threshold

The "honest limits" section above called the `idle_rounds >= 2` threshold a
threshold rather than a proof and named the principled alternative: drain while
the collector is not idle (`!mark_active && !cycle_requested &&
!has_sweep_candidate`), which is the same three-way test the step's own gate
applies.  Implemented it — exported `pcc_gc_tracing_idle()` from
`py_gc_backend.c`, declared it in `py_internal.h`, mirrored it in the strict
dispatcher as the inverse of the existing `_tracing_work_pending`, and rewrote
both drain loops around it with a 1024-round liveness backstop.

It regresses:

```text
pcc_python PCC_GC_KIND_INCREMENTAL_TRICOLOR collect-during-list-contains
  returned 14: list.contains answered 0 (expected 1)
```

**Why it is wrong:** the loop calls `pcc_gc_collect_tracing()` whenever
`has_tracing_sweep()` is set and the step reported no progress.  That flag means
*a sweep candidate exists*, not *the mark has finished*.  Draining to idleness
reaches that combination in states the threshold version never did, so the sweep
runs mid-mark and frees objects that are still live — here, something the
in-flight `py_list_contains` still needed, so the equality answered against
freed memory.

Reverted to the threshold version, which is verified green across the whole
substrate (244 passed, 5 known-red deselected).  The exported predicate was
removed too rather than left as unused ABI surface.  The `[DENIED]` note now
lives in the comment above both drain loops, not only here: `idle_rounds >= 2`
is exactly the kind of magic number the next reader will want to replace with a
state check, and the code should say that has been tried.

A correct state-based drain would additionally need a "mark is complete"
predicate to gate the sweep.  `has_tracing_sweep()` is not that predicate.

## One thing this did find

The probe held its C-extension `target` in a plain C local while every other
pointer it needs across the callback is a registered scheduler root.  With the
old early-exiting drain the sweep never ran, so it survived by luck.  That
rooting is kept — it is correct hygiene independent of this denial — though it
was not the cause of the regression.
