# goal data-model D2 generator state machine

This slice advances goal.md No.26 at the runtime boundary.

## Added ABI

- `py_gen_is_done(gen) -> int64`
- `py_gen_finish(gen, value) -> PyObject*`

`py_gen_finish` marks the generator done and raises `StopIteration(value)`.
This gives codegen a stable runtime target for `return value` inside generator
state machines.

## Tested behavior

The native harness in `tests/data_model/test_generator_state_runtime.py`
constructs a generator with a C resume thunk and verifies:

- `next(gen)` on a fresh generator sends `None`
- `send(value)` is visible through `py_gen_take_send`
- generator state transitions through `py_gen_state` / `py_gen_set_state`
- `py_gen_finish(gen, value)` raises `StopIteration(value)`
- later `next(gen)` reports StopIteration and `py_gen_is_done(gen) == 1`

## Still open

This is not the whole D2 source-level generator lowering.  The codegen pass
still needs to lower Python `yield` / `return` into a heap frame + resume thunk
that calls these runtime helpers.

Gate:

```bash
bash scripts/run_generator_goal_gate.sh
```
