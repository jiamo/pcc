# Investigation: self-backend nested valueclass payload aggregate returns

## Status
resolved locally 2026-06-04

## Problem Description
The V1/V2 nested valueclass payload ABI slice exposed self-backend parser gaps
after the frontend began lowering a non-recursive nested valueclass payload as a
literal nested LLVM struct:

```llvm
{ { i64, i64 }, { i64, i64 } }
```

The first focused valueclass regression proved that the old frontend path used
`ptr` signatures and `py_instance_new` for `Segment(start: Point, end: Point)`.
After enabling nested payload type calculation and constructor/field lowering,
IR shape was correct, but strict self-backend native emission failed on several
parser boundaries for literal nested aggregate types.

## Repro

Focused parser regression:

```bash
env -u LC_ALL uv run pytest \
  tests/c/test_self_backend.py::test_self_backend_parse_supports_literal_nested_struct_return_type \
  -q -n0
```

Focused valueclass regression:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_nested_payload_uses_payload_abi_in_direct_calls \
  -q -n0
```

Before the parser fixes, strict self-backend emission failed successively on:

- function headers returning `{ { i64, i64 }, { i64, i64 } }`;
- `ret { { i64, i64 }, { i64, i64 } } %value`;
- `extractvalue { { i64, i64 }, { i64, i64 } } %payload, 0`;
- `call { { i64, i64 }, { i64, i64 } } (...) @make_segment(...)`.

## Test [CONFIRMED]

The focused valueclass regression first failed on IR shape before the frontend
payload patch, then failed on each self-backend parser boundary above after the
IR shape became correct.

## Proposals

- No.1 Enable non-recursive nested valueclass direct payload ABI [CONFIRMED]
- No.2 Parse nested literal aggregate returns/extracts/calls structurally [CONFIRMED]

## No.1 Enable non-recursive nested valueclass direct payload ABI

### Code Change

Allow a valueclass field to contribute its own payload struct to the parent
payload type. Recursively build nested constructor payload fields, recursively
box/unbox nested payload fields at valuebox boundaries, and allow payload-valued
attribute expressions such as `s.start.x`.

### CONFIRMED

The focused IR-shape assertions in
`test_valueclass_nested_payload_uses_payload_abi_in_direct_calls` now pass:
`Segment(start: Point, end: Point)` direct calls/returns use
`{ { i64, i64 }, { i64, i64 } }`, and the `main` hot path avoids both
`py_instance_new` and `py_valuebox_new`.

## No.2 Parse nested literal aggregate returns/extracts/calls structurally

### Code Change

Replace narrow return-type parsing with top-level type-token splitting in
self-backend function headers and `ret` terminators. Replace the narrow
`extractvalue` regex with a structure-aware parser. Add a call parser path that
keeps the existing narrow regex fast path but falls back to structure-aware
return type, optional explicit signature, callee, and argument parsing.

### CONFIRMED

Focused gates after the fix:

```text
tests/c/test_self_backend.py::test_self_backend_parse_supports_literal_nested_struct_return_type
  -> 1 passed
tests/python/test_py_value_class_unboxed.py::test_valueclass_nested_payload_uses_payload_abi_in_direct_calls
  -> 1 passed
```

Broader gates also passed: parser aggregate batch -> 4 passed; full
`tests/python/test_py_value_class_unboxed.py` -> 12 passed; full
`tests/python/data_model/test_value_class_runtime.py` -> 15 passed; V0/V1/status
batch -> 29 passed; fallback/no-libpython baselines -> 18 passed; full
`tests/python/gc_production_contract` -> 130 passed; mandatory full self
bootstrap -> 5 passed in 469.46s.

## Report

The landed fix is a focused V1/V2 boundary: non-recursive nested valueclass
payloads now work in direct typed calls/returns under the strict self backend.
The self-backend parser changes are generic for literal nested aggregate
function returns, `ret`, `extractvalue`, and aggregate-return calls; they are
not special-cased to valueclasses.

Remaining boundaries are still open: recursive valueclasses, full V2 marshal
coverage, flattened object storage/dispatch, typed arrays, and broader
aggregate ABI claims beyond the tested parser/codegen shapes.
