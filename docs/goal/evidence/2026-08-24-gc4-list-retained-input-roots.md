# GC4 list retained-input roots across growth/callback boundaries — 2026-08-24

## Claim

List operations that can cross the safepointable growth planner no longer keep
unregistered managed owner/item/source/replacement locals and then reuse stale
forwarding shells.  C and strict pcc-Python register updateable temporary roots,
reload after growth and before raw access, and unregister on success and every
explicit failure path for:

- generic append and insert owner/item;
- fresh-native-instance append owner (the unpublished item remains under its
  separate constructor-publication contract);
- list/tuple/iterator extend destination and source;
- slice source and growing output (`py_list_append` / strict `_push_to_list`);
- C set-slice destination and replacement; and
- strict split-member `py_list_set_slice.py`, which now calls the common
  transaction-aware `pcc_list_grow_for_mutation` cross-object helper instead of
  private `realloc`.

Non-backend0 append/insert/extend element commits also take an outer graph
lease before deriving the raw destination slot; nested `pcc_gc_store_ptr` then
runs inside the same no-park tenure.  Backend0 retains dedicated direct paths
and never allocates/registers temporary root handles or acquires the graph.

This is a retained-input/root-lifetime sub-claim.  Non-growing operations that
cache list items across equality/comparison/decref/iterator callbacks remain
open, as do full callback revalidation semantics for complex set-slice edits.

## Dynamic forwarding proof

A list source is explicitly forwarded before being passed through two growth
paths as the stale source pointer:

1. generic append stores the list as an element while growing a capacity-four
   holder; and
2. list extend reads two elements while growing a capacity-four destination.

C and strict both store/read the forwarding target rather than the source shell,
preserve the two source payload values, and return the scheduler-root count to
the two deliberately persistent test roots.  This directly exercises stale
input reload; no test-only production pause or publication hook was added.

The existing three-party list/set matrix independently proves STW waits for a
real growing mutation and then relocates/remaps/retires the container source.
Together the tests cover retained-input canonicalization and container raw-base
quiescence without relying on a timing race.

## Performance boundary

Backend0 append, fresh append, insert, extend, `_push_to_list`, and the common
growth helper branch before any root/graph machinery.  They retain direct
`realloc`/raw iteration plus the existing refcount store helper.  No timed
stage2 claim is made; source contracts prove the moving-GC root protocol is not
executed by the default backend0 path.

## Gates

- C syntax, threads off/on: pass (one pre-existing unrelated pointer warning).
- strict `py_list.py` and `py_list_set_slice.py` self-backend/no-libpython
  closures: pass.
- list/barrier/fresh-instance/copy/repeat final neighbors:
  `16 passed in 10.76s`.
- C/strict stale-forwarded item/source proof: `2 passed in 2.68s`.
- list pending-slot plus real list/set three-party matrix:
  `6 passed in 2.32s`.
- split set-slice compile and runtime assignment: `2 passed in 1.87s`.
- task-card relocation payload/forwarding retirement gate:
  `24 passed in 7.41s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-retained-neighbors-final.log`
- `build/gc4-list-retained-forwarded-final.log`
- `build/gc4-list-retained-pthread-final.log`
- `build/gc4-list-set-slice-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
e03d26eb5645bd532390caf3c876cfb0851056535aafa9896334e9fbbb360b15  pcc/py_runtime/src/py_list.c
61967ba19eb5ba22c45bd4be440bd7ee8573812863778486a881741f1a625b42  pcc/py_runtime/py/py_list.py
26d5a2decf43c80e1d6f7d9d99bc8784f19b700a4648896546ad16e5be8da5f8  pcc/py_runtime/py/py_list_set_slice.py
3b5afb24bd1530eef448bb8b41e09b494796cecc5614a15f7763af5c02fa3e55  pcc/py_frontend/codegen/runtime_abi.py
11f37e2eed61ccfdd83e6b4a4d69032c5f09dcf3ae0e434d4774f7d56dccb959  tests/python/test_gc_codegen_write_barrier.py
e2e3f7e4551ebc97a82da927cd76e3bf207d9ad02b80df69d8042df44faf73e5  tests/python/test_gc_threading_substrate.py
f5dafd70e7b6ae97b2dbe37b40014e027133306a24aa0a2b792a40661499d499  build/gc4-list-retained-neighbors-final.log
c4646f82b54d7674cce4c7bfe12fb1cc68fa87676513621ed83eb4280fd41249  build/gc4-list-retained-forwarded-final.log
5491f82f475e84b96677330e748043712838e884ec75160713abca4f8c3d5fdb  build/gc4-list-retained-pthread-final.log
7c1455c3ab2b016e06fa61fda308c7e3664cbcf01a7fa9b416ee9951e3d65a11  build/gc4-list-set-slice-final.log
58a4fb52f2c60427bb70505be4292efe3f627d5f329304fd766aca4973a4ebf7  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for retained managed inputs reused across list growth and for the
strict split set-slice growth route.  Parent task remains `IN_PROGRESS`; next
inventory and close non-growing list APIs that retain raw bases/slots across
Python callbacks or decref/finalizer re-entry.
