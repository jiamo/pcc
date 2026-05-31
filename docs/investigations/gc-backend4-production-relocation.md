# Backend #4 production relocation closure

This patchset finishes the Backend #4 production-facing relocation gate on top
of the existing forwarding table, relocation set, read barrier, container
payload copy, scheduler queue, task/coroutine, and stable-ID work already in
`py_gc_backend.c`.

## Added production checks

### Public telemetry

New telemetry counters:

- `PCC_GC_COUNTER_RELOCATION_SET_SIZE`
- `PCC_GC_COUNTER_FORWARDING_ENTRIES`
- `PCC_GC_COUNTER_STABLE_IDS`
- `PCC_GC_COUNTER_RELOCATION_FRAGMENTATION_SCORE`

New public helpers:

- `pcc_gc_backend4_verify_no_old_addresses()`
- `pcc_gc_backend4_fragmentation_score()`
- `pcc_gc_backend4_forwarding_entries()`
- `pcc_gc_backend4_stable_id_entries()`

These are mirrored into the pcc-Python runtime archive and codegen ABI.

### Stress gate

`tests/python/test_gc_backend4_production.py` adds native C harnesses that verify:

- repeated relocation steps move objects;
- roots follow forwarding through `pcc_gc_load_ptr`;
- stable IDs survive relocation;
- no forwarding entry points to itself or makes an old object a relocation
  target;
- relocated list payloads still preserve all elements;
- production telemetry is non-empty and callable.

### Combined gate

```bash
bash scripts/run_backend4_production_gate.sh
```

Runs:

- `tests/python/test_gc_backend_relocating.py`
- `tests/python/test_gc_backend4_production.py`
- `tests/python/test_gc_abstraction_surface.py`
- `tests/python/test_gc_coroutine_roots.py`

## Production verdict

Backend #4 now has a production gate for the pcc object model. It is not a
literal ZHeap page clone; it uses pcc's object registry and relocation-set
model, because pcc's runtime does not currently allocate objects out of ZGC
pages. The semantic requirements from `goal.md` are covered:

- forwarding table;
- relocation set;
- read barrier/following;
- true copied objects for relocatable runtime objects;
- stable `id` side table;
- scheduler/task/coroutine root following.

## Update: live evacuation debt telemetry

`PCC_GC_COUNTER_FORWARDING_ENTRIES` now reports the live forwarding-table
entry count, not the cumulative relocation count. Cumulative relocation events
remain available through `PCC_GC_COUNTER_RELOCATION_FORWARDS`.

`pcc_gc_backend4_fragmentation_score()` now represents active evacuation debt:

- pending relocation-set entries;
- live forwarding entries waiting for read barriers to update old slots.

Stable ID side-table entries are intentionally excluded from fragmentation
score. They are object identity metadata, not evacuation debt.

The regression `test_backend4_fragmentation_score_tracks_live_evacuation_debt`
proves the production invariant:

- a selected and copied object creates one live forwarding entry;
- loading the root through `pcc_gc_load_ptr()` updates the slot;
- freeing the old address removes the forwarding entry;
- fragmentation score returns to zero while the stable ID survives.
