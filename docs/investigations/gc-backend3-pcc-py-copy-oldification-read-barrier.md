# Investigation: Backend 3 pcc-Python copy oldification read barrier

## Status
resolved

## Problem Description
Continue Backend #3 production work from `goal.md` No.8 and follow up
`docs/investigations/gc-backend3-copy-oldification-read-barrier.md`.

The C runtime now oldifies a remembered young scalar child by copying it out of
the minor arena, installing a forwarding record, and letting `pcc_gc_load_ptr()`
lazily update the old owner slot.  The pcc-Python runtime mirror still has the
old behavior: Backend #3 promotion flips `PY_FLAG_GC_YOUNG` to
`PY_FLAG_GC_OLD` in place, `pcc_gc_install_forwarding()` only accepts Backend
#4, and `pcc_gc_load_ptr()` only runs the read barrier for Backend #4.

Reduced target for this slice: the pcc-Python runtime archive
`libpy_runtime_pcc_py.a` must match the C runtime for an old list storing a
young integer child when minor arena pressure triggers a minor collection.

## Repro
Run the focused pcc-Python runtime Backend #3 gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child' \
  -q -n0
```

Expected after the fix: the probe prints `1 1 1 1 0`, meaning the pcc-Python
runtime creates a forwarding copy, `pcc_gc_load_ptr()` returns and stores that
copy, the copy is old, and the copy is not minor-arena-backed.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child' \
  -q -n0
```

Result: `FAILED` in `23.74s`.  The probe printed
`['0', '1', '1', '1', '1']` instead of `['1', '1', '1', '1', '0']`:
`pcc_gc_note_relocation_read(child)` returned the original child,
`pcc_gc_load_ptr()` did not move the owner slot to a copied object, and the
object was merely marked old while still carrying `PY_FLAG_GC_MINOR_ARENA`.

## Proposals
- No.1 Mirror C runtime scalar copy-oldification in pcc-Python runtime     [CONFIRMED]

## No.1 Mirror C runtime scalar copy-oldification in pcc-Python runtime
### Code Change
Mirror the narrow C runtime slice in `pcc/py_runtime/py/py_gc_backend.py` and
`pcc/py_runtime/py/py_obj.py`:

- let forwarding infrastructure serve Backend #3 and Backend #4;
- process remembered owners before generic young promotion;
- copy supported scalar remembered young objects into non-minor old storage;
- register the copy in the pcc-Python object list and object-index table;
- use the read barrier in `pcc_gc_load_ptr()` for Backend #3 and Backend #4.

Landed change:

- `_backend_uses_forwarding()` lets the pcc-Python forwarding side tables serve
  Backend #3 and Backend #4.
- `_generational_oldify_copy()` copies supported remembered young scalar
  objects with `malloc + memmove`, clears young/minor/remembered/relocation
  flags on the copy, marks it old, registers it in the object list and
  `pcc_gc_object_index`, then installs forwarding from the minor source object.
- `_step_generational_promotion()` now scans remembered owners before generic
  young promotion, matching the C runtime and preventing the child from being
  old-marked in place before oldification sees it.
- `pcc_gc_load_ptr()` in `py_obj.py` now runs the read barrier for Backend #3
  and Backend #4.
- The old abstraction-surface processed-count assertion was updated from `2`
  to `1`: a remembered owner that oldifies its child now accounts for the
  remembered-owner unit of work, and the source child is no longer separately
  promoted in the later young pass.

This still is not full Backend #3 production.  Full production still requires
all traced-slot/root/suspended-frame reference updates, cross-domain
remembered-set sharing, forwarded minor cleanup policy, and broader pcc-Python
threaded object-index/object-list synchronization.

### CONFIRMED
The focused pcc-Python parity gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_oldifies_copy_for_remembered_child' \
  -q -n0
```

Result: `1 passed in 24.41s`.

Broader regression gates also passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
# 11 passed in 85.67s

env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 420s uv run pytest \
  'tests/test_gc_abstraction_surface.py::test_generational_gc_remembered_set_promotes_young_child' \
  -q -n0
# 1 passed in 0.82s

env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
# 206 passed in 234.89s

env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
# 206 passed in 222.58s
```

Default and `PCC_WITH_THREADS=1` pcc-Python runtime archive rebuilds passed.
After the threaded rebuild, the repository archive was rebuilt again in the
default no-thread configuration before running the default/backend matrix.

## Report (only when the investigation is closing)
Proposal No.1 landed.  The pcc-Python runtime mirror now matches the C runtime
for this Backend #3 scalar oldification slice: remembered young scalar children
can be copied out of the minor arena, followed through forwarding, and lazily
written back through `pcc_gc_load_ptr()`.

This closes pcc-Python parity for the narrow scalar-copy/read-barrier case.  It
does not complete Backend #3 production: full container/root/suspended-frame
reference rewriting, cross-domain remembered-set sharing, forwarded-minor
cleanup policy, and broader threaded object-index/object-list synchronization
remain open goal items.
