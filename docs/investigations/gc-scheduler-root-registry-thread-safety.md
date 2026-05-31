# Investigation: scheduler root registry must be safe under concurrent GC

## Status
resolved

## Problem Description
The scheduler root registry added for coroutine/task queues is shared between
mutator threads and GC steps. Backend #3 scans `pcc_gc_scheduler_roots` while
register/unregister can push or unlink nodes, so the list must use the same
object-graph lock as frame roots and minor promotion. Without that, backend #2
worker/assist and backend #3/#4 moving collectors can lose scheduler roots or
read freed registry nodes.

## Repro
Run the focused TSan gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_scheduler_root_registry_threadsanitizer_or_skip' -q -n0
```

Expected pre-fix result: ThreadSanitizer reports a data race involving
`pcc_gc_scheduler_roots` or a `PccGcSchedulerRootNode` field while registrar
threads call `pcc_gc_scheduler_root_register/unregister` and a collector thread
calls `pcc_gc_step()`.

## Test [CONFIRMED]
The focused TSan test fails on the pre-fix C runtime:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_scheduler_root_registry_threadsanitizer_or_skip' -q -n0
```

Observed result: `1 failed in 6.03s`; ThreadSanitizer reported a data race on
global `pcc_gc_scheduler_roots`, with the collector reading in
`pcc_gc_step_generational_promotion()` while a registrar wrote in
`pcc_gc_scheduler_root_register()`.

## Proposals
- No.1 Lock scheduler root registry mutations     [CONFIRMED]

## No.1 Lock scheduler root registry mutations
### Code Change
Protect `pcc_gc_scheduler_roots` head mutation and node unlink in both the C
runtime and the pcc-Python runtime mirror with the existing GC object-graph
lock. Allocate register nodes before taking the lock, and leave slot value
updates to the caller.
### CONFIRMED
The fix removes the observed TSan race while preserving Backend #3 scheduler
root slot rewrite in both runtime implementations.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_generational_scheduler_root_registry_threadsanitizer_or_skip' -q -n0
```

Observed result: `1 passed in 4.71s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy' -q -n0
```

Observed result: `2 passed in 28.48s`.

## Report (only when the investigation is closing)
No.1 landed. The scheduler root registry list now uses the same graph lock as
Backend #3 root promotion and tracing scans. This closes the immediate
register/unregister vs GC-step race; it does not claim the full scheduler queue
model is complete. Runnable/timer/IO/wakeup/await-chain queues still need real
runtime objects and coroutine/task integration before the broader scheduler root
goal can close.
