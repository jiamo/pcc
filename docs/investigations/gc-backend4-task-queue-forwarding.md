# Investigation: Backend #4 must heal task and scheduler queue references

## Status
resolved

## Problem Description
The user-mode scheduling gate requires Backend #4 relocation/read barriers to
follow forwarded objects reachable from suspended task state and scheduler
queues. The current task object uses `pcc_gc_load_ptr()` in its getters, but
scheduler queue pop transfers the raw entry value into the output slot.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_and_scheduler_queue_follow_forwarding' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_and_scheduler_queue_follow_forwarding' -q -n0
```

Expected result: Backend #4 relocates the newest simple child object, task
result access heals the task slot to the forwarded object, and scheduler queue
pop transfers the forwarded object rather than the stale source address.

## Test [CONFIRMED]
The focused tests fail before the queue-pop fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_and_scheduler_queue_follow_forwarding' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_and_scheduler_queue_follow_forwarding' -q -n0
```

Observed result:

```text
2 failed in 27.60s
stdout: ['1', '0']
```

The first line confirms Task result access already heals through
`py_task_get_result()`. The second line confirms scheduler queue pop still
copies the stale forwarded source address into the consumer slot.

## Proposals
- No.1 Heal scheduler queue entries through the relocation read barrier     [CONFIRMED]

## No.1 Heal scheduler queue entries through the relocation read barrier
### Code Change
Before `pcc_gc_scheduler_queue_pop_into()` transfers an entry value into the
consumer slot, load the entry through `pcc_gc_load_ptr()` so Backend #4 can
follow forwarding and update the queue entry's registered root slot. Mirror the
same behavior in the pcc-Python runtime-high implementation. Keep task behavior
unchanged if its getter gate already passes. In the C runtime, update
relocation-candidate header bits through the existing atomic flag helpers so
queue pop can safely run read barriers under concurrent producer/consumer GC
traffic.
### CONFIRMED
Landed. `pcc_gc_scheduler_queue_pop_into()` now first reads the entry slot
through `pcc_gc_load_ptr()`, so Backend #4 can follow forwarding and update the
registered queue root before the value is transferred to the consumer slot. The
pcc-Python runtime mirror uses the same load-before-transfer rule.

Focused gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_and_scheduler_queue_follow_forwarding' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_and_scheduler_queue_follow_forwarding' -q -n0
```

Observed result:

```text
2 passed in 27.68s
```

Broader affected gates:

```text
tests/test_gc_backend_relocating.py tests/test_py_runtime_abi_attrs.py tests/test_py_low_ir.py: 14 passed in 55.90s
tests/test_gc_abstraction_surface.py tests/test_gc_threading_substrate.py: 27 passed in 7.72s
CC=clang tests/test_gc_concurrent_collection.py::test_generational_scheduler_queue_threadsanitizer_or_skip: 1 passed in 4.45s
```

The TSan gate initially exposed a C-runtime race introduced by calling
`pcc_gc_load_ptr()` from queue pop under concurrency: `pcc_gc_note_relocation_read()`
cleared `PY_FLAG_GC_RELOCATION_CANDIDATE` with a raw header write while other
threads used atomic header reads. The C runtime now updates relocation-candidate
bits with the existing atomic header flag helpers.

## Report (only when the investigation is closing)
No.1 landed. Backend #4 now has a concrete user-mode scheduling read-barrier
gate for both task result state and scheduler queue handoff. This does not make
Backend #4 production: it still relocates only simple child objects, not
reference-bearing task/queue/container objects themselves, and still lacks page
evacuation/fragmentation policy.
