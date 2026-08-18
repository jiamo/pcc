# GC4 A3b C-extension remap STW prepass evidence — 2026-08-24

## Claim

Backend-4 remap/update no longer invokes C-extension `tp_traverse` while
holding the GC graph lock, and forwarding/source retirement cannot overtake an
in-flight C-extension slot callback.  Every C and strict remap callsite now
enters one wrapper with no graph lock, acquires or reuses STW, starts an exact
monotonic remap phase, and snapshots object-list revision, forwarding head and
population, page-reseed revision and relocation-reseed revision.

The wrapper advances a local active-object cursor under short graph
transactions.  It retains each non-moving C-extension owner, records the exact
pending owner, releases the graph lock, and runs `tp_traverse`.  Each reported
slot re-enters one short transaction, revalidates phase/backend/pending-owner
and every captured forwarding/registry revision, reloads the slot and heals it
to the forwarding target.  No child or raw slot pointer survives callback
reentry.  Only after every C-extension owner completes and the complete
snapshot revalidates does the existing locked built-in/root heal plus
two-epoch retirement run; that final loop skips C-extension owners already
handled by the prepass.  Drift aborts before retirement and leaves forwarding
sources fail-closed.

Backend switch, nested Backend-4 step and nested/direct remap reject or defer
while the phase is active.  An initially rejected nested wrapper now returns
without touching the outer phase; the first implementation incorrectly cleared
the outer active/pending token and was caught by the dynamic probe before final
evidence.  Existing detached retirement finish remains after graph unlock and,
when the wrapper acquired STW, after world resume.

The real C/strict probe installs an old->target forwarding edge, stores the old
object in a C-extension child slot, enters remap, and proves the callback owns
STW while a raw contender acquires the physical graph lock.  Inside the same
callback, same-backend reset returns `-1` and nested remap returns `0`; after
reentry, `Py_VISIT` reaches the short transaction and the child slot equals the
target before first-epoch retirement.

## Generic bridge repair

The strict probe exposed a generic slot ABI defect rather than a remap special
case.  `pcc_capi_visit_cext_object_slots_i64` passed the sentinel visitor into
`pcc_capi_visit_cext_object_slots`, which already wraps its visitor with that
sentinel; the double wrapper never called the original `PyObjSlotVisitor`.
The strict port now mirrors the C oracle with a dedicated
`pcc_capi_visit_cext_object_slot_i64_adapter`, and the i64 bridge passes that
adapter to the core visitor.  Source and owner gates require the adapter and
forbid the old double-sentinel call.

## Focused evidence

Strict remap/retirement/drain/barrier source owners, LLVM/self closures,
production archive owners, C/strict oracle behavior and deferred finish order:

```text
38 passed in 174.38s
```

Real C/strict remap callback and slot-heal probe:

```text
2 passed in 9.27s
```

Complete initial-seed -> ordinary trace -> final/CMS -> remap callback holder
chain, C/strict finisher and CMS neighbors:

```text
31 passed in 8.56s
```

Final-source task-card relocation/forwarding gate:

```text
24 passed in 7.37s
```

Shared-slot/update source, real C extension update, generic visit owner and
production collector link-map neighbors:

```text
10 passed in 1.94s
```

C syntax with `PCC_WITH_THREADS=0/1`, direct strict self/no-libpython closure
for remap, retirement, relocation drain, barrier dispatcher, GC state, managed
GC backend and C-API visit adapter, Python syntax and `git diff --check` pass.

One 120-second nonthreaded archive run and earlier superseded cold diagnostic
runs ended without a pytest summary; immediate inspection found no surviving
pytest/bootstrap/pcc child.  They are not evidence.  The successful cold source
packet above completed under its measured 240-second budget.

## Frozen identities

```text
6388ae3b599368e5cd85471269e00130a2e01329bcd9ffe33735339585bca1f1  pcc/py_runtime/src/py_gc_backend.c
4c9352a92a01094f8f6ad9489ac6bbc4ba131e90776fd7ef18c5156cbacaf770  pcc/py_runtime/py/freestanding_gc_relocation_remap.py
62c11a3fc51802eec2fdef28f34db42164087359e16130c8e7774d17fbb2c82b  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
5141f32ff8ef4a7325a8cb854ee7b5b1cbf98cb2efa23720a1c101a4937b4b06  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
17c3d0764ad1ac46354e5a9dfc4a01dec9536dd460092e56e681d31a0e3931f8  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
a81768507fb4fb41d58491041a545adb5bb9e6911ed1aa180f912017157e3cb6  pcc/py_runtime/py/freestanding_gc_state.py
3d153ee83dcfbf9325b92a6258d033c4204af1a2f73a16271fb3b81648f32429  pcc/py_runtime/py/py_gc_backend.py
1118506df4b682d2034cad6dea54c4fdc5193281dd8bd40f01dcfa270662a59f  pcc/py_runtime/py/py_capi_visit_runtime.py
163cad03e18d0dd8bb91862e21c05a7a8ab03b678001108f5470564eb31fb560  pcc/py_frontend/codegen/runtime_abi.py
84d01d7fc39268ab4182aea5ad388d0dda5634f41a8a01057fdb8ff13a524c6a  tests/python/test_gc_threading_substrate.py
ca80b855d4da41842a0551c941fbc08b12d8a8408b42eebb1fed6581bb8af852  build/gc-remap-cext-source.log
ef1ba0631d1d92a68b5ba61ea4f473715b55303dd73dfe0fab6d3e2bc45bb243  build/gc-callback-holder-complete.log
cb588e4c2736ca0fb6f6be00db2d20f361d29b448bf1077ec8a1aab77f6a9ea5  build/gc4-relocation-mutator-quiescence.log
```

## Open boundary

The complete classified C-extension slot-callback inventory is now source- and
true-pthread-green: promotion, initial seed, ordinary/direct/final/CMS trace and
Backend-4 remap/update.  This does not close the parent mutator-quiescence P0.

Next is the A3c connection: outermost graph-lock acquisition may enter A1
no-park only after successful CAS, and every outer release must exit after the
physical unlock/deferred flush.  Then the task still requires real list/dict/set
raw-access transactions across copy/retirement, source/page lifetime, backend
ABA, constructor publication, C-API raw-view leases, callback roots,
resurrection and stale-candidate fairness.  Stage performance, fixed point and
broad five-GC parity remain unclaimed.
