# goal typing/types native stdlib slice

This pack advances the type-runtime part of the Python compatibility goals.

## typing

The native `typing` replacement now includes:

- `Protocol`
- `Generic`
- `TypeVar`
- `ParamSpec`
- `TypeVarTuple`
- `NewType`
- `Annotated`
- `Required` / `NotRequired`
- `Self` / `Never`
- `get_origin` / `get_args`

All are runtime no-ops or marker objects.  This is deliberate: pcc consumes
annotations during type inference and only needs enough runtime surface to keep
self-host imports no-libpython.

## types

Adds `pcc.py_stdlib.types` with:

- `SimpleNamespace`
- `ModuleType`
- `MappingProxyType`
- common marker aliases such as `FunctionType`, `GeneratorType`, `NoneType`

## Gate

```bash
bash scripts/run_typing_types_goal_gate.sh
```

Still open: a compiled no-libpython import test once the native stdlib import
pipeline has a stable fixture for selecting `pcc/py_stdlib` at compile time.
