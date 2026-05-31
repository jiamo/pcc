# Investigation: GC backends 1-4 production gate progress

## Status
active

## Problem Description
The GC backend table still described backends 1-4 as partial. The practical
question was why they were partial if focused backend tests already existed,
and which concrete blockers prevented promoting them to production evidence.

## Repro
Focused slices used during this pass:

```bash
PCC_GC_BACKEND=1 env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_abstraction_surface.py \
  tests/python/test_gc_backend_incremental.py \
  tests/python/test_gc_g1_cycle_collector.py \
  tests/python/test_gc_g2_finalizers.py -q -n0 -rxX

PCC_GC_BACKEND=3 env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_backend_generational.py -q -n0 -rxX

PCC_BACKEND=self PCC_GC_BACKEND=4 env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_backend_relocating.py -q -n0 -rxX

env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_backend_under_env.py -q -n0 -vv -k self
```

## Findings
Backend #1 was partial because explicit tracing collect preserved fresh
allocations as black, so manual `gc.collect()` did not sweep fresh unreachable
objects. Explicit collect now marks fresh objects collectible while normal
auto-step still ages fresh allocations safely.

Backend #2 was partial because the C runtime only recorded CMS allocation work
when threads were enabled. The pcc-Python mirror and production score expected
the no-thread mutator path to record workbuffer pressure too. C now records CMS
queue work for backend #2 regardless of thread availability, while only
starting a worker when threads are enabled.

Backend #3 was partial because explicit `gc.collect()` only ran generational
promotion and never ran a major tracing/sweep cycle. Backend #3 now enters the
shared tracing major cycle during explicit collect; normal minor promotion is
unchanged.

Backend #4 was partial because explicit collect interleaved relocation before
major tracing and because tracing/root scans did not consistently handle
forwarded roots and children. Explicit collect now uses tracing/sweep rather
than relocation; ordinary `pcc_gc_step()` still exercises relocation. Internal
root/gray scans resolve forwarding for correctness without counting as public
read-barrier telemetry. Scheduler queue free accounts for relocation-sensitive
owned-slot cleanup when a slot was already resolved internally.

Weakref support was also making all tracing/relocating backends look weaker
than they were: `py_weakref_new()` pinned its target, accidentally turning weak
references into strong references for tracing sweep. Weakrefs no longer pin
targets; relocation is handled by read/invalidating paths.

## Validation
Observed passing results after the fixes:

```text
36 passed in 20.82s
31 passed in 375.81s
9 passed in 29.27s
3 passed in 7.30s
4 passed in 32.42s
23 passed in 130.10s
22 passed in 43.25s
64 passed, 64 deselected in 552.54s
```

## Remaining Risk
This does not yet prove full production status. Remaining evidence still needs
to include the bootstrap/fallback gates after runtime changes, full pcc1
self-bootstrap, and the explicit pcc1 threaded `gc.collect()` reliability gap
tracked separately.
