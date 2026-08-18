# GC4 A3b mixed-context tripwires

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b fatal-log holder sub-boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

Three C invariants used from both locked and unlocked contexts now route
conditionally: if the current thread owns the graph lock, they record the
existing static diagnostic in the deferred slot; otherwise they enter the
original fatal sink immediately. Instance-class validation returns before
reading corrupt class layout, negative object tags return before slot fan-out,
and remap type mismatch returns before rewriting the slot.

This covers `pcc_gc_visit_instance_owner_slots`, `py_obj_visit_slots` and
`pcc_gc_backend4_remap_heal_slot` only. Strict and remaining sites stay open.

## RED and gates

The expanded source contract was genuinely RED on direct
`PCC_RT_TRIPWIRE` calls (`1 failed in 0.09s`). Threads-off/on armed C syntax
passed. Final packet:

```text
5 passed in 7.40s
build/gc-mixed-context-tripwire-final.log
```

It covers the conditional source contract, armed valid and injected-failure
runtime paths, and the shared pcc-Python root-slot source contract.
`tests/python/test_gc_update_referents.py::test_backend4_relocation_reuses_shared_slot_contract`
was not evidence: it failed before this path because its historical
`from_slots` struct marker is absent from the current tree. `git diff --check`
exited zero.

## Frozen identities

```text
9098952b1f64e50efba167015cb25acc34e0be761806c4bfd62b17e504e52518  pcc/py_runtime/src/py_gc_backend.c
18d7be880ef285276b7c99f659e526da6ecdc95c95f3b6354423e57b5d245c17  tests/python/test_runtime_tripwires.py
5b3144a3d86cbeb3fd0c5dfcdddce6ec1528b788a77064340e91fa6e2f5cbef5  build/gc-mixed-context-tripwire-final.log
```

## Next boundary

Do not connect A3c. Finish remaining C/strict log-site classification and
design owner-referent promotion as a resumable remembered-slot worklist.
