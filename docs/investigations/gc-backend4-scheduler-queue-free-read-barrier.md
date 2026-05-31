# Investigation: Backend #4 scheduler queue free must heal forwarded entries

## Status
resolved

## Problem Description
Backend #4 queue pop already reads queued values through `pcc_gc_load_ptr()`
before transferring them into the consumer slot. The queue free path releases
remaining entries through `pcc_gc_store_root(..., NULL)` without first loading
the entry value. If a queued object has a forwarding entry and the queue is
destroyed before pop, the free path can release the stale source pointer
without exercising the relocation read barrier.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_and_scheduler_queue_follow_forwarding' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_and_scheduler_queue_follow_forwarding' -q -n0
```

Expected result after the fix: the existing Task result forwarding and queue
pop forwarding checks still pass, and a new queue-free check observes at least
one `PCC_GC_COUNTER_RELOCATION_BARRIER_FORWARDS` increment while freeing an
un-popped forwarded queue entry.

## Test [CONFIRMED]
The focused tests fail before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_and_scheduler_queue_follow_forwarding' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_and_scheduler_queue_follow_forwarding' -q -n0
```

Observed result:

```text
2 failed in 28.93s
stdout: 1, 1, 0
```

Both runtime archives still pass the existing Task result and queue pop
forwarding checks, but the new queue-free check does not observe a relocation
barrier forward while freeing an un-popped forwarded queue entry.

## Proposals
- No.1 Heal scheduler queue entries before free     [CONFIRMED]

## No.1 Heal scheduler queue entries before free
### Code Change
Make `pcc_gc_scheduler_queue_entry_free()` mirror the pop path: load the entry
value with `pcc_gc_load_ptr()` while it is still registered as a scheduler root,
then unregister and clear the slot. Apply the same change to the pcc-Python
runtime mirror.
### CONFIRMED
The focused C runtime and pcc-Python runtime mirror gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_and_scheduler_queue_follow_forwarding' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_and_scheduler_queue_follow_forwarding' -q -n0
```

Observed result:

```text
2 passed in 28.26s
```

The full relocation and abstraction gate passes:

```text
tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py:
36 passed in 233.59s
```

The scheduler queue generational rewrite and TSan gates still pass:

```text
tests/test_gc_backend_generational.py::{test_generational_backend_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy,test_generational_backend_pcc_python_runtime_minor_refill_rewrites_scheduler_queue_entry_to_oldified_copy}:
2 passed in 29.38s

tests/test_gc_concurrent_collection.py::test_generational_scheduler_queue_threadsanitizer_or_skip:
1 passed in 6.06s
```

## Report (only when the investigation is closing)
No.1 landed. Scheduler queue entry free now follows the same root/update model
as queue pop: while the entry is still registered as a scheduler root, it loads
the value through the relocation read barrier, then unregisters and clears the
slot. This closes the un-popped forwarded entry case for both the C runtime and
the pcc-Python runtime mirror.
