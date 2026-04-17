# GC semantics gap — pcc keeps refcount, drops everything else

**Status:** open. Surfaced 2026-04-29 while comparing pcc's runtime to
CPython's GC contract.

## The problem

pcc's runtime currently implements **only the reference-counting half**
of CPython's memory model. Cycle collection, `__del__` finalizers, weak
references, and atomic refcount are all stubbed or absent. The bootstrap
closure happens not to construct cycles or rely on `__del__`, so this
hasn't blocked `pcc1 → pcc2 → pcc3`. But anyone who runs a real Python
program through pcc — long-lived servers, anything with circular
references, RAII via `__del__`, observer patterns over weakrefs —
hits silent memory leaks or wrong behaviour.

This makes pcc structurally **a Python-shaped subset interpreter, not a
Python implementation** for memory semantics. Same shape of problem as
self-host-ergonomics.md, but in the runtime layer instead of the source
layer.

## What's actually there

`pcc/py_runtime/src/py_obj.c::py_decref`:
- Decrements `h->refcount`; at 0, dispatches per-type dealloc.
- Skips immortal-flagged singletons (`py_None` / `py_True` / `py_False`
  / built-in classes — same shape as CPython PEP 683).
- Skips tagged ints (no header to count).

`pcc/py_runtime/src/py_obj_gc.c`:
```c
void py_gc_init(void)    { /* TODO(phase2+): init tri-color lists */ }
void py_gc_collect(void) { /* TODO(phase2+): run a collection */ }
void py_gc_track(PyObject *o)   { ... |= PY_FLAG_GC_TRACKED; }
void py_gc_untrack(PyObject *o) { ... &= ~PY_FLAG_GC_TRACKED; }
```

The GC API is symbol-complete (every CPython call site can link), but
the cycle collector itself is a no-op. The track/untrack flags are
written to the object header but never read.

## Concrete user-visible bugs

| Pattern | CPython | pcc |
|---|---|---|
| `a = []; a.append(a); del a` | freed by gc.collect | leaks forever |
| `class Node: def __init__(self): self.parent = None`; build a tree where `child.parent = parent` | freed when root drops | leaks the whole tree |
| `class Resource: def __del__(self): close()` | runs at refcount=0 | dealloc skips `__del__` |
| `wr = weakref.ref(obj); del obj; wr()` | returns None | pcc has no weakref impl |
| Two threads `decref` same object | atomic on 3.13+ free-threading | non-atomic, double-free or refcount drift |
| `gc.collect()` / `gc.get_referrers()` | works | stub returns 0 / empty |

## Comparison with other Python runtimes

| impl | refcount | cycle GC | __del__ | weakref | atomic refcount |
|---|---|---|---|---|---|
| CPython | yes | generational | yes | yes | 3.13t: yes |
| PyPy | no, mark-sweep | yes | yes | yes | yes |
| Jython | no, JVM | yes | yes | yes | yes |
| MicroPython | no, mark-sweep | yes | partial | yes | n/a |
| **pcc**          | **yes** | **stub** | **no** | **no** | **no** |

pcc is strictly weaker than every other Python runtime on memory
semantics. This is fine for bootstrap but cannot stay that way long.

## Why this isn't a bug — yet

The bootstrap closure (pcc compiling itself) doesn't trigger any of
the gaps:

- The AST is a frozen dataclass DAG with no parent pointers, so no
  cycles.
- pcc's pcc-Python ports avoid `__del__` and weakref (they were written
  defensively for the current runtime).
- bootstrap is single-threaded, so non-atomic refcount is fine.
- Process exits before any leak grows large enough to matter.

That's why `pcc1 → pcc2 → pcc3` byte-equal hasn't surfaced these
bugs. Real-world Python programs will surface them immediately.

## Plan

Sequence: refcount correctness → cycle collector → finalizers →
weakrefs → atomic refcount. Each block is independent enough to
land separately and lock progress with tests.

### Phase G0 — pin down what works and what doesn't (1 day)

- Add `tests/test_gc_semantics.py` documenting the contract:
  - `test_refcount_releases_unreachable_tree` — passes today
  - `test_immortal_singletons_dont_dealloc` — passes today
  - `test_tagged_int_decref_is_noop` — passes today
  - `test_cycle_self_reference_leaks_pre_collector` — passes today as
    a *negative* lock; flips polarity once Phase G1 lands
  - `test_dunder_del_dispatch` — `pytest.xfail` until Phase G2
  - `test_weakref_basic` — `pytest.xfail` until Phase G3
  - `test_concurrent_refcount_no_drift` — `pytest.xfail` until Phase G4

- Document the gap in `README.md` under a "Memory model" section so
  early users aren't surprised.

- No code changes. This phase locks the current contract so future
  work has a known starting line.

### Phase G1 — tricolor cycle collector (1-2 weeks)

The big one. Implement generational-light: one generation, full-trace
on demand, threshold-driven trigger.

- **Container types** that can hold cycles: list, tuple, dict, set,
  class instance, function (closure cells), exception. Each gets a
  `traverse` callback that yields its outgoing references.
- **Collector**:
  1. Mark all GC-tracked objects white.
  2. Walk roots (current frame locals, module globals, immortal
     singletons): mark grey.
  3. Trace grey: for each ref via `traverse`, mark target grey if
     white; mark self black.
  4. White at end = unreachable cycles. Decrement each white object's
     refcount; trigger dealloc cascade.
- **Trigger**: count of GC-tracked allocations since last collect ≥
  threshold (default 700, tuneable via env). `gc.collect()` forces.
- **API**: wire the existing `py_gc_collect` symbol; expose
  `gc.collect()`, `gc.disable()`, `gc.get_count()` natively.

Acceptance:
- `test_cycle_self_reference_leaks_pre_collector` flips to assert
  release-after-`gc.collect()`.
- pcc's own self-compile still byte-equal across stages (collector
  is deterministic enough not to break reproducibility).
- Stage1 closure ON `py_cpy_*` count doesn't increase by more than
  the new gc dispatch glue (~20 calls expected).

Risk: implementing `traverse` for every container is mechanical but
must cover every reference path. Misses cause leaks (still wrong but
no worse than today). The bigger risk is `traverse` over class
instances reading user-defined `__slots__` correctly.

### Phase G2 — `__del__` finalizer dispatch (3-4 days)

- `py_instance_dealloc(o)`: before freeing, look up `__del__` on the
  class; if defined, call it with `o` as self. Resurrect-on-error
  matches CPython behaviour (refcount goes back to 1, object stays
  alive; logged as a warning).
- Cycles with `__del__`: CPython runs them in unspecified order. pcc
  matches by running each `__del__` in arbitrary order, then doing
  the cycle-break decref. Document that ordering is intentionally
  not guaranteed.
- Add `tests/test_gc_finalizer.py`:
  - `test_dunder_del_runs_at_refcount_zero` — passes after G2.
  - `test_dunder_del_resurrect` — object re-stashed in `__del__` is
    not freed.
  - `test_dunder_del_order_in_cycle` — depends on G1; locks
    "ordering is unspecified, but every `__del__` runs once."

Risk: `__del__` raising during interpreter shutdown is a CPython
edge case. pcc can choose to log-and-swallow.

### Phase G3 — weak references (1 week)

- Add `PyWeakRefObject` type with header + target pointer + callback.
- `py_obj.h` adds an optional `weakref_list` slot per type. List, dict,
  class instance need it; int/float/str typically don't (CPython
  matches; tagged ints obviously don't).
- `py_decref` slow path checks `weakref_list` before per-type dealloc;
  walks the list, sets each weakref's target to NULL, optionally
  invokes the callback.
- Native dispatch for `weakref.ref(obj)` / `weakref.proxy(obj)` /
  `wr()` calling.
- Add `tests/test_gc_weakref.py` covering all four (ref, proxy,
  callback, dead-ref returns None).

Risk: weakref + `__del__` + cycle interaction is the historically
buggy intersection. Land G3 only after G2 passes; keep G3 tests in a
separate file so failures are localised.

### Phase G4 — atomic refcount (1-2 weeks, optional)

Only matters once pcc emits multi-threaded programs. Currently pcc
itself is single-threaded so this is deferred.

When it lands:
- Replace `++h->refcount` / `--h->refcount` with `__atomic_fetch_add` /
  `__atomic_fetch_sub` (memory_order_relaxed for incref, _release for
  decref, _acquire for the zero-test).
- Single-threaded path stays fast: most objects are accessed by one
  thread, so the atomic op is uncontested.
- Adopt CPython 3.13t's deferred reference counting for hot
  immortal-shaped objects (small ints, single-char strs) so contended
  atomic adds don't dominate.

Acceptance:
- `test_concurrent_refcount_no_drift` (xfail in G0) flips to assert
  no drift across N threads × M decrefs.
- Microbenchmark: single-threaded refcount overhead ≤ 10% of pre-G4.

Risk: getting memory ordering wrong on weakly-ordered platforms (arm64
*is* weakly ordered). Test on Apple Silicon under thread sanitizer.

### Phase G5 — `gc` module surface (2-3 days)

Native dispatch for the `gc` module so user code that calls
`gc.collect()` / `gc.disable()` / `gc.set_threshold()` doesn't
fall through to libpython.

Most `gc.X` calls are simple wrappers over the runtime functions G1
already exposed. The remaining ones (`gc.get_referrers`,
`gc.get_objects`) need a tracked-object iterator — which G1's
collector already maintains as a side effect.

Acceptance:
- `tests/test_native_gc_module.py` covering collect / disable /
  enable / get_count / get_threshold.
- `gc.get_referrers(x)` returns a list of objects that reference `x`,
  matching CPython behaviour for the common cases.

## Research track — long-term, after G3 (optional)

Phases G0-G5 deliver "pcc has a working CPython-equivalent GC".
Beyond that there's a research-grade extension track that picks up
ideas from Go's runtime GC, ZGC, and incremental tricolor work. None
of this is required for pcc to be a usable Python implementation;
it's where pcc could become an interesting platform for GC research
on top of a real AOT Python toolchain.

The research track is explicitly **optional** and gated on someone
choosing to invest in it. Order is by dependency; each phase is
independently runnable.

### Phase G6 — region-based allocator (~2 weeks)

Replace the current `malloc`-per-object pattern with a region/arena
allocator. Each region is one size class; allocations are pointer-
bump. Same shape as CPython's `obmalloc`, simpler than Go's mcache.

Decoupled from G1's collector — region work changes the allocator,
not the trace logic. Land independently, regression-checked through
the runtime oracle (cc / pcc-C / pcc-Py byte-identical).

Expected wins: faster allocation (pointer bump vs free-list lookup),
better cache locality on hot containers, an enabling primitive for
G8's compaction.

Risk: dealloc patterns currently assume `free(o)` on a per-object
malloc. Per-region recycling needs every type's dealloc to be
restructured around "region returns to pool" rather than "object
returns to heap". `py_obj_dealloc.c` is the focal point.

### Phase G7 — concurrent marking + write barrier (~3 weeks)

The single biggest pause-time win without going full ZGC. Borrows
the model from Go 1.5+ / Erlang / SBCL — *not* the colored-pointer
+ multi-mapping trick from ZGC.

- **Write barrier:** every assignment through a heap reference goes
  through a barrier that records the source-target pair. Codegen
  inserts the barrier at every store that targets an object pointer.
- **Concurrent mark thread:** GC runs alongside mutator, processing
  the mark stack and consulting the barrier-recorded edges. Short
  STW pause to drain residual mark work and finalize the cycle list.

Acceptance: typical Python program pause times drop from "tens of
milliseconds for a 100MB heap" to "sub-millisecond". Not as good as
ZGC's < 10µs but two orders of magnitude better than stop-the-world.

Requires G1 to land first (the collector logic and root-set
discovery is shared). Requires codegen-side write barrier support —
this is the largest non-runtime piece; the codegen change itself is
~150 lines, the discipline of "every object-pointer store goes
through the barrier" is the harder part.

Risk: barrier correctness bugs are silent corruption (missed marks
→ premature free → use-after-free). Test surface needs to include
adversarial cases: assignment during collection, nested writes,
weakref callback writes. ThreadSanitizer is mandatory for this
phase.

### Phase G8 — incremental tricolor with pause budget (~2 weeks)

Final pause-budget control: instead of each `gc.collect()` being one
long pause, split it into N mini-pauses dispersed across allocation.
Match Go's "pacer" and V8's "incremental marking" — pcc commits to
e.g. ≤1ms maximum pause per cycle.

Pcc-Python user code can opt in via `gc.set_pause_budget_ms(n)` or
the env var `PCC_GC_PAUSE_BUDGET_MS`. Default stays "no budget" so
this is non-breaking.

Acceptance: tail-latency stress tests (allocation-heavy long-running
loops) show no pause exceeding the configured budget. Matches the
soft real-time guarantees that Go and V8 ship today.

Requires G7's incremental machinery; G8 is essentially "G7 with a
scheduler attached".

### Phase G9 — colored-pointer experiments (research, no commitment)

Borrow ZGC's "metadata in the unused high bits of pointers" idea
without ZGC's multi-mapping. pcc already uses tagged pointers (low
bit = tagged int); extending the tagged-pointer scheme to mark a
few GC states (young / finalizable / weak) directly in the pointer
gives the load-side check without the multi-mapping requirement.

This is a *real research direction* and might fail. Specifically:
- Tagged-pointer expansion conflicts with x86_64 / arm64 user-space
  conventions when bits 47-48 are used for kernel mappings on some
  configurations.
- Without multi-mapping, the colored bits are advisory not enforced
  — every load has to mask the tag, which can be slower than ZGC's
  hardware-assisted version.

Land only if microbenchmarks show that tag-on-load is cheaper than
the equivalent header-field check. Otherwise leave the idea in
research notes.

### Phase G10 — generational pcc-style (research)

Once G6 (regions) + G7 (concurrent) + G8 (incremental) are in,
generational GC is a natural extension: young region GC'd more often
than old. Standard textbook design; the question is whether pcc's
typical workload shows the bimodal age distribution that makes
generational pay off. Without benchmarks driving this, it's a
"maybe" not a "yes".

### Comparison to ZGC and why pcc doesn't move there directly

ZGC was studied as a possible move during this plan's design and
explicitly rejected as a *direct port*. Reasons:

- Refcount → tracing migration touches every dealloc / decref path
  — equivalent to rewriting the runtime, not adding to it.
- ZGC's colored-pointer + multi-mapping requires Linux `mremap`
  tricks that don't work on macOS, pcc's primary host.
- Load barriers in ZGC mode require codegen-wide insertion,
  including across the `pcc.unsafe` boundary; the runtime ports
  would need barrier-aware variants.
- ZGC targets 100GB+ heaps and multi-threaded servers. pcc's
  bootstrap binary uses ~10MB and is single-threaded; the
  engineering cost scales with the design, not with the workload.

The research-track phases (G6-G10) borrow ZGC's *targets* (low
pause, concurrent, region) without inheriting its hardware
dependencies or its scale. Functionally pcc lands closer to Go's
runtime GC than to ZGC.

## Non-goals for this plan

- **Not** moving to mark-sweep (PyPy-style). Refcount + cycle collector
  is what CPython does and matches user expectations for things like
  `with` statement timing and `__del__` predictability.
- **Not** generational (yet) for the core path; G10 in the research
  track is the optional opt-in.
- **Not** GIL. PEP 703 free-threading is its own multi-month project;
  G4 is a stepping stone, not the destination.
- **Not a direct ZGC port.** The research track borrows ideas, not
  the implementation; see Phase G9 for the explicit "rejected as
  port, considered as inspiration" note.

## Sequencing relative to other work

GC work is **independent** of Issue 1 (libpython link gap) and
self-host-ergonomics. None of those plans depend on cycle collection
landing first. Conversely, GC work doesn't help close Issue 1.

Recommended order (core path):
1. **First** finish Issue 1 (link without `-lpython`) — this is a
   pure-mechanical removal of a dependency, the smallest, most
   well-understood remaining piece of bootstrap.
2. **Then** Phase G0 — document the gap, lock it with xfail tests,
   so the community knows what pcc is and isn't today.
3. **Then** G1 (cycle collector) — single biggest correctness win.
4. **Then** G2 / G3 in either order — independent.
5. **G4 atomic refcount** is gated on having a multi-threading story
   (not currently a goal); defer.
6. **G5** is glue, lands whenever it's convenient after G1.

Research-track order (optional, pick up only if someone wants to
turn pcc into a GC research platform):
7. **G6** (regions) — independent of the rest, can land any time
   after G1 because dealloc shape changes.
8. **G7** (concurrent mark + write barrier) — gated on G1 and on
   codegen having barrier-insertion support.
9. **G8** (incremental pause budget) — gated on G7.
10. **G9** (colored-pointer experiments) — research only, may not
    land at all.
11. **G10** (generational) — research only, gated on G6+G7+G8 and
    on benchmarks justifying the bimodal-age assumption.

## Testing infrastructure that already exists

The runtime oracle harness (`tests/test_runtime_oracle_diff.py`) runs
each `*_basics.py` corpus program through cc-built / pcc-built /
pcc-Py-built archives and asserts byte-identical stdout / stderr /
returncode. Adding `gc_basics.py` covering each phase as it lands
gives us cross-archive equivalence on the new GC paths automatically.

Add a per-phase entry to the oracle corpus:
- `tests/runtime_oracle/gc_cycle_basics.py` — exercises cycle release
  (added with G1).
- `tests/runtime_oracle/gc_dunder_del_basics.py` — finalizer ordering
  (G2).
- `tests/runtime_oracle/gc_weakref_basics.py` — weakref + callback
  (G3).

## Open questions

1. **Does pcc want CPython-bug-for-bug compatibility on
   `__del__`-during-cycle ordering**, or is it OK for pcc to pick a
   simpler rule? CPython 3.4+ runs all `__del__`s before any cycle
   break. pcc could match or document its own rule. Probably match.

2. **Should weakref callbacks run inline during decref, or be queued
   to a safe point?** CPython runs them inline, which has caused
   countless bugs (callback re-enters refcount path). pcc could queue
   them; trade-off is more complexity in exchange for fewer footguns.

3. **Free-threading or GIL-equivalent?** PEP 703 is the standard
   target. pcc skipping the GIL entirely (as it does today) and
   going straight to atomic refcount + thread-safe containers is
   ambitious but matches the long-term direction. This is a big
   architecture call worth deciding before starting G4.

## Bottom line

pcc is shipping a refcount-only GC today and that's not a long-term
position for a Python implementation. The plan above is sequenced so
each phase is independently shippable, locked by tests, and doesn't
gate or get gated by other open work (Issue 1, self-host-ergonomics).
G1 is the single biggest correctness gain; G2 and G3 are fit-and-
finish; G4 is a "when we add threads" item.

**Core path total estimate**: 3-5 weeks of focused work to land
G0-G3 + G5. G4 is open-ended and tied to a not-yet-decided
multi-threading direction. After this, pcc has a working
CPython-equivalent GC.

**Research-track total estimate**: 7-9 additional weeks for G6-G8
(region allocator + concurrent marking + incremental pause budget).
G9-G10 are research notes, not delivery commitments. After the
research track, pcc lands on roughly the same GC capability as
Go's runtime — concurrent, region-based, soft-real-time pause
budget — without the OpenJDK / ZGC engineering cost.

The two tracks are useful targets at different times. The core path
is the position pcc needs for "someone runs my Python program with
pcc and it doesn't leak". The research track is the position pcc
needs to be a GC research platform that can host papers like
"Tricolor on AOT Python" or "incremental Cycle Collection without
JIT support".
