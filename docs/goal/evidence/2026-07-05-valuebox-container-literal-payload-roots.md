# Evidence: Valuebox Container-Literal Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `tests/python/gc_production_contract/test_valuebox_roots.py`

## Claim

The common valuebox payload-root contract now covers valueclasses that cross
object boundaries through container literals. The regression stores nested
pointer-bearing `@pcc.valueclass` payloads in a list literal, tuple literal,
and dict value literal, forces collection, mutates nested list fields through
the boxed values, forces collection again, and reads the nested payload fields
back under every GC backend.

No implementation change was needed for this slice. The existing object
boundary boxing path preserves the pointer-bearing payload fields in these
container literal shapes.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valuebox_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run python -m py_compile tests/python/gc_production_contract/test_valuebox_roots.py`
  - result: passed
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`

## Open Boundary

This is coverage strengthening for boxed valueclass payloads crossing list,
tuple, and dict value-literal object boundaries. It does not close direct
payload forms outside the currently covered shapes, remaining pcc-Python mirror
parity, pcc1 bootstrap proof, or the full unified slot visitor production
contract.
