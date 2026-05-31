# goal data-model D5 protocol polish

This slice adds runtime dispatch for user-defined protocol dunders:

- `__len__`
- `__bool__`
- `__contains__`
- `__getitem__`
- `__setitem__`
- `__delitem__`

The implementation lives in `py_protocol.c` and is called from the generic
object dispatchers.

## Runtime-high mirror

The pcc-Python replacements for `py_obj_ops_dispatch` and `py_obj_ops_compare`
call the same C helper functions through `extern`, so behavior stays aligned
without duplicating raw function-pointer dispatch logic in pcc-Python.

## Gate

```bash
bash scripts/run_protocol_goal_gate.sh
```

## Still open

Source-level no-libpython compiled tests still need to prove:

- `len(C())`
- `bool(C())`
- `x in C()`
- `C()[k]`
- `C()[k] = v`
- `del C()[k]`

lower through these helpers end-to-end.
