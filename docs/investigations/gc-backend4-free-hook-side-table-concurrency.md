# Investigation: Backend #4 free hook must lock side-table removal

## Status
resolved

## Problem Description
Backend #4 collector/read-barrier paths now take the graph lock while reading
the object graph, forwarding table, and relocation set. The C runtime free hook
still calls `pcc_gc_forwarding_remove()`, `pcc_gc_identity_remove()`, and
`pcc_gc_relocation_set_remove()` before taking the graph lock. A mutator freeing
an object can therefore remove side-table nodes while read-barrier threads
traverse them.

The pcc-Python runtime mirror already takes `_object_graph_lock()` before these
free-hook removals.

## Repro
Run the focused ThreadSanitizer gate:

```bash
CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_colored_relocating_free_hook_threadsanitizer_or_skip' -q -n0
```

Expected result after the fix: Backend #4 read-barrier threads can run while
the runtime free hook removes forwarding, identity, relocation-set, and
object-list entries without a TSan data-race report.

## Test [CONFIRMED]
Before the fix, the focused TSan gate failed in 4.55s. TSan reported
`pcc_gc_note_object_freeing()` writing a forwarding node while
`pcc_gc_note_relocation_read()` read the same node:

```text
SUMMARY: ThreadSanitizer: data race py_gc_backend.c:2460 in pcc_gc_note_object_freeing
```

The initial probe also included a collector thread. After the free-hook lock
fix, that broader probe exposed a separate Backend #4 production gap: removing
forwarding entries can make former forwarding targets selectable again, so an
unbounded collector loop can keep relocating work instead of reaching idle.
This investigation was narrowed back to the confirmed free-hook/read-barrier
race; the relocation phase-progress issue remains follow-up No.9 work.

## Proposals
- No.1 Lock C runtime free-hook side-table removal     [CONFIRMED]

## No.1 Lock C runtime free-hook side-table removal
### Code Change
Make `pcc_gc_note_object_freeing()` take `pcc_gc_graph_lock()` before removing
forwarding, identity, relocation-set, and object-list nodes, matching the
pcc-Python mirror's lock boundary.
### CONFIRMED
Implemented the C runtime lock boundary change in
`pcc_gc_note_object_freeing()`: the graph lock is now held before
`pcc_gc_forwarding_remove()`, `pcc_gc_identity_remove()`,
`pcc_gc_relocation_set_remove()`, and object-list unlink/freeing state updates.
The `!pcc_gc_tracks_objects()` early return now unlocks before returning.

Verification:

```bash
CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_colored_relocating_free_hook_threadsanitizer_or_skip' -q -n0
# 1 passed in 5.66s

CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_concurrent_collection.py -q -n0
# 10 passed in 78.24s

env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py -q -n0
# 36 passed in 236.75s
```

## Report (only when the investigation is closing)
No.1 landed. The C runtime now matches the pcc-Python mirror for free-hook
side-table removal: forwarding, identity, relocation-set, and object-list
updates happen under the same graph lock used by read-barrier and relocation
step traversal. This closes the confirmed `pcc_gc_note_object_freeing()` vs
`pcc_gc_note_relocation_read()` forwarding-table race. Backend #4 still needs a
real relocation phase/progress policy so targets do not become repeatedly
selectable after forwarding entries are removed.
