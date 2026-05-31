# Investigation: Task objects must expose GC-updatable state slots

## Status
resolved

## Problem Description
The user-mode scheduling goal needs a real task object shape, not just bare
scheduler queues. A task owns the coroutine it drives, its result, and its
current waiter/await-chain edge; backend #3/#4 must be able to update those
slots when young objects are promoted or moved.

## Repro
Run the focused Backend #3 task slot rewrite gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_task_state_slots_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_task_state_slots_to_oldified_copy' -q -n0
```

Expected implementation result: an old task that receives young `result` and
`waiter` objects during a minor collection has both raw slots rewritten to the
forwarded old copies.

## Test [CONFIRMED]
Initial run exposed a setup-sensitive failure in the new gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_task_state_slots_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_task_state_slots_to_oldified_copy' -q -n0
```

Observed before correcting the probe heap timing:

```text
2 failed in 28.08s
stdout: ['0', '1']
```

The first child was allocated just before an allocation-triggered minor refill
and became old before `py_task_set_result()` installed it in the task. The
final regression explicitly asserts both children are young before the store
and the old task is marked remembered after the store.

## Proposals
- No.1 Add minimal task object GC contract     [pending]

## No.1 Add minimal task object GC contract
### Code Change
Add `PY_TYPE_TASK`, `PyTaskObject`, and minimal `py_task_*` runtime APIs. The
task owns `coro`, `result`, and `waiter` slots via `pcc_gc_store_ptr()`, and
the tracing/generational collectors visit, clear, and promote those slots in
both C runtime and pcc-Python runtime implementations.
### CONFIRMED
Landed. The C runtime and pcc-Python runtime mirror now expose the same task
layout and GC behavior. Focused gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_task_state_slots_to_oldified_copy' 'tests/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_task_state_slots_to_oldified_copy' -q -n0
```

Observed result:

```text
2 passed in 28.03s
```

Broader affected gates:

```text
tests/test_gc_backend_generational.py: 31 passed in 332.93s
tests/test_gc_coroutine_roots.py: 2 passed in 26.57s
```

## Report (only when the investigation is closing)
No.1 landed. `PY_TYPE_TASK` owns `coro`, `result`, and `waiter` through the
same `pcc_gc_store_ptr()` contract used by generator/coroutine state. Backend
#3 promotion now eagerly rewrites those slots in both runtimes, and tracing
collectors can visit/clear/finalize tasks.

Follow-up remains in the parent goal: this is only the minimal task object
GC contract. It is not yet a full Python stackless scheduler, runnable/timer/IO
queue model, task completion collection policy, or Backend #4 task relocation
matrix.
