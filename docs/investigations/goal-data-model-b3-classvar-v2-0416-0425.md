# goal data-model B3 class-level variables v2

This supersedes the earlier classvar runtime attempt.

## Why v2

The first attempt changed `PyClassObject` layout by adding an `attrs` field.
That was wrong for pcc because `py_class.py` mirrors `PyClassObject` using
hard-coded offsets.  Changing the C layout would silently desynchronize the
pcc-Python runtime mirror.

## Design

This v2 keeps `PyClassObject` layout stable and stores class-level variables in
a C side table:

```c
typedef struct PccClassAttrsNode {
    PyClassObject *cls;
    PyObject *attrs;
    struct PccClassAttrsNode *next;
} PccClassAttrsNode;
```

The attrs dict is pinned while it is in the side table:

```c
pcc_gc_pin(attrs);
...
pcc_gc_unpin(dead->attrs);
```

That gives tracing backends a stable root without adding a new class-layout
trace edge.

## Runtime surface

- `py_class_getattr`
- `py_class_setattr`
- `py_class_delattr`
- `py_class_attrs_dispose`

Generic object dispatch routes class-object attribute access through these
functions.  Method lookup remains in `py_class_lookup`.

## Gate

```bash
bash scripts/run_classvar_goal_gate.sh
```
