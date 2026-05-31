# goal data-model D3/D4/D6 runtime slice

This pack advances three later `goal.md` data-model items at the runtime/ABI
boundary.

## D3 async / await

Adds:

- `py_coroutine_is_done`
- `py_coroutine_get_result`
- `py_task_is_done`

The native harness verifies coroutine await, cached result, and task step/done.

## D4 context manager

Adds:

- `py_context_enter(manager)`
- `py_context_exit(manager, exc_type, exc, tb)`

The helpers support both raw method pointers and `PY_TYPE_FUNC` wrappers.

## D6 format

Adds:

- `py_obj_format(obj, spec)`

The first implementation supports user `__format__`, empty spec via `str(obj)`,
and a minimal int `d`/`x` subset.

## Gate

```bash
bash scripts/run_d3_d4_d6_goal_gate.sh
```

## Still open

These are runtime slices.  Source-level codegen still needs to lower:

- async def / await into coroutine/task helpers
- with / async with into context enter/exit helpers
- format() / f-string format-spec into `py_obj_format`
