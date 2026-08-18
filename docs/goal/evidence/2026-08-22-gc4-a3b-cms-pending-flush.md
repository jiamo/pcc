# GC4 A3b C GC2 CMS pending-flush deferral

Status: **FOCUSED GREEN for the stable C GC2 write-barrier/TLS
pending-flush boundary only.**
`GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` remains `IN_PROGRESS`.

## Supported claim

For one stable selected GC2 backend, valid managed values and ordinary mutex
lifetime, the C transition oracle no longer acquires or publishes through the
CMS queue from a TLS write-barrier-buffer flush while an outer GC graph lock is
held.  The compiled threaded claim uses the default `ATOMIC` refcount strategy;
one exact C threads-off case separately preserves the historical complete
32-entry drain.

The accepted C boundary is deliberately narrow:

1. The write barrier records the first 32 gray identities in TLS.  The 32nd
   entry arms pending service, and the 33rd and later identities arm the
   overflow/sentinel state without acquiring the CMS queue lock, polling a
   safepoint, stopping the world, or publishing queue telemetry.
2. A nested `pcc_gc_graph_unlock` does not service pending CMS work.  The
   outermost threaded unlock first releases graph-lock ownership and only then
   services eligible pending work; the exact nonthread path services its
   complete local batch.
3. Before touching the CMS queue, pending service fails closed when graph depth
   is nonzero and validates the selected backend plus lifecycle epoch.  An
   accepted complete batch advances the TLS cursor only after each accepted
   identity; an unaccepted valid suffix remains in TLS, and an overflow token
   clears only after its sentinel is accepted.  This freezes preservation on
   rejection but does not prove eventual delivery under a full or partially
   accepting queue.
4. The dynamic 40-object boundary observes zero pushes/flushes under the outer
   graph lock, including after its nested unlock.  The outer unlock publishes
   exactly 32 identities plus one all-gray sentinel, increments flush telemetry
   once, and the worker traces the 32 buffered identities, all eight overflow
   identities, and an independent gray root.  All 40 identities and that
   independent root are black before any backend switch/reset can mask the
   observation.
5. A serialized CMS reset pauses the worker without resetting or discarding
   queued/TLS work.  Admitted work may progress during pause/join or a later
   failure unlock; every covered pre-commit failure restarts CMS without a
   lifecycle reset/discard or new loss, while only a successful backend commit
   resets queue, epoch and TLS ownership.  Caller-owned graph depth, no-park
   ownership, and stopped-world ownership make the CMS setter fail closed
   before pause or state mutation, so those early refusals preserve exact
   state.  The dynamic nested-depth case covers a same-CMS setter; the source
   guard covers either the current or target backend being CMS.
6. Legal depth-zero unregister attempts one pending service and then clears its
   TLS state before a newly registered thread obtains fresh state.  A rejected
   suffix at thread exit is still an explicit delivery nonclaim.

The strict freestanding pcc-Python gate is negative-only: it freezes that the
strict CMS barrier has no C-style TLS buffer, pending/overflow state, queue
lock, flush helper call, or all-gray sentinel contract.  It is not an
algorithmic parity claim.  No public runtime ABI was added, and this slice does
not cover other CMS queue producers.

## Final source and test identity

| Path | SHA-256 |
|---|---|
| `pcc/py_runtime/src/py_gc_backend.c` | `8932dda75f7a500708eee3787d937eccdb70a93c674851ee87fb276c588ef427` |
| `tests/python/test_gc_backend_concurrent.py` | `c825c8afffc9ecd7d27220504975cfb193660c42bba32601ec5d97fdb02402ae` |
| `tests/python/test_gc_threading_substrate.py` | `783fca7df50e093f7cea66008bf019a66b1f8b0a0c873074f45ed6e56b2e5896` |
| `tests/python/test_freestanding_gc_barrier_dispatcher.py` | `f45ea00921fd7a82e16c5221569bcad961212be0d1d2d4acf62a85041df2ee87` |

These are dirty-worktree content identities, not a commit, release manifest,
or source-stable archive.  The final gates and both independent reviews below
refer to this exact four-file set.

## RED to green history

The primary old-production RED was:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_defers_full_wb_flush_until_outer_graph_unlock
```

The probe returned rc 8 and pytest reported **1 failed in 0.43s**.  After the
32nd store but before the caller's explicit outer graph unlock, the old path
had already changed push/flush telemetry.  Its causal path was an outer root
lock at depth one, a nested barrier graph lock at depth two, a full TLS buffer,
the nested unlock back to depth one, and a direct barrier-tail call that
acquired/published through the CMS queue while the outer graph lock remained
held.  This proves the queue-lock edge was reached under the graph lock and
that the edge's contended loop was safepoint-capable.  It did **not** force
real queue-mutex contention, a safepoint wait, or a deadlock.  A later rc 12
from already-gray test values was setup RED, not production-causal evidence,
and is not promoted here.

Four later exact convergence REDs were retained:

```bash
gtimeout 20s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_reset_fails_closed_for_stw_owner_and_no_park
```

The initial no-park same-CMS probe returned rc 12: **1 failed in 0.23s**.

```bash
gtimeout 30s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short tests/python/test_gc_threading_substrate.py::test_cms_wb_queue_publication_is_outermost_and_lifecycle_epoch_guarded
```

This source/order gate first stopped on its legacy `buffer[i]` expectation:
**1 failed in 0.10s**.  A later run of the same exact command enforced zero
barrier-tail flush-helper calls and exposed the remaining redundant calls:
**1 failed in 0.27s**.  The helper's depth guard had prevented publication,
but that was insufficient for the literal zero-tail-call contract because the
outermost graph-unlock owner already services pending work.

```bash
gtimeout 20s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_reset_fails_closed_for_stw_owner_and_no_park
```

The caller-owned root-graph-depth same-CMS probe returned rc 22:
**1 failed in 0.22s**.  The final setter preflight now rejects that nested
state before pause, queue/TLS reset, worker change, or backend mutation.

None of these short REDs created a durable log.  Other intermediate diagnostic
argv and timings were not retained verbatim and are deliberately not
reconstructed.

## Final focused gate commands and results

The final exact source/order gate was:

```bash
gtimeout 30s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short tests/python/test_gc_threading_substrate.py::test_cms_wb_queue_publication_is_outermost_and_lifecycle_epoch_guarded
```

Result: **1 passed in 0.23s**.

The final exact threaded 32nd/33rd+, nested/outer unlock, identity, sentinel
and telemetry gate was:

```bash
gtimeout 40s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_defers_full_wb_flush_until_outer_graph_unlock
```

Result: **1 passed in 0.18s**.

The exact threads-off regression was:

```bash
gtimeout 40s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_nonthread_full_wb_batch_still_drains
```

Result: **1 passed in 7.40s**.  This is one targeted C nonthread case, not a
default/nonthread CMS matrix claim.

The final combined claim gate was:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_defers_full_wb_flush_until_outer_graph_unlock tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_invalidates_partial_wb_on_switch_and_restart tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_reset_fails_closed_for_stw_owner_and_no_park tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_nonthread_full_wb_batch_still_drains tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_unregister_clears_partial_wb_tls tests/python/test_gc_threading_substrate.py::test_cms_wb_queue_publication_is_outermost_and_lifecycle_epoch_guarded tests/python/test_freestanding_gc_barrier_dispatcher.py::test_strict_cms_barrier_has_no_tls_wb_queue_lock_contract
```

Result: **7 passed in 0.51s**.  It covers the threaded pending-flush boundary,
serialized switch/same-CMS reset, no-park/STW/nested-depth failure preservation,
the exact nonthread drain, legal unregister cleanup, source/order controls, and
the strict negative contract.

The final C CMS neighbor file was:

```bash
gtimeout 60s env -u LC_ALL -u PCC_REFCOUNT_KIND -u PCC_REFCOUNT_STRATEGY uv run pytest -q -x -n0 --tb=short tests/python/test_gc_backend_concurrent.py
```

Result: **11 passed in 1.01s**.  The file includes worker start/assist, gray
work, batching, pending deferral, lifecycle reset, fail-closed ownership,
nonthread drain, unregister, positive allocation work, mark termination and
backend-switch worker neighbors.  Passing that file is not a global CMS
termination or performance claim.

The exact syntax gates were:

```bash
gtimeout 30s env -u LC_ALL uv run python -m py_compile tests/python/test_gc_backend_concurrent.py tests/python/test_gc_threading_substrate.py tests/python/test_freestanding_gc_barrier_dispatcher.py
```

Result: exit 0 with no output.

```bash
gtimeout 30s cc -O2 -fPIC -Wall -Wextra -std=c11 -pthread -DPCC_WITH_THREADS=1 -Ipcc/py_runtime/include -Ipcc/py_runtime/src -fsyntax-only pcc/py_runtime/src/py_gc_backend.c
```

```bash
gtimeout 30s cc -O2 -fPIC -Wall -Wextra -std=c11 -DPCC_WITH_THREADS=0 -Ipcc/py_runtime/include -Ipcc/py_runtime/src -fsyntax-only pcc/py_runtime/src/py_gc_backend.c
```

Both C modes exited 0.  Each printed the same ten warnings: five tautological
`int32_t` bounds and five unused pre-existing helpers.  This is syntax success,
not a warning-free claim.

All implementation pytest gates were expected and completed below 60 seconds,
so none used `tee` or produced a durable execution log.  No runtime cache,
archive, provenance, C-API, target, completeness-marker or other receipt was
captured for this slice.  Pytest temporary artifacts are not archival evidence.

## Independent review

Two independent read-only reviews converged on the exact four-file identity:

- `/root/a3b_root_tail_impl` reported **ZERO findings** for runtime/design,
  including pause-preserve versus success-only reset, failure restart,
  no-park/STW/depth preflight, rejected-suffix/sentinel preservation and the
  stable C-only claim; and
- `/root/a3b_docs_review` reported **ZERO findings** for source/test and test
  sufficiency, including old-code RED causality, exact 40-object identity
  closure, nested/outer telemetry linearization, lifecycle controls, the
  nonthread case and strict negative-only gate.

Neither reviewer edited the frozen files or reran tests, builds, or profiles.
The commands above belong to the implementation/gate runner.

## Explicit nonclaims and next boundary

This evidence does **not** prove:

- every CMS producer or queue-publication path, a global CMS algorithm claim,
  mark termination, long-run correctness, pause/RSS/throughput/fragmentation,
  or any performance acceptance;
- eventual or lossless delivery when the CMS queue is full or accepts only a
  partial batch, runtime retry of a rejected suffix/sentinel, or delivery of a
  rejected suffix during thread exit;
- admission, epoch ownership, or rollback across a simultaneous/concurrent
  backend switch, or any unstable-backend claim;
- allocation/index insertion failure, worker start/join failure, queue-lock
  corruption, partial index rollback or other fault-injection behavior;
- `BIASED`, `DEFERRED`, any other non-`ATOMIC` refcount strategy, or broad
  threads-off/default-nonthread behavior beyond the one named C test;
- strict pcc-Python CMS algorithmic or debug parity, C/strict GC0..4 parity,
  public ABI parity, or a strict TLS pending-flush implementation;
- all GC graph-lock holders, A3c graph-lock/no-park integration, raw container
  transactions, callback/root handoffs, constructor or managed-thread
  publication, C-API raw-view/buffer leases, collector copy/remap/target-dies,
  post-resume decrefs, forwarded-source retirement, resurrection restoration,
  or physical Backend 4 relocation under concurrent mutators; or
- any broad suite, bootstrap stage, package, performance, five-GC matrix,
  self-host or fixed-point claim.

The next A3b boundary is the remaining **GC3/GC4 graph-lock holder audit**, not
a broad gate or A3c itself.  It must inventory and remove, defer, or prove
bounded every safepoint-capable wait/CAS loop, decref/finalizer, allocation,
free, callback, runtime log/blocking-I/O edge, tripwire, refmeta path and
broader `pcc_gc_store_ptr` holder while the graph lock is owned, with the
relevant C/strict mirror and failure ordering.  Only after those holders are
bounded non-parking leaves may A3c connect outermost graph-lock
acquire/release to no-park.  The CMS queue-full/partial, thread-exit,
concurrent-switch, fault-injection, non-ATOMIC and strict-parity limitations
above remain open alongside that route.
