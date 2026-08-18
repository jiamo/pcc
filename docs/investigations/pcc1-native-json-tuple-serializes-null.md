# Investigation: pcc1 native json.dumps serializes tuples as null

## Status

active

## Problem Description

The strict pcc1 lowering for `json.dumps` calls the native runtime ABI rather
than `pcc.py_stdlib.json._encode`.  Both native runtime owners handle lists but
omit tuples, and their default branch silently appends `null`.  Therefore a
supported Python tuple changes meaning instead of encoding as a JSON array or
failing closed.  The packed indexed-module sidecar exposed this when its
function/global tables were encoded as `null`, while the scalar payload still
described the omitted functions.

## Repro

Compile this source through a current self/no-libpython pcc1 and run it:

```python
import json
print(json.dumps({"tuple": (1, 2)}))
```

CPython prints `{"tuple": [1, 2]}`.  The v45 pcc1 at
`build/indexed-sidecar-stage1-v45/pcc1` prints `{"tuple": null}`.

## Test [CONFIRMED]

The minimized source is `/tmp/pcc_json_tuple_repro.py`.  Its guarded compile
receipt is `build/json-tuple-v45-compile.result.json`; the produced executable
ran successfully and printed the incorrect `{"tuple": null}` result.  The
original sidecar likewise contains `"functions": null` and `"globals": null`
despite its frontend receipt reporting three functions.

## Proposals

- No.1 Add tuple traversal and fail-closed unsupported-value errors to both native JSON runtime owners `[pending]`

## No.1 Add tuple traversal and fail-closed unsupported-value errors to both native JSON runtime owners

### Code Change

Teach `pcc/py_runtime/py/py_json_runtime.py` and its C differential oracle
`pcc/py_runtime/src/py_json.c` to traverse `PY_TYPE_TUPLE` with the same JSON
array spelling as a list.  Unsupported object tags must raise `TypeError`
instead of silently writing `null`, and frontend native-json lowering must
check the runtime exception after `loads`/`dumps`.  Add a pcc1 compile/run
differential for nested tuples and one unsupported object.

### pending

The active performance slice will use list-only wire records because lists are
the intended mutable construction projection.  That representation correction
does not close this generic Python-semantics defect; this proposal remains a
separate task until both runtime owners and the pcc1 execution gate pass.
