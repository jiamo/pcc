# ValueBox Function-Return Ownership

Task: `AUD-P0-GC-SLOT-VISITOR`

Date: 2026-07-05

## Claim

This slice extends the boxed valueclass GC production contract to function
return boundaries:

- direct valueclass constructors returned as `Any` release constructor-owned
  pointer fields once the caller drops the returned object;
- returned containers carrying direct valueclass constructor elements retain
  their payload fields while live and release them after the caller drops the
  returned container;
- chained dynamic reads through returned containers, such as
  `return_list_cell[0].item.name`, release the owned intermediate attr receiver.

The slice does not claim full `AUD-P0-GC-SLOT-VISITOR` completion or pcc1
fixed-point closure.

## Changed Files

- `pcc/py_frontend/codegen/return_lowering.py`
- `pcc/py_frontend/codegen/ownership_lowering.py`
- `tests/python/gc_production_contract/test_valuebox_roots.py`

## Red Before Green

- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valuebox_roots.py`
  failed 5/5 after adding direct-return and returned-container nodes. All
  backends read back `return-any-holder` / `return-any-old` and
  `return-list-holder` / `return-list-old`, but event count remained `8`
  instead of advancing to `9` / `10`.
- A same-source CPython reduction printed both expected finalizers after
  dropping the returned value and returned list.

## Implementation

- `return_lowering.py` now boxes direct valueclass-constructor returns to an
  object ABI with `consume_fields=True`, transferring constructor-owned field
  refs into the returned `ValueBox`.
- `ownership_lowering.py` now treats runtime `getattr` results whose receiver
  is an attr/call/subscript expression as owned, so chained reads release the
  intermediate object returned by runtime attribute lookup.

## Green Gates

- `gtimeout 60s env -u LC_ALL uv run python -m py_compile pcc/py_frontend/codegen/return_lowering.py pcc/py_frontend/codegen/ownership_lowering.py tests/python/gc_production_contract/test_valuebox_roots.py`
  -> passed.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valuebox_roots.py`
  -> 5 passed in 29.03s.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  -> 5 passed in 1.46s.
- `gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  -> 140 passed in 25.88s.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
  -> 18 passed in 146.89s.
- `gtimeout 360s env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_multi_file_compile.py tests/python/test_py_multi_file_bootstrap_shim.py`
  -> 107 passed in 241.20s.

## Open Boundary

`AUD-P0-GC-SLOT-VISITOR` remains `DONE_WEAK`: broader value payload slots,
remaining pcc-Python mirror parity, future object-slot/value-payload families
not represented by focused contract nodes, and current-source pcc1/pcc2/pcc3
fixed-point proof remain open.
