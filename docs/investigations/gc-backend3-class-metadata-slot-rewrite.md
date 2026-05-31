# Investigation: Backend 3 class metadata slot rewrite

## Status
resolved

## Problem Description
Continue Backend #3 production work from `goal.md` No.8 after
`docs/investigations/gc-backend3-frame-root-slot-rewrite.md`.

Backend #3 now rewrites remembered container/instance/function/thread slots and
registered native frame root slots to forwarded old copies.  Class metadata is
still handled as borrowed generic trace metadata: `bases[]`, `mro[]`,
`methods[].func`, and `del_method` are visited with generic promotion helpers,
but their raw metadata slots are not rewritten when a copy-supported young child
is oldified.

`py_class_add_method()` also stores a borrowed method pointer directly into the
method table.  If a class is already old and a young method object is installed,
Backend #3 needs a write-barrier policy that marks the old class remembered
without changing borrowed refcount ownership.

Reduced target for this slice: in both `libpy_runtime.a` and
`libpy_runtime_pcc_py.a`, adding young class metadata to an old class and
triggering a Backend #3 minor refill should rewrite `methods[].func` and
`del_method` to the forwarded old copy.

## Repro
Run the focused C-runtime gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_class_metadata_slots_to_oldified_copy' \
  -q -n0
```

Run the focused pcc-Python-runtime gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_class_metadata_slots_to_oldified_copy' \
  -q -n0
```

Expected after the fix: each probe prints `1 1 1 1`, meaning two methods were
installed, `methods[0].func` was rewritten to the forwarded old copy,
`methods[1].func` was rewritten to the forwarded old copy, and `del_method`
points at the same forwarded old copy as the `__del__` method entry.

## Test [CONFIRMED]
Observed before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_class_metadata_slots_to_oldified_copy' \
  -q -n0
```

Result: `FAILED` in `3.97s`.  The probe printed `['1', '0', '0',
'0']` instead of `['1', '1', '1', '1']`: both methods were installed, but
neither method-table slot nor `del_method` was rewritten to the forwarded old
copy.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_class_metadata_slots_to_oldified_copy' \
  -q -n0
```

Result: `FAILED` in `25.14s`.  The pcc-Python archive probe printed the same
`['1', '0', '0', '0']` result.

## Proposals
- No.1 Add borrowed class metadata barrier and slot rewrite     [CONFIRMED]

## No.1 Add borrowed class metadata barrier and slot rewrite
### Code Change
Teach `py_class_add_method()` to record a Backend #3 write barrier for borrowed
method metadata without taking ownership of the method pointer.  Then add a
borrowed-slot oldification helper used by Backend #3 class promotion for
`bases[]`, `mro[]`, `methods[].func`, and `del_method`; unlike owned container
slots, it rewrites the slot without `py_incref` / `py_decref`.

Mirror the same behavior in `pcc/py_runtime/py/py_class.py` and
`pcc/py_runtime/py/py_gc_backend.py`.

This closes class metadata slots only.  Scheduler queues, suspended
generator/coroutine/task frames, cross-domain remembered-set sharing,
forwarded-minor cleanup, and broader pcc-Python threaded object-index/list
synchronization remain separate work.

### CONFIRMED
Implemented in both runtime implementations:

- `py_class_add_method()` now records a borrowed metadata write barrier so an
  old class that receives a young method object becomes remembered without
  changing method pointer refcount ownership.
- Backend #3 class promotion now uses borrowed-slot oldification for
  `bases[]`, `mro[]`, `methods[].func`, and `del_method`.
- The pcc-Python class mirror now matches the C `PyClassObject` layout at 104
  bytes, including the `del_method` slot at offset 96, and traces/promotes that
  slot.

Focused gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_class_metadata_slots_to_oldified_copy' \
  -q -n0
```

Result: `1 passed in 3.80s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_class_metadata_slots_to_oldified_copy' \
  -q -n0
```

Result: `1 passed in 26.35s`.

Broader gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_backend_generational.py -q -n0
```

Result: `19 passed in 202.26s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_python_class_features_parity.py tests/test_gc_finalizer_corner.py \
  -q -n0
```

Result: `20 passed in 15.41s`.

Runtime archives rebuilt successfully for C runtime, default pcc-Python
runtime, `PCC_WITH_THREADS=1` pcc-Python runtime, and final default pcc-Python
runtime restore.

```bash
env -u LC_ALL PCC_GC_BACKEND=3 /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Result: `214 passed in 359.40s`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  tests/test_gc_*.py -q -n0 -rxX
```

Result: `214 passed in 355.61s`.

## Report
Proposal No.1 landed.  Backend #3 now has an explicit policy for class metadata:
method/class metadata slots are borrowed for refcount ownership, but they still
participate in the generational remembered-set and reference-update model.
When a copy-supported young object stored in class metadata is oldified, the raw
metadata slot is updated in place without ownership refcount churn.

This does not close Backend #3 production.  Remaining work still includes
scheduler queues, suspended generator/coroutine/task frames, cross-domain
remembered-set sharing, forwarded-minor cleanup, and broader pcc-Python
threaded object-index/list synchronization.
