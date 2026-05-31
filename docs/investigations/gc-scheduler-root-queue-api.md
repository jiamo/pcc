# Investigation: scheduler queue entries should own GC root slots

## Status
resolved

## Problem Description
The scheduler root registry lets future runnable/timer/IO/wakeup queues
register individual `PyObject **` slots, but every queue implementation would
still need to allocate entries, register slots, clear roots, and unregister
slots in exactly the right order. That is too easy to get wrong in the
coroutine/task scheduler path and is directly tied to backend #3/#4 reference
updates.

## Repro
Run the focused Backend #3 queue slot rewrite gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy' -q -n0
```

Expected implementation result: a young object pushed into a scheduler queue is
oldified during minor collection, the queue entry slot is rewritten to the
forwarded old copy, and `pop_into()` transfers that forwarded object to the
caller slot.

## Test [CONFIRMED]
The focused C runtime and pcc-Python runtime mirror gates pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy' -q -n0
```

Observed result: `2 passed in 28.88s`.

## Proposals
- No.1 Add scheduler root queue API     [CONFIRMED]

## No.1 Add scheduler root queue API
### Code Change
Add an opaque `PccGcSchedulerQueue` ABI with `new/free/push/pop_into/len`.
Each pushed entry owns one registered scheduler root slot. `pop_into()` stores
the entry value into the caller-provided root slot before unregistering and
clearing the queue entry.
### CONFIRMED
No.1 passes the focused Backend #3 C runtime and pcc-Python runtime mirror
gates. The probe pushes a young string into the queue, forces a minor refill,
confirms the original source has a forwarded old copy, then pops into a caller
slot and verifies the popped value is the forwarded old copy.

## Report (only when the investigation is closing)
No.1 landed. The runtime now has a narrow queue-level substrate for scheduler
roots, so future runnable/timer/IO/wakeup queues do not need to manually
duplicate root slot registration and cleanup. This still is not a full
user-mode scheduler: there are no task state machines, timer wheels, IO wait
sets, or await-chain futures yet.
