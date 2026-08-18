# GC4 list capacity-growth transaction — 2026-08-24

## Claim

List capacity growth no longer uses in-place `realloc(items)` on moving/tracing
backends.  C and strict pcc-Python now keep a moving list in an updateable root,
snapshot canonical owner/items/length/capacity/backend under the GC graph,
allocate and zero a replacement array outside the lock, re-lock and revalidate,
then copy healed items, retarget pending GC4 slot/span metadata, emit move
barriers and publish items/capacity in one graph/no-park tenure.  Old item arrays
are freed only after unlock; the helper returns/updates the canonical owner so
the caller cannot continue through a forwarded shell.

This closes list raw-base replacement itself.  It does not yet claim every
multi-element list operation: incoming item/source/replacement locals that live
across a growth safepoint or callback still require their own updateable-root
inventory and reload proof.  The parent P0 therefore remains open.

## Performance boundary

Backend 0 keeps the original direct `realloc(items)` path in both runtimes.  It
does not register a scheduler root, acquire the GC graph, allocate a slot map or
invoke moving-GC barriers/retargeting.  No timed stage2 speedup is claimed, but
the source contract proves the GC4 correctness machinery is absent from the
default backend0 growth path.

## Pending-slot semantics

With an OLD list at capacity four and four pending young tuple edges, the fifth
append grows the table to capacity eight.  C and strict prove:

- all four old slot addresses disappear from remembered-page state;
- all five live new slot addresses are remembered;
- pending buffer entries change from four to exactly five, not nine;
- copied barriers are duplicates and only the newly appended edge increments
  enqueue telemetry; and
- draining from the retargeted slots preserves all five values.

List slice output growth was also changed from `py_incref + raw store` to
NULL-init plus `pcc_gc_store_ptr`, matching append ownership/barrier semantics.

## Three-party pthread proof

The no-production-hook three-party test now covers both set rehash and a real
capacity-crossing list append, each under C and strict runtimes.  A mutator owns
an outer recursive graph/no-park lease while the collector publishes STW.  The
collector cannot acquire before the real container mutation commits.  After
unlock it drains the retargeted edge, explicitly selects the container,
relocates it, remaps/retires the source and resumes.

For list, final evidence proves length five, exact appended tuple payload, root
rewritten to the target, forwarding population zero, source retired and target
refcount exactly one.

## Gates

- C syntax, threads off/on: pass (one pre-existing unrelated
  `PyClassObject *` warning).
- strict `py_list.py` self-backend/no-libpython closure: pass.
- list/barrier/fresh-instance/copy/repeat neighbors: `15 passed in 11.12s`.
- list extend barrier, remembered-slot relocation and C/strict list-copy
  ownership: `4 passed in 1.21s`.
- final list pending-slot plus list/set three-party C/strict matrix:
  `6 passed in 2.47s`.
- store/refcount plus five-GC abstraction neighbors: `23 passed in 11.45s`.
- final task-card relocation payload/forwarding retirement gate:
  `24 passed in 12.03s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-growth-focused.log`
- `build/gc4-list-growth-relocation-neighbors.log`
- `build/gc4-list-growth-pthread-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
c1c9cb8d102dc6f8103adcf508693cfd4e6d6c8d40b2025f630f1c27d9650287  pcc/py_runtime/src/py_list.c
6162bb42b89e1bbede524d4c0328294125d9c638faad30f82b9d5c58e4aa25ef  pcc/py_runtime/py/py_list.py
d848705f27eb37ce441d6994a4d32121edd093cf9fc328986d3de4aa6efe6836  pcc/py_runtime/src/py_gc_backend.c
f87d4686b9e544e35074a9e1b89c38517f71a0493c6938ae140c8825ca28791d  pcc/py_runtime/py/py_gc_backend.py
3262ed00e48c966651140c4721cb5418c15d79bca7fc105a3e7524e343536adb  pcc/py_runtime/src/py_internal.h
1e72db7b8978863ece87b80aa57ba8c07ae547e6c2a4c495aaf46585eceb69fe  pcc/py_frontend/codegen/runtime_abi.py
1db61e7bd1c584358c559ad208f018f4e8a10b30c6385db60282c8cf18ffa1ab  tests/python/test_gc_threading_substrate.py
2805b882c8ad8c2949b5c80f430291cc5fe92be3dfce0a231d583774caf0af98  tests/python/test_gc_codegen_write_barrier.py
baae1c944de5a17e8ef848a46a674979183aef9e51c496e7d184b223aed21586  build/gc4-list-growth-focused.log
e88709b2bf377e1b7bd2fbf96eaaf6e9223af7a39b775d371aaa020ef8cbe678  build/gc4-list-growth-relocation-neighbors.log
dac7bd440c7f3c6cc0c022bbbe904f4c7f00cbd789235318c072298d37bcaecb  build/gc4-list-growth-pthread-final.log
3b1adfbeaa877cabc881ca7578712155b6009e7b592b9bbb395dad5b95b9546c  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for list capacity-growth raw-base replacement and the real list
three-party STW/relocation/retirement proof.  Parent task remains
`IN_PROGRESS`; next close updateable retained-argument/source roots and reloads
for every list operation that can cross this new growth safepoint or a Python
callback, then continue the remaining constructor/C-API/callback boundaries.
