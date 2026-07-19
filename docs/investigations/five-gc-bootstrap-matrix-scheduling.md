# Five-GC bootstrap matrix scheduling regression

## Status

Resolved (2026-07-17).  The bounded five-GC matrix completed inside its
watchdog, and a follow-up GC3-only fixed-point run proved and removed a
collector-specific parent-process full-collection cost.

## Failure boundary

The standard five-file pytest command uses the repository default
`-n 6 --dist=loadgroup`.  `tests/python/gc/conftest.py` places every full
bootstrap item in the same `gc_full_bootstrap` xdist group.  That sends all
five items to one worker and makes them strictly serial.

The shared helper now contains a separate resource-pool scheduler that can
admit up to three full chains, prioritize heavier collectors, partition CPU
budgets, and share stage1.  Its `parallel_slots` fixture deliberately returns
one when it sees the legacy shared xdist group, so the group prevents the
newer scheduler from participating at all.

## Observation

The interrupted current-source matrix completed GC0, then entered GC1 stage3,
but still had only one pytest result after more than 16 minutes.  The outer
command watchdog is 1800 seconds.  Process snapshots showed healthy active
codegen workers; this was scheduling serialization, not a compiler deadlock.

The run was interrupted with exit 130 after the human requested optimization.
A post-interrupt process scan found no remaining pytest, bootstrap, pcc1,
pcc2, or pcc3 child.

## Confirmed design

The legacy all-in-one xdist marker predates the bounded resource pool and
conflicts with it, but simply launching three cold chains would duplicate the
largest cost.  The implemented design has two coupled parts:

1. A host-side SHA-256 planner keys each self-backend object by cache schema,
   full pcc source identity, platform/machine, target, assembler path/version,
   and complete IR bytes.  Cached objects have their own SHA-256 sidecars and
   are verified before reuse.  A miss still runs the short-lived native pcc
   emitter worker, then a host-side atomic publisher stores the object.  Cache
   failure or corruption becomes a miss; it cannot weaken compilation.
2. The five pytest items are independently schedulable.  The resource lease
   gives GC0 one full-machine cache-warm slot first, then restores the existing
   maximum of three active chains for GC1..4.  Thus every collector still runs
   both compiler frontends, while deterministic native object emission is
   shared rather than repeated ten times.

The cache accelerator does not import or execute `pcc.backend.*` in host
Python.  The self-backed compiler remains the owner of IR-to-assembly/object
generation; host Python only hashes, verifies, copies, and atomically publishes
build artifacts.

## Focused performance evidence

The pre-change current GC0 profile was:

```text
stage2 155.745s + stage3 207.913s = 363.658s
stage3 self object emission: 116.868s
```

With an empty cache, the focused implementation-source GC0 probe (immediately
before cached-object checksum sidecars were added) recorded:

```text
stage2: 168.132s
IR: 199,633,206 bytes
cache: 0 hits / 196 misses; publish_ok=1
self object emission: 80.663s
```

The immediately following real pcc2 -> pcc3 stage recorded:

```text
stage3: 91.371s
cache: 196 verified hits / 0 misses
self object phase including verified copies: 6.061s
pcc2/pcc3: metadata-normalized byte-identical
```

Stage2+stage3 therefore fell from 363.658s to 259.503s (28.6%), while
stage3 alone fell 56.1%.  Cross-GC scheduling should additionally parallelize
the remaining frontend work after GC0 has populated the shared cache.

The one final five-file matrix then completed with an unambiguous pytest
summary:

```text
5 passed in 1500.11s (0:25:00)
```

GC0 ran alone as the cache warmer.  After its terminal marker, the resource
lease admitted GC1/GC3/GC4 concurrently and admitted GC2 when a slot became
free.  This proves independent xdist scheduling plus bounded in-test resource
admission; it does not imply that five full compiler chains ran unbounded.

That matrix exposed a second, GC3-specific cost.  Both GC3 compiled stages
reported 196 cache hits and zero misses, but the parent process still called
the unconditional `gc.collect()` at the end of the native-object phase:

```text
before cleanup fix
stage2 total 585.220s; native-object phase 421.403s
stage3 total 553.387s; native-object phase 400.663s
```

Native emission belongs to short-lived compiled workers on this path, so the
parent owns no emitter heap to reclaim; worker exit is the reclamation
boundary.  The source-mode in-process emitter still retains its incremental
and terminal collections.  After restricting parent collection to that
source-mode path, the current-source GC3-only strict fixed-point gate recorded:

```text
1 passed in 533.59s (0:08:53)
source identity: aacec3d2b7e5a6c700e0716d0821bc85b37fa669a8210f272c1a3401a4ffbc88
stage2 cold cache: total 289.107s; native 118.977s; 0 hits / 196 misses
stage3 warm cache: total 131.944s; native 7.688s; 196 hits / 0 misses
both stages: collect 0.0s; collect_skipped=1
```

The stage3 comparison is 76.2% lower end-to-end and 98.1% lower in the native
phase.  The 533.59-second outer wall time also includes rebuilding shared
stage1 and about three minutes of overlap with an unrelated eight-worker CPU
search, so it is not presented as an uncontended machine benchmark.

## Claim boundary

The proof covers the object-reuse mechanism, checksum hardening, bounded
scheduling, one five-GC aggregate on this machine, and a current-source GC3
fixed point after removing redundant parent collection.  It does not claim a
universal compiler speedup or performance on another machine.  Cache hits
never replace frontend execution, pcc2/pcc3 normalization, or no-libpython
checks.  No full GCC gate was run for this Python self-bootstrap change.
