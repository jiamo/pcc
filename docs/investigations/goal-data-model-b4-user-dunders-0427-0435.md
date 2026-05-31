# goal data-model B4 user dunders

This slice advances goal.md No.23:

```text
B4 user dunders iter/hash/str
__iter__/__next__/__hash__/__str__ runtime dispatch passing
```

## Runtime behavior

The C runtime now dispatches user-instance dunders through the class method
table:

- `py_obj_str(obj)` -> `obj.__str__()`
- `py_obj_hash(obj)` -> `obj.__hash__()`
- `py_obj_iter(obj)` -> `obj.__iter__()`
- `py_obj_next(obj)` -> `obj.__next__()`

The dispatch helper accepts both historical raw function-pointer methods and
`PY_TYPE_FUNC` method objects.

## pcc-Python mirror

`py_dunder.py` exports the same `py_user_*_dispatch` helpers for the
pcc-Python runtime-high archive.  `py_iter.py` calls the user dispatch before
raising TypeError for non-builtin iterables/iterators.

## Gate

```bash
bash scripts/run_user_dunder_goal_gate.sh
```

## Still open

This slice covers runtime dispatch.  Source-level lowering still needs a
compiled no-libpython test for:

- `str(C())`
- `hash(C())`
- `for x in C(): ...`
- `next(C())`
