# Evidence: Direct Valueclass Reassignment Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`

## Claim

The direct valueclass payload GC regression now covers a reassigned
pointer-bearing payload local. The focused program first constructs a
`Holder(Nested(list, str), list, str)` payload, forces collection, overwrites
the same local with a second pointer-bearing `Holder(...)`, forces collection
again, then passes the reassigned payload to a typed callee that mutates nested
pointer fields across collection. A final readback confirms that the new
payload's list and string fields remain live under all five GC backends.

This specifically exercises the root-slot lifetime for the payload alloca
across aggregate reassignment, rather than only the first constructor stored in
a local.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`

## Open Boundary

This is coverage strengthening for reassigned direct payload locals. It does
not close broader value payload forms, remaining pcc-Python mirror parity,
bootstrap proof, or the full unified slot visitor production contract.
