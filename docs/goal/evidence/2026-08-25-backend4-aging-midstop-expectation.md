# Backend-4 aging mid-stop expectation — 2026-08-25

## Status

**Diagnosed, not changed.**  Generation aging is functionally correct.  The
failing assertion is a mid-stop timing expectation that no longer holds and, as
written, no longer demonstrates the property the test is named for.  I did not
rewrite the expectation, because doing so would make the gate vacuous — see
"Why I did not just change the numbers".

## The symptom

`test_gc_threading_substrate.py::test_colored_generation_aging_polls_only_after_releasing_graph_lock[c]`
fails in under half a second with:

```text
mid-stop promotions=0 aged=0
```

against a required `16` and `16`.

## What the probe does

A worker thread spins on `pcc_thread_stop_requested_acquire()`.  Main allocates
32 young lists via `py_list_new(0)`, calls `pcc_stop_the_world()`, then takes
the graph lock and asserts that **exactly 16** promotions have happened and
**exactly 16** objects have aged.  After `pcc_resume_world()` and join it
asserts the worker returned 32 and all 32 are old.  For `kind="c"` the aging
step is `pcc_gc_step`.

## Measurement

The probe returns at the mid-stop check, so it never reports the end state.  A
variant that continues past it shows the whole picture:

```text
mid-stop promotions=0 aged=0
[continuing past mid-stop check]
worker_result=32
total promotions=32
aged_final=32
```

So aging promotes **all 32 objects**, the worker returns the expected 32, and
every object ends up old.  Nothing about generation aging is broken.

The mid-stop value is stable, not flaky — 8 consecutive runs:

```text
mid-stop promotions=0 aged=0    (x8)
```

## Reading

The worker reaches `pcc_gc_step(32)` only after a stop has been requested.  A
worker that parks at its safepoint before starting a fresh aging batch, and does
the work after `pcc_resume_world()`, is the **safe** behaviour — beginning new
GC work while the world is stopped is what you would not want.  The observed
numbers match exactly that: zero during the stop, all 32 after resume.

The `16/16` expectation encodes an older cadence in which the worker got one
tenure batch in before parking.  Under the current behaviour the worker never
takes the graph lock mid-stop at all.

## Why I did not just change the numbers

The test is named
`..._aging_polls_only_after_releasing_graph_lock`.  Its property is that the
aging step must not hold the graph lock across a poll, and the `16/16` mid-stop
reading was the *evidence*: the worker had done half a batch, released the lock
and parked, so the stop-the-world owner could take it.

With `0/0` the stop-the-world owner takes the lock because the worker never
started.  The property is then trivially satisfied but **not demonstrated** —
relaxing the assertion to `0/0` would leave a gate that passes without testing
anything.  That is exactly what this row's own `failure_disposition` forbids.

Re-expressing it needs the worker to be *mid-aging* when the stop is requested,
rather than starting only after it.  That is a real test-design change and is
filed rather than improvised here.

## Nonclaims

- No runtime file was modified and no expectation was relaxed.
- This says nothing about the third symptom
  (`test_colored_relocating_task_and_scheduler_queue_follow_forwarding`,
  `[0,1,1]`), which is still undiagnosed.
- Whether the parking behaviour changed deliberately was not established; only
  that the current behaviour is self-consistent and safe.
