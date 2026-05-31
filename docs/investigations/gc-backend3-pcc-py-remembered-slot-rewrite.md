# Investigation: Backend 3 pcc-Python remembered slot rewrite

## Status
resolved

## Problem Description
Continue Backend #3 production work from `goal.md` No.8 after
`docs/investigations/gc-backend3-remembered-slot-rewrite.md`.

The C runtime now rewrites remembered list item slots eagerly when it
copy-oldifies a young scalar child.  The pcc-Python runtime mirror still has
the lazy-only behavior: `_trace_referents_for_promotion()` reads `items[i]` and
calls `_promote_young_if_known()`, but it does not write the forwarded old copy
back to the list slot.  Runtime paths that inspect the raw list slot can
therefore still observe the minor source object until a later
`pcc_gc_load_ptr()` call happens.

Reduced target for this slice: in `libpy_runtime_pcc_py.a`, when an old list
stores a young integer child and minor arena pressure triggers collection, the
old list's raw item slot should point at the oldified copy before any explicit
`pcc_gc_load_ptr()` call.

## Repro
Run the focused pcc-Python runtime Backend #3 gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_remembered_list_slot_to_oldified_copy' \
  -q -n0
```

Expected after the fix: the probe prints `1 1 1 0`, meaning a forwarding copy
exists, the old list slot was rewritten to the forwarded copy, the slot no
longer points at the minor source, and the forwarded copy is not minor-arena
backed.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_remembered_list_slot_to_oldified_copy' \
  -q -n0
```

Result: `FAILED` in `23.63s`.  The probe printed
`['1', '0', '0', '0']` instead of `['1', '1', '1', '0']`: a forwarding copy
exists and is not minor-arena-backed, but the pcc-Python runtime's old list raw
slot still points at the minor source object until a later load barrier updates
it.

## Proposals
- No.1 Mirror remembered list slot rewrite in pcc-Python runtime     [CONFIRMED]

## No.1 Mirror remembered list slot rewrite in pcc-Python runtime
### Code Change
Teach the pcc-Python Backend #3 remembered-owner scan to rewrite list item
slots when `_generational_oldify_copy()` returns a forwarded copy.  This should
mirror the C runtime list-slot slice and keep non-list referents on the
existing generic promotion visitor until broader container/root/suspended-frame
updates are implemented.

Landed implementation:

- add `_promote_young_slot(slot_base, slot_offset)` in
  `pcc/py_runtime/py/py_gc_backend.py`;
- skip null/tagged-int slots;
- call `_generational_oldify_copy(child)`;
- when the returned object differs from the minor source, incref the oldified
  copy, store it back to the raw list item slot, and decref the minor source;
- fall back to `_promote_young_if_known(child)` when no copy-oldification
  happened.

This does not complete Backend #3 production.  Full production still requires
tuple/dict/set/instance/class/function/thread/root/suspended-frame slot
updates, cross-domain remembered-set sharing, forwarded-minor cleanup policy,
and broader threaded object-index/object-list synchronization.

### CONFIRMED
The focused gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_remembered_list_slot_to_oldified_copy' \
  -q -n0
```

Result: `1 passed in 30.01s`.

Broader verification:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Result: `13 passed in 117.20s`.

```bash
env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Result: `208 passed in 282.64s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Result: `208 passed in 276.67s`.

The default and `PCC_WITH_THREADS=1` `libpy_runtime_pcc_py.a` rebuilds also
passed.  The final archive state was restored to the default no-thread build
before running the default GC suite.

## Report (only when the investigation is closing)
No.1 landed.  This closes the pcc-Python runtime mirror gap for remembered
old-list item slots: raw `items[i]` now points at the copied old object after
Backend #3 minor collection, matching the C runtime list-slot behavior.

This does not complete Backend #3 production.  Remaining work is still tracked
under `goal.md` No.8: tuple/dict/set/instance/class/function/thread/root and
suspended-frame slot updates, cross-domain remembered-set sharing,
forwarded-minor cleanup policy, and broader pcc-Python threaded
object-index/object-list synchronization.
