# Investigation: Backend 3 remembered slot rewrite

## Status
resolved

## Problem Description
Continue Backend #3 production work from `goal.md` No.8 after
`docs/investigations/gc-backend3-copy-oldification-read-barrier.md` and
`docs/investigations/gc-backend3-pcc-py-copy-oldification-read-barrier.md`.

Backend #3 can now copy-oldify a remembered young scalar child and lazy-update a
container slot through `pcc_gc_load_ptr()`.  That is not enough for production
generational semantics: runtime paths that read container memory directly can
still observe the minor source object until they happen to pass through the
load barrier.  The remembered-set scan should update the slot it just traced
when it installs a forwarding copy.

Reduced target for this slice: when an old list stores a young integer child
and minor arena pressure triggers collection, the old list's raw item slot
should point at the oldified copy before any explicit `pcc_gc_load_ptr()` call.

## Repro
Run the focused C-runtime Backend #3 gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_remembered_list_slot_to_oldified_copy' \
  -q -n0
```

Expected after the fix: the probe prints `1 1 1 0`, meaning a forwarding copy
exists, the old list slot was rewritten to the forwarded copy, the slot no
longer points at the minor source, and the forwarded copy is not minor-arena
backed.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_remembered_list_slot_to_oldified_copy' \
  -q -n0
```

Result: `FAILED` in `3.85s`.  The probe printed
`['1', '0', '0', '0']` instead of `['1', '1', '1', '0']`: a forwarding copy
exists and is not minor-arena-backed, but the old list raw slot still points at
the minor source object until an explicit `pcc_gc_load_ptr()` call updates it.

## Proposals
- No.1 Rewrite remembered list slots during Backend #3 oldification     [CONFIRMED]

## No.1 Rewrite remembered list slots during Backend #3 oldification
### Code Change
Teach the Backend #3 remembered-owner scan to rewrite pointer slots when
oldification returns a forwarded copy.  Start with C-runtime list slots as the
smallest production-relevant slot shape, then mirror the pattern into broader
container/root/suspended-frame updates in follow-up slices.

Landed change:

- added a C-runtime slot visitor for Backend #3 remembered list owners;
- when `pcc_gc_generational_oldify_copy()` returns a forwarded copy, the list
  slot is updated immediately, the copy is increfed for the slot, and the minor
  source object is decrefed;
- non-list remembered owners still use the previous generic promotion visitor
  and remain follow-up work.

This does not complete Backend #3 production.  Full production still requires
all traced container/root/suspended-frame slots, pcc-Python parity for eager
slot rewrite, cross-domain remembered-set sharing, forwarded-minor cleanup
policy, and broader threaded object-index/object-list synchronization.

### CONFIRMED
The focused C-runtime slot-rewrite gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_remembered_list_slot_to_oldified_copy' \
  -q -n0
```

Result: `1 passed in 3.77s`.

Broader regression gates also passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
# 12 passed in 92.86s

env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
# 207 passed in 248.67s

env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
# 207 passed in 288.22s
```

C runtime archive rebuild passed.

## Report (only when the investigation is closing)
Proposal No.1 landed.  Backend #3 C runtime now eagerly rewrites old list slots
when remembered-set scanning oldifies a young scalar child, so list readers that
inspect the raw slot no longer have to wait for `pcc_gc_load_ptr()` to observe
the oldified copy.

This closes the narrow C-runtime list-slot case.  It does not complete Backend
#3 production: tuple/dict/set/instance/class/function/thread/root/suspended
frame slot updates, pcc-Python eager slot rewrite parity, cross-domain
remembered-set sharing, and forwarded-minor cleanup policy remain open goal
items.
