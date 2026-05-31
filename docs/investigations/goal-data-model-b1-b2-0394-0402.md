# goal data-model B1/B2 slice 0394-0402

This slice continues after the goal-order gate pack and advances the next
Python data-model items in `goal.md`.

## B1 — bytes literal / native bytes

The parser and native parser already carry `BytesLit`; the C and pcc-Python
runtime already expose `py_bytes_new`, `py_bytes_len`, `py_bytes_getitem`, and
`py_bytes_slice`.

This slice adds focused data-model tests that lock those behaviors:

- CPython-ast parser keeps `b"..."` as `BytesLit`.
- type inference preserves `BytesType`.
- native runtime verifies length, indexing, non-ASCII byte values, and slicing.

## B2 — type(x) / type(x).__name__

Adds the missing runtime ABI surface:

- `py_type_builtin(PyObject*)` in C runtime.
- matching pcc-Python runtime export.
- public runtime header declaration.
- `runtime_abi.py` signature.

The runtime returns a class object for built-in values and returns the actual
class for user instances / exceptions when available.  `.__name__` then flows
through the existing `py_obj_getattr(cls, "__name__")` / `py_class_lookup`
path.

This slice also gives type inference a concrete return type for the `type`
builtin so `type(x).__name__` stops being unconditionally dynamic.

## Gate

```bash
bash scripts/run_data_model_goal_gate.sh
```

## Still open

- Full codegen-direct lowering for arbitrary `type(x)` user source should be
  verified through a compiled no-libpython binary after this runtime ABI lands.
- B3 class-level variable read/write is not included in this slice because it
  needs class-owned attribute storage distinct from method-table entries.
