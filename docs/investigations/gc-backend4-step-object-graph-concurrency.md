# Investigation: Backend #4 step relocation must lock object graph traversal

## Status
resolved

## Problem Description
Backend #4 public forwarding install/read paths are now graph-lock protected,
but the C runtime's step relocation path still selects relocation candidates by
walking `pcc_gc_objects` without taking the graph lock. A mutator allocating
objects can update the object graph while a collector thread runs
`pcc_gc_step()` and traverses the same list.

## Repro
Run the focused ThreadSanitizer gate:

```bash
CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_colored_relocating_step_allocation_threadsanitizer_or_skip' -q -n0
```

Expected result after the fix: a Backend #4 collector thread can run
`pcc_gc_step()` concurrently with a mutator allocation thread without a TSan
data-race report, and the probe observes at least one relocation forwarding.

## Test [CONFIRMED]
Before the fix, the focused TSan gate failed in 5.67s with data races between
the collector thread and mutator allocation:

- `pcc_gc_select_relocation_set()` read `pcc_gc_objects` while
  `pcc_gc_note_object_allocated_sized()` wrote it.
- `pcc_gc_relocate_selected()` / `pcc_gc_known_object_size()` read object-list
  nodes while allocation linked new nodes.

After the fix, the same gate passes and observes relocation forwarding.

## Proposals
- No.1 Lock Backend #4 selection/copy step traversal     [CONFIRMED]

## No.1 Lock Backend #4 selection/copy step traversal
### Code Change
Make the C runtime match the pcc-Python mirror's graph-lock discipline for
Backend #4 step operations: lock around relocation-set selection and copy
preflight/object-list traversal, keeping the existing reentrant graph lock for
nested allocation and forwarding calls.
### CONFIRMED
Implemented locked public wrappers plus `_unlocked` helpers for Backend #4
relocation-set selection, known-size object-list lookup, relocation copy, and
selected relocation traversal. The collector now traverses `pcc_gc_objects` and
`pcc_gc_relocation_set` under `pcc_gc_graph_lock()`, while nested allocation and
forwarding install continue to use the existing reentrant lock path.

Verification:

```bash
CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_concurrent_collection.py::test_colored_relocating_step_allocation_threadsanitizer_or_skip' -q -n0
# 1 passed in 4.61s

CC=clang env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_concurrent_collection.py -q -n0
# 9 passed in 66.13s

env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py -q -n0
# 36 passed in 234.09s
```

One intermediate full-file run saw
`test_cms_worker_threadsanitizer_stress_or_skip` report zero CMS worker drains;
the same node passed immediately when rerun alone (`1 passed in 5.96s`) and the
full file then passed. That failure is not attributed to Backend #4 relocation
locking.

## Report (only when the investigation is closing)
No.1 landed. The change keeps the lock boundary on the C runtime's graph data
structures rather than adding atomics to individual list links; this matches the
pcc-Python mirror and the existing C graph-lock model for tracing and
allocation. It closes the confirmed `pcc_gc_step()` vs allocation races for the
current colored-relocating backend. Follow-up work remains for page evacuation,
fragmentation policy, and broader mutator relocation stress.
