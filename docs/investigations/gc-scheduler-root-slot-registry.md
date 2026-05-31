# Investigation: GC scheduler root slot registry

## Status
resolved

## Problem Description
The GC production goal requires user-mode scheduler queues to be traced and
updated as roots. The runtime has native frame root registration, but no
dedicated root registry for runnable/timer/await queue entries that live outside
native call frames.

This slice adds a small scheduler-root substrate: queues can register individual
`PyObject **` slots, and non-refcount backends can trace or update those slots.

## Repro
Focused gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy' \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy' \
  -q -n0
```

Expected result: both C runtime and pcc-Python runtime variants pass. The probe
registers one scheduler root slot, stores a young object into it, forces a
Backend #3 minor refill, and checks that the root slot points at the non-minor
oldified copy.

## Test [N/A]
This is a missing substrate for the scheduler-root roadmap item, not a
previously observed user-program crash. The focused gate above is the regression
guard.

Observed result:

```text
2 passed in 28.22s
```

## Proposals
- No.1 Add scheduler root slot registry     [CONFIRMED]

## No.1 Add scheduler root slot registry
### Code Change
Add `pcc_gc_scheduler_root_register(PyObject **slot)` and
`pcc_gc_scheduler_root_unregister(PyObject **slot)` to the runtime ABI. Backend
#1/#2 root seeding grays registered scheduler slots; Backend #3 promotion
rewrites registered scheduler slots with the same slot-aware oldification used
for native frame roots.

### CONFIRMED
The focused C runtime + pcc-Python runtime gate passed:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest \
  'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy' \
  'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_root_slot_to_oldified_copy' \
  -q -n0
```

Observed result: `2 passed in 28.22s`.

## Report (only when the investigation is closing)
The scheduler-root substrate now exists in both runtime implementations. A
future scheduler can register runnable/timer/await queue entry slots without
pretending they are native frame roots. Backend #1/#2 root seeding treats those
slots as roots, and Backend #3 promotion rewrites them through the same
slot-aware oldification path used for native frame roots.

This closes the root-slot registry substrate only. It does not implement the
actual user-mode scheduler queues or async task state machine.
