# GC4 dict/set rehash raw-payload transaction — 2026-08-24

## Claim

Dict and set rehash now replace their pointer-bearing raw tables under one
owner-canonical graph-lock/no-park transaction in the C and strict pcc-Python
runtimes.  Replacement allocation and initialization occur outside the lock;
an updateable registered owner root spans the unlocked planning window for
moving backends.  Snapshot and commit are separated, and commit revalidates the
backend, canonical owner, exact old base, capacity, used/fill count and logical
size before the first mutation.

The callback-free commit copies live entries with stored hashes and raw
occupancy only, retargets every matching pending GC4 store-buffer slot,
remembered-slot/page/card entry and payload span through an exact
`(old_slot,new_slot)` map, emits move barriers, publishes the completed owner
metadata, then unlocks before freeing the old raw bases and unregistering the
temporary root.  Deleted/tombstoned pending slots use a same-offset slot in the
fully zeroed new span, so no queue or remembered entry retains a freed address.

This closes the dict/set raw-base sub-claim of
`GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`.  It does not close the parent task:
list growth still uses `realloc(items)` without the same transaction, and the
remaining constructor, C-API lease, callback-root, resurrection and fairness
boundaries remain open.

## Performance boundary

Backend 0 has explicit C/strict refcount-only dict/set rehash fast paths.  They
use the callback-free raw placement helper but do not acquire the GC graph,
register a root, allocate slot-pair maps or call moving-GC barriers/retargeting.
This prevents the GC4 correctness mechanism from taxing the default stage2
runtime path.  No timed stage2 improvement is claimed here; source shape only
proves the new correctness path did not become mandatory for backend 0.

## Pending-edge semantics

The original GC4 set test expected two barrier enqueues at growth: one for the
insert into the old table and one for the move.  That proxy is no longer
correct.  Commit retargets the already-retained edge to the new slot before
the old table is freed, so the move barrier correctly sees a duplicate:

- enqueue/barrier counter remains exactly one;
- every old table slot is absent from remembered-page state;
- the actual new slot holding the young key is remembered;
- pending edge count remains one; and
- draining that edge promotes from the new live slot and preserves contents.

Both set and dict are proven under C and strict runtimes.  No pending edge is
dropped or prematurely promoted merely to make rehash safe.

## Three-party pthread proof

A real set grows across capacity 8 while a mutator owns an outer recursive
graph/no-park lease and a collector has published a stop-the-world request.
The collector cannot acquire the world before `py_set_add` commits the actual
rehash.  After outer unlock, the collector acquires STW, drains the retargeted
pending edge, explicitly selects the set for relocation, copies it, performs
bounded remap/retirement while preserving caller-owned STW, and resumes.

C and strict both prove:

- mutation committed before STW acquisition;
- set length and inserted-key membership are six/correct;
- root is rewritten to the relocation target;
- forwarding population returns to zero within four remap epochs;
- the retired source no longer owns the root; and
- the target's final root-owned refcount is exactly one.

The explicit relocation-set add is an internal test seam that already existed
in the strict cross-object ABI; this slice added its missing C parity wrapper.
Its node is prepared before graph acquisition, so the wrapper introduces no
allocator into its locked commit.

## Gates

- C syntax, threads off/on: pass (one pre-existing unrelated
  `PyClassObject *` warning in `py_dict.c`).
- strict `py_gc_backend.py`, `py_dict.py`, `py_set.py` self-backend,
  no-libpython single-module closures: pass.
- ABI/refcount/barrier/rehash/three-party focused set: `24 passed in 26.30s`.
- final C/strict pending-slot plus STW/relocation/retirement matrix:
  `4 passed in 148.50s`.
- final C/strict dict/set relocation-copy ownership: `4 passed in 1.05s`.
- relocation-copy source/archive gate before the backend0-only fast-path
  branch: `10 passed in 152.75s`; final-source dict/set copy and three-party
  relocation gates above cover the changed container modules.
- final task-card relocation payload/forwarding retirement gate:
  `24 passed in 144.16s`.
- final five-GC abstraction surface: `15 passed in 10.20s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-rehash-focused-final.log`
- `build/gc4-rehash-pthread-final.log`
- `build/gc4-rehash-dict-set-copy-final.log`
- `build/gc4-rehash-abstraction-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
d848705f27eb37ce441d6994a4d32121edd093cf9fc328986d3de4aa6efe6836  pcc/py_runtime/src/py_gc_backend.c
f87d4686b9e544e35074a9e1b89c38517f71a0493c6938ae140c8825ca28791d  pcc/py_runtime/py/py_gc_backend.py
3b6333b7f3073e6ffcfdb33ca219c860ad3b614c2f839c41bbc74d65c1c51033  pcc/py_runtime/src/py_dict.c
a899c6129f1f78083581746695d7c0928d4c7bcda1ab1bb00e89853d5eed13c0  pcc/py_runtime/py/py_dict.py
84e24d906c7045445979defe3a47aa7860bbd44e7ef228e0d6441994adce88d2  pcc/py_runtime/src/py_set.c
530b72e236b28ef3897d8f3d5114e77697afa0cb3586dfeb7cb4ddedc9e45967  pcc/py_runtime/py/py_set.py
3262ed00e48c966651140c4721cb5418c15d79bca7fc105a3e7524e343536adb  pcc/py_runtime/src/py_internal.h
1e72db7b8978863ece87b80aa57ba8c07ae547e6c2a4c495aaf46585eceb69fe  pcc/py_frontend/codegen/runtime_abi.py
8192eb8eb051906ced1892452744a41842124a42877814d91c2db076e8ab1732  tests/python/test_gc_threading_substrate.py
34f5f5e678caa5d308ae78df6ee01425d6c017964870dbac082b0bb20fe8ebba  tests/python/test_gc_codegen_write_barrier.py
1c26b9c57b1dae6ac7ceca668d19d4b02f4010bc728958be9e1ce218e00b3709  tests/python/test_gc_backend4_production.py
1ee1bba759da235473950c3a6869d063db59279e15dbc7869866e5527947c445  build/gc4-rehash-focused-final.log
60dfcd198cf288c73a526ad3dfa70d3accb07e7a290b3c7ec44688bc0a2ed670  build/gc4-rehash-pthread-final.log
f0a63090fcc405bed7e793503995b8393428ba9c592a23c5087b737380b9d50d  build/gc4-rehash-dict-set-copy-final.log
fed5c3934886fc808d9edacd275f2e1705472d173f68d36631a0795386ddf0a6  build/gc4-rehash-abstraction-final.log
0debe529cf7e05165fc104a5546763f07dcd2edc7ad8026a8baa5c8e0ccf2d82  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for dict/set rehash raw-base replacement and the associated real
set three-party quiescence/retirement proof.  Parent task remains
`IN_PROGRESS`; next boundary is list capacity growth/reallocation under the
same owner-rooted, pending-slot-retargeting transaction without penalizing
backend0.
