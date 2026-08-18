# Implemented: the sweep is gated on mark completion

`pcc_gc_sweep_owed()` now gates every `pcc_gc_collect_tracing()` reachable from
`pcc_gc_collect`, in both mirrors:

```c
int64_t pcc_gc_sweep_owed(void) {
    if (backend == REFCOUNT_CYCLE) return 0;
    if (pcc_gc_mark_active_load() != 0) return 0;   /* the missing condition */
    return pcc_gc_has_sweep_candidate() != 0;
}
```

Sound because `pcc_gc_finish_tracing_cycle` is the only writer of the
sweep-candidate flag and publishes it atomically with clearing `mark_active`,
so the flag can only be set by a finished cycle.  `pcc_gc_has_tracing_sweep()`
is left alone — it answers "are there candidates", which reporting callers want.

## The two things that had to be true

**The four previously red arms turn green.**

```text
c INCREMENTAL_TRICOLOR remove      FAILED -> 1 passed in 10.19s
c CONCURRENT_MARK_SWEEP remove     FAILED -> 1 passed in 0.59s
c GENERATIONAL_MINOR_MAJOR remove  FAILED -> 1 passed in 0.57s
c COLORED_RELOCATING remove        FAILED -> 1 passed in 0.58s
```

**The regression that DENIED the previous attempt does not return.**  That is
the load-bearing check, not the four greens: the last attempt also turned these
green and was reverted because `pcc_python INCREMENTAL_TRICOLOR contains`
started answering 0, i.e. it swept mid-mark and freed a live object.  That arm
no longer answers 0.

The round bound is still there but is now liveness-only: it decides when to
stop looking for more work, and can no longer decide whether a sweep is safe.

## What it looked like it surfaced, and did not

Making the sweep run appeared to expose a strict-only defect: every strict
tracing backend returned with an exception pending after both list phases, on
no C arm.  I filed it as a P1 against the runtime.

**It was my own build error.**  Read with the correct API, the exception is:

```text
tag=12  msg=name 'pcc_gc_sweep_owed' is not defined
```

The strict `py_obj.py` called the new predicate without declaring its extern,
and an unresolved name in a port becomes a runtime `NameError`.  So the strict
drain had been *raising* rather than exercising the new gate — which also means
the strict half of this fix was unverified for as long as that row stood.

Declaring it fixes all eight arms:

```text
                             c                    pcc_python
                        contains  remove      contains   remove
REFCOUNT_CYCLE           pass     pass         pass      pass
INCREMENTAL_TRICOLOR     pass     pass         pass      pass
CONCURRENT_MARK_SWEEP    pass     pass         pass      pass
GENERATIONAL_MINOR_MAJOR pass     pass         pass      pass
COLORED_RELOCATING       pass     pass         pass      pass
```

```text
all collect probes + the tag-gate contract test    51 passed, no deselects
```

Two mistakes worth keeping:

- My first attempt to identify the exception cast `py_err_occurred()` to a
  `PyObject *` and segfaulted (`returned -11`).  It returns an **int64 flag**;
  the object comes from `py_current_exception()`, and the message from
  `py_exc_get_message()` plus `py_str_utf8()`.
- A probe assertion firing on exactly one mirror is at least as likely to be a
  missing port declaration as a runtime defect.  I skipped that check and filed
  a P1 against the runtime for my own missing `extern`.

## Nonclaims

- The phase-boundary zero (1, 0, 6 on backend 1) is still unexplained.
- No bootstrap, stage or fixed-point gate was run.

---

## The phase-boundary zero, explained

Two earlier evidence files list "the step returns zero at a phase boundary
(1, 0, 6 on backend 1)" as unexplained.  It is structural, in
`pcc_gc_step_trace_cycle_unlocked`:

```c
if (pcc_gc_mark_active_load() == 0) {
    if (cycle_requested == 0)            return processed;   /* genuinely idle */
    if (finish_claim_epoch != 0)         return processed;
    if (threads_enabled && in_auto_step) return processed;
    (void)pcc_gc_begin_mark_cycle_claim_unlocked();   /* creates work: greys roots */
    return processed;                                 /* ...and reports 0 */
}
if (pcc_gc_trace_extension_roots_pending != 0) return processed;   /* also 0 */
```

Starting a mark cycle greys the roots and therefore *creates* work, but
contributes nothing to `processed`, so the call reports zero.  The measured
sequence follows exactly:

```text
step 1 -> 1    work left from before
step 2 -> 0    mark inactive + cycle requested: begin the cycle, report 0
step 3 -> 6    the greys now exist
```

Breaking on the first zero exits precisely there, which is why the collect
returned with a sweep owed.

This also changes the standing of the round bound in the drain.  It is not an
empirical threshold guessed to fit an observation: it corresponds to at least
two identified structural zero-progress returns — beginning a mark cycle, and
extension roots pending.  A cleaner fix would have those paths report the work
they created, but the return value feeds every caller's budget accounting, so
that is a separate slice rather than something to bundle here.
