# ValueBox Method-Return Ownership

Task: `AUD-P0-GC-SLOT-VISITOR`

Date: 2026-07-05

## Claim

This slice extends the boxed valueclass GC production contract to bound method
return boundaries:

- direct valueclass constructors returned as `Any` from a method keep
  constructor-owned pointer fields alive while the returned object is live;
- method-returned containers carrying direct valueclass constructor elements
  keep payload fields alive while the returned container is live;
- dropping the method-returned object or container releases the payload-owned
  `Track` instance exactly once under all five GC backends.

The slice does not claim full `AUD-P0-GC-SLOT-VISITOR` completion or pcc1
fixed-point closure.

## Changed Files

- `tests/python/gc_production_contract/test_valuebox_roots.py`

## Red Before Green

No implementation red was found in this slice. The new method-return contract
nodes passed on the first focused run after being added, which shows the
existing function-return/valuebox ownership fixes also cover bound method
returns through the tested shapes.

## Green Gates

- `gtimeout 60s env -u LC_ALL uv run python -m py_compile tests/python/gc_production_contract/test_valuebox_roots.py`
  -> passed.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valuebox_roots.py`
  -> 5 passed in 1.56s.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  -> 5 passed in 1.45s.
- `gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  -> 140 passed in 27.18s.

## Open Boundary

`AUD-P0-GC-SLOT-VISITOR` remains `DONE_WEAK`: broader value payload slots,
remaining pcc-Python mirror parity, future object-slot/value-payload families
not represented by focused contract nodes, and current-source pcc1/pcc2/pcc3
fixed-point proof remain open.
