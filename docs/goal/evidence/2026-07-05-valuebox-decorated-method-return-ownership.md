# ValueBox Decorated Method-Return Ownership

Task: `AUD-P0-GC-SLOT-VISITOR`

Date: 2026-07-05

## Claim

This slice extends the boxed valueclass GC production contract to decorated
method return boundaries:

- a `@staticmethod` returning a direct valueclass constructor as `Any` keeps
  constructor-owned pointer fields alive while the returned object is live;
- a `@classmethod` returning a container carrying a direct valueclass
  constructor keeps payload fields alive while the returned container is live;
- dropping the decorated-method returned object or container releases the
  payload-owned `Track` instance exactly once under all five GC backends.

The slice does not claim full `AUD-P0-GC-SLOT-VISITOR` completion or pcc1
fixed-point closure.

## Changed Files

- `tests/python/gc_production_contract/test_valuebox_roots.py`

## Red Before Green

No implementation red was found in this slice. The new staticmethod and
classmethod return contract nodes passed on the first focused run after being
added, so the existing valuebox/function-return ownership behavior also covers
the tested decorated method binding paths.

## Green Gates

- `gtimeout 60s env -u LC_ALL uv run python -m py_compile tests/python/gc_production_contract/test_valuebox_roots.py`
  -> passed.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valuebox_roots.py`
  -> 5 passed in 1.62s.
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  -> 5 passed in 1.48s.
- `gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  -> 140 passed in 27.29s.

## Open Boundary

`AUD-P0-GC-SLOT-VISITOR` remains `DONE_WEAK`: broader value payload slots,
remaining pcc-Python mirror parity, future object-slot/value-payload families
not represented by focused contract nodes, and current-source pcc1/pcc2/pcc3
fixed-point proof remain open.
