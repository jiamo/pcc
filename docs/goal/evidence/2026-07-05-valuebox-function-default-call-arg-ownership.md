# ValueBox Function-Default And Call-Argument Ownership

Task: `AUD-P0-GC-SLOT-VISITOR`

Date: 2026-07-05

## Claim

This slice closes three boxed valueclass ownership gaps in the current GC
production contract:

- direct `Any` user-function arguments created from valueclass constructors now
  release the temporary `ValueBox` after the call consumes it;
- function default objects stored in native function signature tuples release
  the compiler-created temporary refs after tuple insertion;
- chained dynamic ValueBox attribute reads such as `value.item.name` release the
  owned intermediate field object returned by `py_valuebox_get_field(...)`.

The slice does not claim full `AUD-P0-GC-SLOT-VISITOR` completion, pcc1 fixed
point, or exhaustive value payload coverage.

## Changed Files

- `pcc/py_frontend/codegen/unary_call_lowering.py`
- `pcc/py_frontend/codegen/call_expression_lowering.py`
- `pcc/py_frontend/codegen/user_function_lowering.py`
- `pcc/py_frontend/codegen/attr_load_lowering.py`
- `pcc/py_frontend/codegen/ownership_lowering.py`
- `tests/python/gc_production_contract/test_valuebox_roots.py`

## Red Before Green

- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valuebox_roots.py`
  first failed 5/5 backends. All backends reached
  `default_finalizer_value.item.name` but printed event count `7` instead of
  expected `8`, missing `del:func-default-old`.

## Green Gates

- `gtimeout 60s env -u LC_ALL uv run python -m py_compile ...` for touched
  Python codegen/test files -> passed.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valuebox_roots.py`
  -> 5 passed in 26.96s.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  -> 5 passed in 1.39s.
- `gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  -> 140 passed in 24.81s.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
  -> 18 passed in 143.46s.
- Adjacent multi-file/bootstrap-shim gate first produced a transient red:
  `gtimeout 360s env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_multi_file_compile.py tests/python/test_py_multi_file_bootstrap_shim.py`
  -> 106 passed, 1 failed in 237.07s at
  `MultiFileBootstrapShimTests.test_callable_type_alias_literal_with_cpython_values`
  with executable exit `-11`.
- The transient adjacent failure did not reproduce:
  exact node -> 1 passed in 0.65s; bootstrap-shim file -> 78 passed in 197.34s;
  original combined gate rerun -> 107 passed in 210.66s.

## Open Boundary

`AUD-P0-GC-SLOT-VISITOR` remains `DONE_WEAK`: broader value payload slots,
remaining pcc-Python mirror parity, future object-slot/value-payload families
not represented by focused contract nodes, and pcc1/pcc2/pcc3 fixed-point
proof remain open.
