# Investigation: cross-module static table imports triggered strict fallback

## Status
resolved

## Problem Description
After splitting static builtin type tables out of `layer1.py`, the strict
self-bootstrap gate failed at stage 1:

```text
Python pipeline requires libpython fallback for multi-file compile
(modules: pcc.py_frontend.codegen.layer1)
```

The concrete pattern was a module importing a top-level static table from a
native sibling module and then using membership or subscript operations on it.

## Repro
The minimized regression now lives in:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_cross_module_static_table_import_stays_native \
  -q -n0
```

It compiles:

```python
# tables.py
VALUES = {"a": 3, "b": 4}

def direct() -> int:
    return VALUES["a"]
```

```python
# entry.py
from .tables import VALUES, direct

def lookup(name: str) -> int:
    if name not in VALUES:
        return 0
    return VALUES[name]

print(direct())
print(lookup("b"))
```

Before the fix, strict multi-file emit-LLVM succeeded but produced
`py_cpy_import`, `py_cpy_getattr`, and `py_cpy_getitem` calls for `VALUES`.

## Root Cause
The multi-file native export table modeled:

- top-level functions
- top-level classes
- scalar literal constants

It did not model top-level literal container assignments like
`VALUES = {"a": 3}`. Therefore `from .tables import VALUES` was not a native
export. Import lowering fell back to CPython `from ... import ...`, and
subsequent membership/subscript operations on `VALUES` were typed as dynamic
CPython object operations.

The right model is not to inline the whole table into every importer. The
defining module already owns and initializes the object. Importing modules only
need a native reference to the defining module's global storage slot.

## Fix
The export pass now records shallow static types for top-level literal
containers as `kind: "module_global"`.

Codegen now:

- gives module globals stable per-module symbols,
  `.modvar.<module>.<name>`;
- emits defining module globals as normal global definitions with initializers;
- declares imported module globals as extern globals in importing modules;
- binds the imported local name into `_module_globals`, so normal name
  lowering loads the native slot instead of routing through CPython.

Type inference also consumes `module_global.value_ty`, so operations like
`name not in VALUES` and `VALUES[name]` keep their native `dict[str, int]`
shape.

## Validation
Focused regression:

```text
1 passed in 1.14s
```

Full multi-file compile suite:

```text
11 passed in 6.08s
```

Self-bootstrap must remain green after this change because it touches the
Python frontend and native multi-file import model.
