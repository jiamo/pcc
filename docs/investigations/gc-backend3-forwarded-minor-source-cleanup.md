# Investigation: Backend #3 forwarded minor source cleanup

## Status
resolved

## Problem Description
Backend #3 copy-oldifies a young minor object into old storage and installs a
forwarding entry, but the original minor source remains in the active object
list and live-byte accounting.  The source must stay addressable while stale
locals release or read through the forwarding table, but it must not keep
participating in tracing, sweep-candidate marking, or live-object accounting.

## Repro
Run:

```
env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_forwarded_minor_source_is_inactive_after_oldify -q -n0
env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_forwarded_minor_source_is_inactive_after_oldify -q -n0
```

Expected failure before the fix: the probe prints `0` for the forwarded source
replacement-forwarding rejection check and then `0` for the unchanged-forwarding
check.  The expected values are `1` and `1`, meaning a stale copied-from minor
source is no longer an active object that can accept a new forwarding target.

## Test [CONFIRMED]
The two pytest nodes above are the fix gate.  They compile a C probe against
the C runtime and the pcc-Python runtime archive, force remembered-slot
copy-oldification, then try to install a replacement forwarding target from
the stale source.  A correct runtime keeps the existing stale-source forwarding
entry readable but rejects the replacement because the source is no longer an
active heap object.

Observed 2026-05-08 before the fix:

```
tests/test_gc_backend_generational.py::test_generational_backend_forwarded_minor_source_is_inactive_after_oldify
  output: 1 1 0 0 1
tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_forwarded_minor_source_is_inactive_after_oldify
  output: 1 1 0 0 1
```

The third line shows replacement forwarding was accepted.  The fourth line
shows the stale-source forwarding entry was overwritten.

## Proposals
- No.1 Mark forwarded minor source inactive after forwarding install     [CONFIRMED]

## No.1 Mark forwarded minor source inactive after forwarding install
### Code Change
After `pcc_gc_install_forwarding(from, to)` succeeds in Backend #3 oldification,
mark the original minor source node as inactive/freeing and subtract its live
bytes without unlinking it or releasing the minor arena block.  Active-object
queries and trace/promotion loops must skip freeing nodes, while final source
release still removes the forwarding entry and releases the minor block.

### CONFIRMED
C runtime and pcc-Python runtime mirror now share this policy:

- active object lookup/known-size/trace/promotion/sweep/relocation selection
  skip object nodes whose `freeing` flag is set
- Backend #3 oldification marks the copied-from source node inactive after the
  forwarding entry is installed
- pcc-Python `pcc_gc_note_object_freeing()` no longer subtracts live bytes a
  second time when the inactive source is later released

Observed after the fix:

```
env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_forwarded_minor_source_is_inactive_after_oldify -q -n0
  1 passed in 3.79s
env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_forwarded_minor_source_is_inactive_after_oldify -q -n0
  1 passed in 25.38s
env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py -q -n0
  21 passed in 232.93s
env -u LC_ALL PCC_GC_BACKEND=3 uv run pytest tests/test_gc_*.py -q -n0
  216 passed in 409.07s
env -u LC_ALL uv run pytest tests/test_gc_*.py -q -n0
  216 passed in 383.36s
```

## Report (only when the investigation is closing)
No.1 landed.  It is deliberately conservative: inactive copied-from sources
remain in the object list and keep their forwarding entries until the stale
source reference is actually released, so minor arena memory is not reused too
early.  They are no longer known active heap objects, so a caller cannot replace
their forwarding target and tracing/promotion/sweep/live-byte paths no longer
double count or process them.
