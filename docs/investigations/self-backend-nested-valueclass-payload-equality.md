# Investigation: self-backend nested valueclass payload equality

## Status
resolved locally 2026-06-04

## Problem Description
The next V1/V2 value-model boundary after non-recursive nested payload calls and
returns is direct equality for the same nested payload shape. A strict
self-backend probe for `Segment(start: Point, end: Point)` `==` / `!=` fails
during native emission because valueclass equality lowering compares nested
aggregate fields with scalar `icmp`.

This is a focused continuation of the value-model state recorded in
`python-valhalla-value-model-actual-state.md`, which explicitly left
non-scalar or nested valueclass payload equality unimplemented, and follows the
aggregate parser fixes in
`self-backend-nested-valueclass-payload-aggregate-return.md`.

## Repro

Focused gate to add:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_nested_payload_equality_uses_recursive_fieldwise_compare \
  -q -n0
```

Observed probe failure before the fix:

```text
pcc.py_frontend.pipeline.PyPipelineError: self backend native emission failed
BackendUnavailable: self backend cannot use aggregate type in a register directly: <anon-struct>
```

The generated IR contains `value.eq.icmp` for a field whose LLVM type is a
nested literal struct such as `{ i64, i64 }`.

## Test [CONFIRMED]

The focused regression was added and failed for the expected IR-shape reason:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_nested_payload_equality_uses_recursive_fieldwise_compare
  -> failed on `assert not re.search(r"\bicmp eq \{", eq_ir)`
```

This confirms equality lowering emits aggregate `icmp` before the strict
self-backend compile/run step. The original failure was also observed with a
strict `--backend self --python-libpython=off --ir-scaffold=on` probe using:

```python
@pcc.valueclass
class Point:
    x: int
    y: int

@pcc.valueclass
class Segment:
    start: Point
    end: Point

def same(left: Segment, right: Segment) -> bool:
    return left == right

def different(left: Segment, right: Segment) -> bool:
    return left != right
```

Expected runtime output is:

```text
True
False
True
```

## Proposals

- No.1 Recurse valueclass payload equality over nested valueclass fields [CONFIRMED]

## No.1 Recurse valueclass payload equality over nested valueclass fields

### Code Change

Add a helper in valueclass equality lowering that compares a field according to
its declared type. Scalar fields keep the existing integer/bool/float compare
behavior, pointer-shaped fields keep `py_obj_eq`, and nested valueclass fields
recursively extract and compare their payload fields instead of comparing the
aggregate as a register value.

`host_contract.py` includes the new helper methods and
`scripts/regen_l1_codegen_static_methods.py` refreshed
`_l1_codegen_static_methods.py` so the self-host path has the same helper
surface.

### CONFIRMED

Focused gate after the fix:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_nested_payload_equality_uses_recursive_fieldwise_compare
  -> 1 passed in 25.53s
```

The gate proves the direct equality functions no longer emit aggregate `icmp`
for nested valueclass fields and strict self-backend compile/run prints the
expected `True`, `False`, `True` output.

## Report

No.1 landed. The fix is in valueclass equality lowering, not in the
self-backend: nested valueclass fields are recursively extracted and compared
through the same scalar/pointer leaf rules already used for scalar-field
valueclasses. This keeps the self-backend from seeing illegal aggregate `icmp`
while preserving existing pointer-field equality behavior through `py_obj_eq`.

Verification:

```text
tests/python/test_py_value_class_unboxed.py::test_valueclass_nested_payload_equality_uses_recursive_fieldwise_compare
  -> 1 passed
py_compile for touched Python files
  -> passed
tests/python/test_py_value_class_unboxed.py
  -> 13 passed
tests/python/data_model/test_value_class_runtime.py
  -> 15 passed
V0/V1/status batch
  -> 30 passed
tests/python/gc_production_contract
  -> 130 passed
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
  -> 18 passed
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  -> 5 passed in 376.36s on the final code state
```

Remaining value-model boundaries are unchanged: recursive valueclasses, full
V2 marshal coverage, flattened object storage/dispatch, typed arrays, and full
V-track completion remain open.
