# Investigation: scheduler queue root slots must be TSan-clean

## Status
resolved

## Problem Description
The scheduler queue API registers each entry value as a GC root slot. Concurrent
Backend #3 GC steps scan registered scheduler slots while producer/consumer
threads write queue entry slots and consumer handoff slots. The registry list
is locked, but the slot value writes also need a synchronization story.

## Repro
Run the focused TSan gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_scheduler_queue_threadsanitizer_or_skip' -q -n0
```

Expected pre-fix result if queue handoff is unsynchronized:
ThreadSanitizer reports a data race in the scheduler queue producer/consumer
handoff path.

## Test [CONFIRMED]
The focused TSan test fails on the pre-fix C runtime:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_scheduler_queue_threadsanitizer_or_skip' -q -n0
```

Observed result: `1 failed in 6.55s`; the first TSan report was a race between
`pcc_refcount_incref()` during `pcc_gc_scheduler_queue_pop_into()` and the raw
`h->refcount` assertion read in `py_decref()` on the producer side.

## Proposals
- No.1 Synchronize scheduler queue root slot writes     [CONFIRMED]

## No.1 Synchronize scheduler queue root slot writes
### Code Change
Add `pcc_refcount_load()` to the threading/refcount substrate and use it for
the `py_decref()` underflow assertion, so threaded builds do not mix atomic
INCREF/DECREF with a raw diagnostic read.

Second TSan report after the refcount diagnostic fix: collector reads a
scheduler queue entry slot in `pcc_gc_promote_young_slot()` while producer
writes the same slot in `pcc_gc_store_root()` from
`pcc_gc_scheduler_queue_push()`.

Follow-up code change: make the C graph lock reentrant, expose
`pcc_gc_root_slot_lock/unlock()` internally, and run `pcc_gc_store_root()`
under that lock. Queue push now registers and stores the entry value under one
graph-lock critical section; queue pop transfers the entry value into the
caller slot and unregisters the entry under one graph-lock critical section.
The pcc-Python runtime mirror uses its existing reentrant graph lock around
the same queue push/pop sequence.
### CONFIRMED
The focused TSan gate is clean after the refcount diagnostic and root-slot
synchronization fixes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_scheduler_queue_threadsanitizer_or_skip' -q -n0
```

Observed result: `1 passed in 5.51s`.

The registry and queue TSan gates also pass together:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_scheduler_root_registry_threadsanitizer_or_skip' 'tests/test_gc_concurrent_collection.py::test_generational_scheduler_queue_threadsanitizer_or_skip' -q -n0
```

Observed result: `2 passed in 9.06s`.

The full concurrent-collection TSan file remains green:

```bash
CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_concurrent_collection.py -q -n0
```

Observed result: `7 passed in 49.53s`.

The affected functional gates remain green:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy' tests/test_gc_coroutine_roots.py -q -n0
```

Observed result: `6 passed in 84.98s`.

## Report (only when the investigation is closing)
No.1 landed. The scheduler queue handoff path is now TSan-clean for concurrent
producer/consumer queue traffic and Backend #3 GC steps. This closes the
immediate slot synchronization issue; broader task scheduler semantics remain
tracked by the user-mode scheduling goal.
