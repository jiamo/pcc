# Investigation: Backend #3 pcc-Python threaded object graph synchronization

## Status
resolved

## Problem Description
`goal.md` still lists broader pcc-Python threaded object-index/object-list
synchronization as a Backend #3 production gap.  The pcc-Python runtime-high
mirror now has thread-local minor current blocks, but its object graph registry
still mutates `pcc_gc_object_head` and calls the C-hosted
`pcc_gc_object_index_*` helpers without a shared graph lock.  Concurrent native
mutators can race while allocating/freeing Backend #3 minor objects.

## Repro
Run:

```
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_concurrent_collection.py::test_pcc_python_runtime_object_graph_threadsanitizer_or_skip' \
  -q -n0 -rxX
```

Expected before the fix: ThreadSanitizer reports a data race in the C-hosted
`pcc_gc_object_index_*` table when two threads allocate/release objects through
`libpy_runtime_pcc_py.a` built with `PCC_WITH_THREADS=1`.

## Test [CONFIRMED]
Observed before the fix:

```
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_concurrent_collection.py::test_pcc_python_runtime_object_graph_threadsanitizer_or_skip' \
  -q -n0 -rxX
```

Result: `FAILED` in `29.38s`.  ThreadSanitizer reported a data race on the
C-hosted pcc-Python runtime-high object-index table:

```
WARNING: ThreadSanitizer: data race
Write ... pcc_gc_object_index_insert py_gc_index_table.c:184
Previous read ... pcc_gc_object_index_insert py_gc_index_table.c:183
Location is global 'pcc_gc_object_index_buckets'
```

## Proposals
- No.1 Reuse the pcc-Python runtime-high graph lock for object graph registry access     [CONFIRMED]

## No.1 Reuse the pcc-Python runtime-high graph lock for object graph registry access
### Code Change
Protect pcc-Python runtime-high object-list and object-index mutation/traversal
with the existing C-hosted graph spinlock used by the threaded minor arena
substrate.  The lock must cover object registration, object freeing, minor
memory release, tracing/root seeding, promotion scans, and object-list clearing,
without double-locking paths that oldify or sweep while already walking the
graph.

### CONFIRMED
Landed the graph-lock protection in `pcc/py_runtime/py/py_gc_backend.py`.
Object registration/freeing, object-list traversals, relocation-set selection,
tracing, promotion, and object-list clearing now serialize object-list and
object-index access.  `pcc_gc_install_forwarding()` is split into an exported
locked wrapper and an internal unlocked helper so oldification can update
forwarding state while it already owns the graph lock.

The first post-lock run no longer reported the original ThreadSanitizer race,
but exposed a second bug:

```
[BAD_INCREF] o=... tag=-1
```

That came from pcc-Python runtime-high keeping `pcc_gc_pending_minor_block` as a
process-global scratch slot while the C runtime already used `_Thread_local`
state.  A native mutator could allocate from one minor block, another mutator
could overwrite the pending block before `pcc_gc_note_object_allocated_sized()`,
and the object node could be attached to the wrong minor block.  The fix adds
C-hosted TLS getters/setters in `py_runtime_high_substrate.c` and makes
`py_gc_backend.py` use them for pending minor blocks.

The second post-lock run exposed a deadlock in oldified-source cleanup.  During
`pcc_gc_note_object_freeing()`, removing a forwarding entry can `py_decref()` the
oldified target, which may recursively re-enter `pcc_gc_note_object_freeing()`
on the same thread.  The graph lock is now same-thread reentrant while still
serializing different native mutators.

Observed after the fix:

```
env -u LC_ALL CC=clang /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_concurrent_collection.py -q -n0 -rxX
```

Result: `5 passed in 39.51s`.

```
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Result: `21 passed in 204.79s`.

```
env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0
```

Result: `217 passed in 428.86s`.

```
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0
```

Result: `217 passed in 415.04s`.

Archive rebuilds also passed for C runtime, pcc-Python runtime-high default,
pcc-Python runtime-high `PCC_WITH_THREADS=1`, and final default restore.

## Report (only when the investigation is closing)
No.1 landed.  The original race was the unsynchronized pcc-Python object graph
registry; the two secondary failures found during verification were part of the
same threaded object-graph contract: pending minor-block state also had to be
per-thread, and the graph lock had to tolerate same-thread refcount/freeing
re-entry from forwarding cleanup.  This closes the Backend #3 pcc-Python
threaded object-index/object-list synchronization slice.  It does not close the
whole Backend #3 production task; scheduler/suspended-frame reference update and
cross-domain remembered-set sharing remain open.
