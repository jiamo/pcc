# Investigation: self-backend aggregate call signatures for valueclass payloads

## Status
resolved locally 2026-06-04

## Problem Description
The V2 valueclass boundary work exposed a strict no-libpython self-backend
failure when a boxed valueclass was recovered through a container subscript and
passed into a typed valueclass function.

The minimized Python repro:

```python
from typing import Any

import pcc

@pcc.valueclass
class Point:
    x: int
    y: int

def to_dyn(v: Any) -> Any:
    return v

def total(p: Point) -> int:
    return p.x + p.y

boxed = to_dyn(Point(7, 8))
tup = (boxed,)
lst = [boxed]
print(total(tup[0]))
print(total(lst[0]))
```

The strict invocation failed during self-backend native emission:

```text
BackendUnavailable: self backend does not understand LLVM type '{ i64'
```

The first failing boundary is the self-backend LLVM IR parser, not runtime
valuebox payload tracing. A separate direct nested valueclass aggregate smoke
already compiled and printed the expected result under the self backend.

## Repro

Focused parser regression:

```bash
env -u LC_ALL uv run pytest \
  tests/c/test_self_backend.py::test_self_backend_parse_supports_literal_struct_call_signature_args \
  -q -n0
```

Focused end-to-end regression:

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_boxed_container_subscript_self_backend_to_typed_payload \
  -q -n0
```

Before the fix both failed with the same root symptom:

```text
self backend does not understand LLVM type '{ i64'
```

## Hypotheses

1. `self_backend_parse._parse_call_signature()` splits signature arguments on
   every comma. CONFIRMED. The failing aggregate argument text
   `{ i64, i64 }` was split into `{ i64` and `i64 }`.
2. Valuebox object-to-payload unboxing produced malformed LLVM IR. REJECTED for
   this failure. The IR is legal LLVM-style text; the parser could not decode
   the explicit aggregate signature.
3. The self-backend aggregate ABI cannot pass small valueclass payloads.
   REJECTED for this boundary. Direct nested valueclass aggregate payloads
   already compile and run; the failing symptom occurs before target ABI
   lowering.

## Root Cause

`pcc/backend/self_backend_parse.py::_parse_call_signature()` used
`inner.split(",")` for explicit call signatures. Other parser paths already
use `split_top_level()` to keep nested LLVM aggregates, arrays, vectors,
parenthesized constants, and quoted values intact.

When Python valueclass lowering emitted a typed call through a recovered boxed
`Point`, the signature contained an aggregate argument:

```llvm
call i64 ({ i64, i64 }) @...
```

The parser split the aggregate on its field comma and then passed `{ i64` into
`_parse_type()`, causing the observed `BackendUnavailable`.

## Fix

Use `split_top_level(inner)` in `_parse_call_signature()`. This matches the
rest of the parser's LLVM text policy and keeps aggregate signatures as one
argument type.

Added regressions:

- `tests/c/test_self_backend.py::test_self_backend_parse_supports_literal_struct_call_signature_args`
- `tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_boxed_container_subscript_self_backend_to_typed_payload`

## Verification

Focused and claim gates passed after the fix:

```text
tests/c/test_self_backend.py::test_self_backend_parse_supports_literal_struct_call_signature_args
  -> 1 passed
tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_boxed_container_subscript_self_backend_to_typed_payload
  -> 1 passed
self-backend parser call batch
  -> 6 passed
tests/python/data_model/test_value_class_runtime.py
  -> 13 passed
V0/V1/status batch
  -> 28 passed
tests/python/gc_production_contract
  -> 130 passed
py_compile for touched parser/tests
  -> passed
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  -> 5 passed in 458.24s
```

This is a focused V2 boundary result. It does not prove full V2 marshal
coverage, flattened payload dispatch, general pointer-aggregate self-backend
ABI, typed arrays, monomorphization, or full value-model completion.
