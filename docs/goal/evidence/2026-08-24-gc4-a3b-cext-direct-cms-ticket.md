# GC4 A3b C-extension direct CMS ticket evidence — 2026-08-24

## Claim

The C runtime direct CMS gray-object ticket no longer invokes a C-extension
`tp_traverse` callback while the GC graph lock is held.  The ticket claims and
retains the gray object under the graph lock using the existing exact
`(object, cycle_epoch, backend)` trace token.  The production CMS worker
snapshots that token, releases the graph lock, and only then executes the
shared callback/commit helper.  Each reported slot is grayed through the
existing short revalidated transaction, and the final commit revalidates
liveness, epoch, backend and color before decrementing the gray count and
publishing BLACK.

The source contract was RED on repository HEAD:

```text
git show HEAD:pcc/py_runtime/src/py_gc_backend.c |
  rg 'pcc_gc_cms_direct_gray_probe_run|pcc_gc_trace_cext_complete\(&cext_ctx\)'
# no output
```

The final dynamic probe calls the exact direct-ticket helper under the graph
lock and then the same completion helper after unlock.  A raw pthread waits
until `tp_traverse` begins and acquires the production graph lock from inside
the callback, proving the callback tenure is unlocked.  The probe uses the
incremental backend deliberately so an independent CMS worker cannot turn the
test into a stop-the-world registration test; source assertions prove the
production CMS worker routes its direct ticket through the same helper and
orders graph unlock before completion.

## Focused evidence

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_threading_substrate.py::test_incremental_trace_cext_claim_unlocks_callback_and_revalidates_commit \
  tests/python/test_gc_threading_substrate.py::test_cms_direct_gray_cext_ticket_runs_callback_outside_graph_lock

2 passed in 8.29s
```

CMS worker and queue neighbors:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_worker_traces_gray_barrier_work \
  tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_worker_reaches_mark_termination_without_mutator_gc_step \
  tests/python/test_gc_threading_substrate.py::test_cms_wb_queue_publication_is_outermost_and_lifecycle_epoch_guarded

3 passed in 0.35s
```

Final-source task-card payload/retirement packet, with per-node durable output
in `build/gc4-relocation-mutator-quiescence.log`:

```text
gtimeout 270s zsh -o pipefail -c "gtimeout 240s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_relocation_payload.py \
  tests/python/test_freestanding_gc_forwarding_retirement.py \
  2>&1 | tee build/gc4-relocation-mutator-quiescence.log"

24 passed in 145.45s
```

One earlier 120-second cold-archive run ended after six passed nodes without a
pytest summary.  Immediate process inspection found no surviving pytest,
bootstrap or pcc child.  It is not evidence.  The first 240-second rerun passed
24/24, after which an unconditional diagnostic atomic counter was removed from
the production direct-ticket path; because that changed the source identity,
the 24/24 packet above was run again on the final source.

C syntax under `PCC_WITH_THREADS=0/1` and `git diff --check` pass.

## Frozen identities

```text
d0f39d6a9ed8a1c32ce94920f9bfcf10435814ce7638994899f4bf6258dd9deb  pcc/py_runtime/src/py_gc_backend.c
79b0bf32cd3a6709a7949296f6100841bf3ee3a37fcbe9705e38b0bdd295e3c1  tests/python/test_gc_threading_substrate.py
c8752599fe77a9d2e1f1a254dfd8e47920f7d866dde2f820999d128ef7219949  build/gc4-relocation-mutator-quiescence.log
```

## Open boundary

This proves the direct-ticket callback split and production worker routing; it
does not prove a real CMS worker plus stop-the-world callback execution end to
end.  The CMS RESCAN ticket still calls the whole-gray drain under the graph
lock.  Initial refcount-root seed traversal, final/CMS whole-gray drains and
Backend-4 remap/update C-extension callbacks remain open.  Collector-owned STW,
post-resume temporary-owner cleanup, object/source lifetime, registry revision,
A3c graph-lock/no-park connection, raw access, stage performance, fixed point
and five-GC parity are not claimed.
