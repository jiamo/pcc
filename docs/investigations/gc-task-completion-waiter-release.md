# Investigation: Task completion must release waiter roots

## Status
resolved

## Problem Description
The user-mode scheduling GC gate requires an await-chain cycle reachable only
from a suspended task to be retained while the task is pending, then collected
after task completion. `py_task_set_result()` currently marks the task done but
does not clear the `waiter` slot.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_coroutine_roots.py::test_task_completion_releases_waiter_cycle_across_backends' 'tests/test_gc_coroutine_roots.py::test_pcc_python_runtime_task_completion_releases_waiter_cycle_across_backends' -q -n0
```

Expected result: for backend 0..4, a self-cyclic waiter list remains alive
while it is reachable only through a rooted pending task, then becomes dead
after `py_task_set_result(task, py_None)` completes the task.

## Test [CONFIRMED]
The focused tests fail before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_coroutine_roots.py::test_task_completion_releases_waiter_cycle_across_backends' 'tests/test_gc_coroutine_roots.py::test_pcc_python_runtime_task_completion_releases_waiter_cycle_across_backends' -q -n0
```

Observed result:

```text
2 failed in 27.96s
stdout: 0:1:0
```

The backend-0 line means the waiter cycle is correctly retained while the task
is pending, but it remains alive after `py_task_set_result()`.

A diagnostic run after clearing the task waiter showed the waiter slot was
NULL, the waiter list refcount had dropped to the self-cycle only, and
`pcc_gc_collect()` returned `1`, but `py_weakref_call()` still returned the
freed target:

```text
diag backend=0 collected_before=0 waiter_null=1 ref_after_clear=1 collected_after=1 after_dead=0
```

So the remaining failure was backend-0 cycle collection not invalidating
weakrefs before deallocating unreachable non-instance containers.

After adding backend-0 weakref invalidation, backend 0 passed but backend 1
failed with:

```text
stdout: 0:1:1
stdout: 1:0:1
```

That was a test-rooting issue: tracing backends do not scan arbitrary C local
variables, so the probe's weakref object itself must be registered as a root
before collection. The final probe roots both the task slot and the weakref
slot through `pcc_gc_scheduler_root_register()`.

## Proposals
- No.1 Clear task waiter on completion     [DENIED]
- No.2 Clear waiter and invalidate weakrefs during backend-0 cycle collection     [CONFIRMED]

## No.1 Clear task waiter on completion
### Code Change
When `py_task_set_result()` or `py_task_step()` completes a task, clear the
`waiter` slot with `pcc_gc_store_ptr(task, &waiter, NULL)` in both the C runtime
and pcc-Python runtime mirror. Keep `result` retained because completed tasks
must still expose their result.
### DENIED
This was necessary but incomplete. The task no longer retained the waiter, but
backend 0 still reported the weakref target as live after collection. A
diagnostic run showed `pcc_gc_collect()` did collect one object, proving the
remaining issue was weakref invalidation during backend-0 cycle deallocation.

## No.2 Clear waiter and invalidate weakrefs during backend-0 cycle collection
### Code Change
Keep No.1's task completion slot clear. Also call `py_weakref_invalidate(obj)`
for each still-unreachable object before `py_gc_clear_referents(obj)` in the
backend-0 cycle collector, in both `py_obj_gc.c` and the pcc-Python mirror.
Tracing backends already do this through `_finalize_unreachable()`. The C probe
also roots the weakref local explicitly so tracing backends do not reclaim the
observer object itself.
### CONFIRMED
The focused C runtime and pcc-Python runtime mirror gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_coroutine_roots.py::test_task_completion_releases_waiter_cycle_across_backends' 'tests/test_gc_coroutine_roots.py::test_pcc_python_runtime_task_completion_releases_waiter_cycle_across_backends' -q -n0
```

Observed result:

```text
2 passed in 28.36s
```

The full coroutine-root file also passes:

```text
tests/test_gc_coroutine_roots.py: 4 passed in 55.71s
```

## Report (only when the investigation is closing)
No.2 landed. Completed tasks now drop their waiter edge in both
`py_task_step()` and `py_task_set_result()`, while preserving the result slot.
Backend 0 now invalidates weakrefs for unreachable cycle-collected containers
before clearing and deallocating them, matching the tracing backend finalize
path. The regression roots both the Task and weakref observer slots, then checks
all five backends in both C and pcc-Python runtime archives.
