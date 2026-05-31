# Investigation: Backend 3 non-list remembered slot rewrite

## Status
resolved

## Problem Description
Continue Backend #3 production work from `goal.md` No.8 after
`docs/investigations/gc-backend3-remembered-slot-rewrite.md` and
`docs/investigations/gc-backend3-pcc-py-remembered-slot-rewrite.md`.

The C runtime and pcc-Python runtime now rewrite remembered list item slots
eagerly when a young scalar child is copy-oldified.  The remaining remembered
owner path still treats non-list referents generically: tuple items, dict
values, set keys, and instance fields call the promotion visitor on the loaded
child value, but do not write the forwarded old copy back to the raw owner
slot.

This diverges from OCaml's minor collector shape in
`refs_docs/gc-research/ocaml/minor_gc.c`: `oldify_one` receives the slot
address and writes the promoted value through that address, and debug code
asserts remembered entries no longer point into the young heap after minor
collection.

Reduced target for this slice: in both `libpy_runtime.a` and
`libpy_runtime_pcc_py.a`, Backend #3 minor collection should rewrite raw
non-list owned slots for representative containers:

- tuple item;
- dict value;
- set key;
- instance field.

## Repro
Run the focused C-runtime gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy' \
  -q -n0
```

Run the focused pcc-Python-runtime gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy' \
  -q -n0
```

Expected after the fix: each probe prints `1 1 1 1`, meaning tuple, dict, set,
and instance raw slots all point at the forwarded old copy rather than the
minor source object.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy' \
  -q -n0
```

Result: `FAILED` in `3.86s`.  The probe printed
`['0', '0', '0', '0']` instead of `['1', '1', '1', '1']`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy' \
  -q -n0
```

Result: `FAILED` in `26.08s`.  The pcc-Python archive probe also printed
`['0', '0', '0', '0']` instead of `['1', '1', '1', '1']`.

## Proposals
- No.1 Use slot-aware promotion for owned non-list referents     [CONFIRMED]

## No.1 Use slot-aware promotion for owned non-list referents
### Code Change
Teach Backend #3 remembered-owner promotion to use the existing slot rewrite
helper for non-list owned references:

- tuple `items[i]`;
- dict live-entry `key` / `value`;
- set live-entry `key`;
- function captures;
- iterator sequence;
- generator frame/send value;
- coroutine captures/args/result;
- exception message/cause/context;
- instance declared fields and dynamic-attribute dict slot;
- weakref callback;
- thread callable/args/result.

Borrowed class metadata arrays remain on the generic promotion visitor until a
separate class/root slot-update investigation, because those pointers do not
have the same refcount ownership contract.

### CONFIRMED
Landed in the C runtime and pcc-Python runtime mirror:

- `pcc/py_runtime/src/py_gc_backend.c` now dispatches remembered-owner
  promotion through `pcc_gc_promote_young_slot()` for owned non-list slots.
- `pcc/py_runtime/py/py_gc_backend.py` mirrors the same slot-aware promotion
  offsets and adds the missing Backend #3 thread-object promotion path.

Focused gates now pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy' \
  -q -n0
```

Result: `1 passed in 9.00s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_non_list_owned_slots_to_oldified_copy' \
  -q -n0
```

Result: `1 passed in 26.33s`.

Broader verification:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Result: `15 passed in 149.48s`.

Runtime archive rebuilds passed:

- `make -B -C pcc/py_runtime libpy_runtime.a`
- default `make -B -C pcc/py_runtime ... libpy_runtime_pcc_py.a`
- `PCC_WITH_THREADS=1 make -B -C pcc/py_runtime ... libpy_runtime_pcc_py.a`
- default `make -B -C pcc/py_runtime ... libpy_runtime_pcc_py.a` again to
  restore the no-thread archive before default GC gates.

Full GC gates passed:

```bash
env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Result: `210 passed in 310.89s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Result: `210 passed in 306.06s`.

## Report (only when the investigation is closing)
No.1 landed.  Backend #3 remembered-owner promotion now rewrites
representative non-list owned slots to the forwarded old copy in both runtime
implementations.  The new gate covers tuple items, dict values, set keys, and
instance fields; the implementation also applies the same owned-slot rule to
function, iterator, generator, coroutine, exception, weakref callback, and
thread object slots.

This does not complete Backend #3 production.  Remaining work still includes
class/borrowed metadata update policy, root and suspended-frame reference
updates, cross-domain remembered-set sharing, forwarded-minor cleanup policy,
and broader pcc-Python threaded object-index/object-list synchronization.
