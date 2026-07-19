# Evidence: Valuebox Container-Mutation Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `tests/python/gc_production_contract/test_valuebox_roots.py`

## Claim

The common valuebox payload-root contract now covers valueclasses that cross
object boundaries through runtime container mutation and object attribute
stores. The regression appends a nested pointer-bearing `@pcc.valueclass` to a
list, overwrites a list slot with one, stores one through dict subscript
assignment, and stores one in a normal object attribute. It then forces
collection, mutates nested payload lists through each boxed value, forces
collection again, and reads nested payload fields back under every GC backend.

No implementation change was needed for this slice. The existing object
boundary boxing path preserves pointer-bearing payload fields in these mutation
and attribute-store shapes.

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

This is coverage strengthening for boxed valueclass payloads crossing
`list.append`, list subscript assignment, dict subscript assignment, and normal
object attribute-store boundaries. It does not close direct payload forms
outside the currently covered shapes, remaining pcc-Python mirror parity, pcc1
bootstrap proof, or the full unified slot visitor production contract.
