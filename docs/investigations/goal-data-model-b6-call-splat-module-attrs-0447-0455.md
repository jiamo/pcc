# goal data-model B6 call splat / module attrs

This slice advances goal.md No.25.

## Runtime helpers

Call splat:

- `py_call_merge_posargs(base_tuple, star_args)`
- `py_call_merge_kwargs(base_kwargs, star_kwargs)`
- `py_obj_call_splat(callable, base_args, star_args, base_kwargs, star_kwargs)`

The first implementation supports tuple/list for `*args` and dict for
`**kwargs`.

Module attrs:

- `py_module_attr_set(module, attr, value)`
- `py_module_attr_get(module, attr)`
- `py_module_attr_del(module, attr)`
- `py_module_attr_len(module)`

The module attr implementation is a pinned side table keyed by module name.
This avoids CPython module objects while keeping tracing GC roots safe.

## Gate

```bash
bash scripts/run_b6_goal_gate.sh
```

## Still open

Codegen still needs to lower source-level:

- `f(*args)`
- `f(**kwargs)`
- `f(*a, **k)`
- `module.attr = value`

to these runtime helpers in no-libpython binaries.
