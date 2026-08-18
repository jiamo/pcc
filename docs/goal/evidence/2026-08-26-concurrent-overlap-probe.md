# A concurrency probe that proves its own overlap — and one thing it caught

`GC-P1-CONCURRENT-TRACER-PROBE-MUST-PROVE-OVERLAP` asked for a probe where a
real second thread traces while the mutator rewrites a container, with three
properties the removed first attempt lacked.  All three are in:

* **start barrier** — the mutator does not begin until the worker has
  registered, so "the worker ran" and "it ran during the mutations" are
  distinguishable;
* **counted window** — the worker counts a step only if `mutation_active` was
  set both before and after it;
* **non-zero progress inside the window** — not merely that a loop executed.

Ten arms (both mirrors, five backends) pass.

## Three probe defects found on the way, all mine

**A registered thread must not block outside a safepoint.**  The first version
called `pthread_join` straight after signalling the worker to stop, and
deadlocked on backends 1, 2 and 4.  Three stacks, one sample:

```text
main    pthread_join -> __ulock_wait
CMS     pcc_gc_cms_worker_main -> pcc_stop_the_world -> cond_wait
tracer  pcc_gc_step -> complete_mark_cycle_seed -> pcc_stop_the_world -> cond_wait
```

`pcc_stop_the_world` explicitly handles two racing requesters — the loser parks
and takes its turn later — but a GC-registered thread blocked in `pthread_join`
never parks, so the stop owner waits forever for it.  The mutator now polls
`pcc_gc_safepoint()` while waiting for the worker to exit.  This is an API
contract worth knowing: *a registered thread that blocks outside a safepoint is
invisible to stop-the-world.*

**Window duration and mutation cost must be separate.**  Mutating on every
iteration makes holding the window open expensive, so the iteration bound has to
be small, and then the worker sometimes never lands a productive step inside it
— measured, about half the runs failed with "gave up waiting for in-window GC
progress".  The loop now yields cheaply and mutates every 64th iteration.

**Backend 0 has no tracing step**, so requiring in-window *progress* there
asserts something impossible.  It requires only that the worker ran in the
window.

## The exact-count invariant does not hold on backend 4, and I stopped guessing

The invariant "each displaced value is finalized exactly once" holds on
backends 0-3.  On COLORED_RELOCATING the count moved between `leak` and
`premature free` depending only on what I added to the teardown drain
(plain collects; collects plus forced `remap_and_retire`).  A number that
changes with the shape of the teardown is telling me my expectation is wrong,
not that a defect was found, so the exact-count assertion is off for backend 4
and what it owes exactly is left open rather than guessed.

The timing-independent safety assertions still apply to all five: dict length,
the surviving value present and correct, no exception pending, root balance, and
that the finalization count never *exceeds* the number of displaced values.

## What it caught, and what I am not claiming

That last assertion fires intermittently on COLORED_RELOCATING:

```text
more finalizations (100) than displaced values (99): the surviving value was freed too
```

Measured rate: **1 failure in 8 consecutive full runs** (and roughly 3 in 6
before an unrelated edit, so the rate is not stable either).  At that point the
value is in the dict — `len == 1` and `py_dict_get` non-NULL are asserted before
the drain — and the dict is a registered scheduler root.

**I am not filing this as a runtime defect.**  The single-variable control that
would settle it — same probe with no worker thread — did not work: my switch
pre-set the window counters and the run exited through the iteration bound
instead, so it exercised a different path and answers nothing.  Without that
control I cannot separate "backend 4 frees a live rooted value when a
concurrent tracer is running" from "my probe mis-accounts somewhere".  Two
findings I filed against the runtime earlier in this work turned out to be my
own errors; this one gets the control first.

Filed as `GC-P1-BACKEND4-CONCURRENT-SURVIVOR-FINALIZED` with the rate, the
message, and the control as the next step.  The probe must be deselected in gate
commands until it is resolved — a 1-in-8 flake poisons every later run.

## Gates

```text
concurrent_tracer_overlaps, 10 arms       10 passed (current)
stability                                  7 of 8 full runs pass
```

## Nonclaims

- Whether the intermittent failure is a runtime defect or a probe accounting
  error is unresolved.
- Backend 4's exact reclamation obligation under a concurrent tracer is
  unspecified by this probe.
- No bootstrap, stage or fixed-point gate was run.
