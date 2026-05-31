# Investigation: Backend #4 Task relocation must retain state slots

## Status
resolved

## Problem Description
Backend #4 now relocates scalar objects, lists, and tuples. A Task is the first
user-mode-scheduling owner object we need to move. It owns `coro`, `result`, and
`waiter` slots. A plain relocation copy preserves those pointers but does not
give the moved Task ownership of the referenced objects. Releasing the old
forwarded Task would then drop objects still referenced by the moved Task.

## Repro
Run the focused C runtime and pcc-Python runtime mirror gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_copy_retains_state_slots' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_copy_retains_state_slots' -q -n0
```

Expected result after the fix: Backend #4 can select a Task for relocation,
copy it, `py_incref()` `coro`, `result`, and `waiter` for the moved Task,
preserve stable object identity, and keep all three slots readable after the
root slot follows the forwarding entry and releases the old Task.

## Test [CONFIRMED]
The focused tests fail before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_copy_retains_state_slots' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_copy_retains_state_slots' -q -n0
```

Observed result:

```text
2 failed in 28.77s
stdout: 0
```

Both runtime archives build and run the probe, but Backend #4 does not yet
relocate Task objects while preserving moved-Task ownership of `coro`,
`result`, and `waiter`.

## Proposals
- No.1 Add Task-specific relocation slot ownership     [CONFIRMED]

## No.1 Add Task-specific relocation slot ownership
### Code Change
Keep Backend #3 oldification support unchanged. Add Backend #4-specific
relocation support for `PY_TYPE_TASK`. After the copied Task header is in the
moved object, clear the moved slots for failure-safe cleanup, `py_incref()` each
source slot, copy those slots into the moved Task, and preserve `done`.
### CONFIRMED
The focused C runtime and pcc-Python runtime mirror gate now passes:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 900s uv run pytest 'tests/test_gc_backend_relocating.py::test_colored_relocating_task_copy_retains_state_slots' 'tests/test_gc_backend_relocating.py::test_pcc_python_colored_relocating_task_copy_retains_state_slots' -q -n0
```

Observed result:

```text
2 passed in 28.89s
```

The full relocation and abstraction gate passes:

```text
tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py:
30 passed in 165.57s
```

The Backend #3 Task slot gates still pass, confirming Backend #3 oldification
support was not widened:

```text
2 passed in 30.80s
```

## Report (only when the investigation is closing)
No.1 landed. Backend #4 now relocates `PY_TYPE_TASK` as a state-owner object:
the moved Task owns its `coro`, `result`, and `waiter` slots independently of
the old forwarded source, and root-slot read-barrier healing can release the
old Task while keeping all three moved-state slots readable.
