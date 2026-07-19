# Investigation: backend 1 threaded explicit collection invalidates a live list

## Status
active

## Problem Description

The default parallel suite intermittently aborts a pcc1-built real-pthread
program under `PCC_GC_BACKEND=1`: `py_list_append` receives an object whose
header no longer has `PY_TYPE_LIST`. This is a continuation of the reliability
boundary recorded in `pcc1-threaded-explicit-gc-collect-gap.md`, but occurs
after its stale-sweep-candidate repair and therefore needs a new evidence chain.

## Repro

Run the preserved failing pcc1 executable repeatedly with backend 1. The
failure reproduced on run 9 with exit 134 and the `py_list_append` assertion.

## Test [CONFIRMED]

`tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threads_and_explicit_gc_collect_all_backends[1]`
failed in the default xdist suite with exit `-6`; the preserved executable
then reproduced independently without pytest.

## Proposals

- No.1 Identify the first invalidation boundary with crash evidence [CONFIRMED]
- No.2 Publish the deallocating state before any safepoint [pending]

## No.1 Identify the first invalidation boundary with LLDB

### Code Change

Add a temporary fixed-size in-memory ring for list deallocation provenance and
print it only when `py_list_append` receives a non-list. This distinguishes a
tracing-sweep free from a refcount free without adding output or lock traffic
to the healthy path. Remove the ring before closing the investigation.

### CONFIRMED

The macOS crash report placed the invalid-list call at worker offset 400: the
first append into a newly allocated, pinned, frame-rooted `chunk`. A second
diagnostic run crashed in `py_dealloc_dict <- py_decref <-
py_func_call_kwargs` while releasing the per-call temporary kwargs dict. Both
signatures point to duplicate type-specific deallocation, not a list-specific
lowering error. ASan changed the stop-the-world timing and timed out without a
report, so it is denied as the deciding oracle for this race.

## No.2 Publish the deallocating state before any safepoint

### Code Change

Set `PY_FLAG_GC_DEALLOCATING` immediately after a real refcount transition to
zero, before logging, weakref invalidation, graph locking, or any safepoint.
Set the same bit for collector-owned finalization. Active-node scans already
reject this dedicated state. Unlike the denied `refcount == 0` rule, this does
not reject legitimate zero-count forwarding shells in backend 3.
