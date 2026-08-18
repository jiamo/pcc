# GC4 A3c graph-lock/no-park evidence — 2026-08-24

## Claim

In threaded C and strict pcc-Python runtimes, one outermost physical GC graph
lock ownership now owns exactly one A1 no-park lease.  A recursive graph lock
changes only graph depth.  An outer waiter completes/awaits thread registration
before participating in CAS; failed CAS iterations remain parkable and retain
their existing safepoint/backoff.  Only successful physical CAS enters
no-park, then graph depth becomes one.

Outermost unlock decrements graph depth to zero, physically release-stores the
lock word, performs deferred CMS flush/tripwire work, and only then exits
no-park.  That exit may service a pending stop.  Threads-off lock elision is
unchanged.

The first A3c implementation exposed a raw-newcomer deadlock in the real seed
probe: an unregistered raw pthread won graph CAS during an active STW, then
`no_park_enter` waited for registration while still holding the graph lock.
The STW owner could not finish its callback/commit and the probe timed out.
Final ordering registers before CAS and fails stop if registration cannot
complete.  STW callback probes no longer assume an unregistered thread may
bypass STW to acquire a production lock; they prove callback-side no-park depth
is zero plus the source release ordering.  Separate registered/newcomer tests
cover admission and STW behavior.

## Focused evidence

Final C/strict recursive-depth, threads-off, newcomer admission, trampoline /
unregister, and seed/final/remap callback packet:

```text
17 passed in 5.38s
```

Cold strict receipts on final source identities:

```text
1 passed in 133.62s  nonthreaded no-park/world-owner node
3 passed in 139.88s  threaded seed/final/remap callback nodes
```

Complete callback-holder chain and finisher/CMS neighbors:

```text
31 passed in 8.48s
```

Remap/retirement/drain/barrier plus relocation-payload packet:

```text
45 passed in 12.82s
```

Final-source task-card payload/retirement gate:

```text
24 passed in 6.51s
```

C syntax with `PCC_WITH_THREADS=0/1`, strict runtime-high self/no-libpython
closure, Python syntax and `git diff --check` pass.  Two deliberately
interrupted dots-only archive runs are not evidence and left no surviving
pytest/bootstrap/pcc child.

## Frozen identities

```text
35b7593cebf89e33e8697ef53800a552589b47f01aa6adeed2c31fb5b977a8d9  pcc/py_runtime/src/py_gc_backend.c
ced9099f22711c829d2d6026249ff3b361d1b679fa1dfd9d9babe92e2800a55c  pcc/py_runtime/py/freestanding_runtime_high_substrate.py
c718cc0446120636e6b66bcfcd7c9778ca1a4f36fb57c52aa6d28ef3b6ce7170  tests/python/test_gc_threading_substrate.py
880561c5c82c39e91e61b8fe4bb70d9b32f97e48f9f7140231bfc89e0f3a6cfc  build/gc-graph-no-park-focused.log
580b0041fd3ac0bcf391109e8c519d5b04d3c8eb849f9814ca35d7727291d372  build/gc-a3c-nonthread-strict.log
d6a5161d4141bd42cb3256136e8147f845fb69d76258aa1a4c2ec08e528de0a5  build/gc-a3c-callback-strict.log
3ec4cb159a94beae902665deba8ec7f6986e5dd4d439f8ed1ed5ccb59cb60889  build/gc-callback-holder-complete.log
9177c0e8ec33019ab54cbbf573978b97fbbda2484baa4073a50b411a849fe335  build/gc4-relocation-mutator-quiescence.log
```

## Open boundary

A3c proves graph-lock ownership is non-parkable; it does not prove mutators
hold that lock across complete raw payload operations.  The parent P0 still
requires real list/dict/set transactions spanning barrier, old-value load,
incref, raw slot store and decref, with the collector unable to copy or retire
the payload until transaction exit.  Source/page lifetime, backend ABA,
constructor publication, C-API raw views/leases, callback roots, resurrection
and stale-candidate fairness also remain.  No stage/performance, fixed-point or
broad five-GC claim follows.
