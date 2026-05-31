# Investigation: Backend #2 worker mark termination

## Status
resolved

## Problem Description
Backend #2 has a pthread mark worker and a work queue, but the worker drains
mark work with `finish_cycle=0`. If no mutator calls `pcc_gc_step()`, a worker
can empty all gray work and still leave the cycle without the STW mark
termination cut that creates sweep candidates.

## Repro
Run the focused gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_reaches_mark_termination_without_mutator_gc_step' -q -n0
```

Expected after the fix: the C probe prints `1` for worker-created tracing
sweep debt and `pcc_gc_collect_tracing()` reclaims at least one unreachable
self-cycle object.

## Test [CONFIRMED]
`tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_reaches_mark_termination_without_mutator_gc_step`

Observed failing before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_reaches_mark_termination_without_mutator_gc_step' -q -n0
```

Result: `FAILED`, with probe output line `0` where the test expects worker
mark termination to create tracing sweep debt.

## Proposals
- No.1 Let the Backend #2 worker run STW mark termination     [CONFIRMED]

## No.1 Let the Backend #2 worker run STW mark termination
### Code Change
Change the worker trace path so it calls the shared tracing step with
`finish_cycle=1`. The worker already holds the GC graph lock around the trace
step, matching the mutator `pcc_gc_step()` path. `pcc_gc_finish_tracing_cycle()`
performs the existing cooperative STW cut before marking white objects as sweep
candidates.

### CONFIRMED
The worker now calls the shared tracing step with `finish_cycle=1`, so a
worker-drained cycle reaches the same cooperative STW mark termination path as
mutator `pcc_gc_step()`.

Focused repro:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest 'tests/test_gc_backend_concurrent.py::test_concurrent_backend_worker_reaches_mark_termination_without_mutator_gc_step' -q -n0
```

Observed result after the change: `1 passed in 3.19s`.

Backend #2 focused gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_backend_concurrent.py tests/test_gc_abstraction_surface.py tests/test_gc_threading_substrate.py -q -n0 -rxX
```

Observed result: `32 passed in 46.03s`.

## Report (only when the investigation is closing)
No.1 landed. Backend #2 worker tracing can now perform mark termination without
waiting for a mutator-side `pcc_gc_step()` call. The new regression creates an
unreachable self-cycle, switches to Backend #2, gives only the worker a work
ticket, and waits with `pcc_thread_safepoint()` rather than `pcc_gc_step()`.
Before the fix no sweep debt appeared; after the fix the worker creates tracing
sweep debt and `pcc_gc_collect_tracing()` reclaims the cycle.

This is one productionization slice, not the full Go-style CMS closeout.
Remaining Backend #2 work still includes TSan-clean GC worker stress and a
concurrent sweep/allocation safety proof.
