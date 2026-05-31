# goal data-model B5 exception chaining / traceback frames

This slice advances goal.md No.24.

## Runtime surface

The exception object already stored:

- `cause`
- `context`
- `traceback`
- `n_frames`

This slice exposes that state through stable runtime ABI helpers:

- `py_exc_get_cause`
- `py_exc_get_context`
- `py_exc_traceback_len`

The C runtime and pcc-Python runtime mirror are both updated.

## Native behavior tested

`tests/data_model/test_exception_chaining_runtime.py` compiles and links a
native C harness.  It verifies:

- explicit `raise from`-style cause via `py_exc_set_cause`
- implicit context set by `py_raise` when another exception is already active
- traceback frame accumulation via `py_exc_append_frame`
- `py_exc_print_unhandled` emits both chained exceptions and frame lines

## Gate

```bash
bash scripts/run_exception_chaining_goal_gate.sh
```

Still open: source-level no-libpython lowering for `raise ... from ...` and
automatic frame appending at every compiled throw/call boundary.
