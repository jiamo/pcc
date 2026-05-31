# goal data-model B3 class-level variables 0405-0412

This slice implements the runtime side of goal.md No.22:

```text
B3 class-level variable read/write
Cls.count read/write, inheritance shadowing, test_classvar passing
```

## Design

Class variables must not be stored in the method table.  Methods are borrowed
function objects with lookup semantics; class variables are owned Python object
values with normal assignment/deletion semantics.

This patch adds an owned `attrs` dict to `PyClassObject`:

```c
PyObject *attrs;
```

`py_obj_getattr/setattr/delattr` now dispatch class objects to:

- `py_class_getattr`
- `py_class_setattr`
- `py_class_delattr`

Method lookup remains in `py_class_lookup`, and instance method fallback
continues to use the method table path.

## Gate

```bash
bash scripts/run_classvar_goal_gate.sh
```

## Remaining work

This patch covers runtime class attribute storage.  The next slice should add
compiled no-libpython source-level tests for:

- `class C: count = 1`
- `C.count = C.count + 1`
- subclass shadowing and inherited class attr lookup.
