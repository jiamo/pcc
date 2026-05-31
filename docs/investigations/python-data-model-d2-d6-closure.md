# Python data-model D2-D6 closure

This bundle closes D2-D6 with compiled no-libpython acceptance tests on top of
the runtime helpers already landed in earlier slices.

## D2 generator state machine

Compiled gate verifies `yield`, `send`, and `return value` surfaced through
`StopIteration.value`.

## D3 async / await minimum

Compiled gate verifies async function creation, await of another coroutine, and
result delivery through the native coroutine shell.

## D4 context-manager full semantics

Compiled gate verifies `with`, `__enter__`, `__exit__`, and exception
suppression.

## D5 iteration / number / comparison polish

Compiled gate verifies user protocol dunders for len/bool/contains/getitem/
setitem/delitem and comparison.

## D6 __format__ / format-spec

Compiled gate verifies builtin integer format, user `__format__`, and f-string
format-spec lowering.

## Gate

```bash
bash scripts/run_d2_d6_closure_gate.sh
```

The gate compiles source programs with `libpython_mode="off"` and keeps runtime
wiring regression checks for the helper ABI.
