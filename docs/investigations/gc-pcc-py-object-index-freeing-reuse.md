# Investigation: pcc-Python GC object index stale freeing entry

## Status
resolved

## Problem Description
While validating the No.42 Phase 2 suspended-root hook substrate, the broader
coroutine/root gate exposed a pcc-Python runtime mirror regression:

```
env -u LC_ALL uv run pytest \
  tests/python/test_gc_coroutine_roots.py tests/python/test_gc_root_precision.py \
  -q -n0
```

Before the fix, the C runtime cases passed but the pcc-Python runtime archive
failed:

```
2 failed, 5 passed in 74.50s
```

`test_pcc_python_runtime_suspended_heap_frame_local_survives_collect_across_backends`
reported backend #2 collecting a generator that was still reachable from a
scheduler root. The paired task waiter-cycle probe segfaulted in the
pcc-Python archive.

## Root Cause
`pcc/py_runtime/py/py_gc_backend.py::pcc_gc_note_object_freeing()` marked a
tracked non-minor object node as `freeing` but left the node linked and left
the C-hosted `pcc_gc_object_index_*` entry installed. When malloc reused the
same object address in a later backend pass, `pcc_gc_object_index_find()`
returned the old freeing node. `_is_known_object()` then treated the new object
as unknown, so `_gray_current_roots()` ignored the scheduler root and backend
#2 swept the still-rooted generator.

The C runtime also unlinked the non-minor object node without removing the
object-index entry, which left the same lifetime invariant under-specified even
though the focused C run did not reproduce the failure.

## Fix
Keep object-list and object-index lifetime in lockstep for non-minor freed
objects:

- C runtime: `pcc_gc_note_object_freeing()` removes the object-index entry
  before unlinking/freeing a non-minor `PccGcObjectNode`.
- pcc-Python runtime mirror: the unreachable remove/unlink/free block after the
  minor-block early return is now the normal non-minor cleanup path.

Minor-block nodes still stay indexed and marked `freeing` until their arena
block is released.

## Validation
After the fix:

```text
env -u LC_ALL uv run python -m py_compile pcc/py_runtime/py/py_gc_backend.py
passed

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime_pcc_py.a
passed

env -u LC_ALL uv run pytest \
  tests/python/test_gc_coroutine_roots.py tests/python/test_gc_root_precision.py \
  -q -n0
7 passed in 73.33s

env -u LC_ALL uv run pytest \
  tests/python/test_virtual_threads_gap.py \
  tests/python/test_gc_coroutine_scheduler_roots_production.py \
  -q -n0
5 passed, 3 xfailed in 13.15s
```
