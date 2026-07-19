# Evidence: Direct Valueclass Loop-Carried Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`

## Claim

The direct valueclass payload GC regression now covers a pointer-bearing
payload local that is rebound inside a loop and survives repeated explicit
collections before and after each loop-carried assignment.

The focused program starts with one `Holder(Nested(list, str), list, str)`,
rebounds the same direct payload local on both loop iterations, forces
`gc.collect()` before and after each rebinding, then passes the final payload
to a typed callee that mutates nested pointer fields across collection. Final
direct payload readback confirms the loop-carried value's list and string
fields remain live under all five GC backends.

No implementation change was needed for this slice; the existing direct
payload field-root registration covers this loop-carried local shape. The new
test locks that coverage into the common 5-GC production contract.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`

## Open Boundary

This is coverage strengthening for loop-carried direct payload locals. It does
not close broader value payload forms, remaining pcc-Python mirror parity,
bootstrap proof, or the full unified slot visitor production contract.
