# Evidence: Direct Valueclass Walrus Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`

## Claim

The direct valueclass payload GC regression now covers a walrus-bound
pointer-bearing payload. The focused program binds
`Holder(Nested(list, str), list, str)` through `if (walrus := Holder(...))`,
forces collection before passing the payload to a typed callee, mutates nested
pointer fields in the callee across another collection, and then reads the
walrus-bound payload again after a final collection.

Existing valueclass source-shape coverage already locks the walrus constructor
path as a direct payload shape rather than ordinary instance construction; this
slice adds the five-GC pointer-payload lifetime proof for that shape.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`

## Open Boundary

This is coverage strengthening for walrus-bound direct payload roots. It does
not close broader value payload forms, remaining pcc-Python mirror parity,
bootstrap proof, or the full unified slot visitor production contract.
