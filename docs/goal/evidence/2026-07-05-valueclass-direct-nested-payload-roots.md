# Evidence: Direct Nested Valueclass Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`

## Claim

The direct valueclass payload GC regression now covers nested pointer-bearing
payload fields in addition to direct `Bag(items, label)` fields. The focused
program keeps `Holder(Nested(list, str), list, str)` in direct payload form and
checks local, function-parameter, method-receiver, and typed function-return
access across explicit `gc.collect()` calls under all five GC backends.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`

## Open Boundary

This is coverage strengthening for the direct local/parameter/method-receiver
and typed-return payload shapes. It does not close broader value payload forms,
remaining pcc-Python mirror parity, or bootstrap proof.
