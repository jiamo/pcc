# Evidence: Direct Valueclass Constructor Temporary Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`

## Claim

The direct valueclass payload GC regression now covers constructor-temporary
payloads in addition to local, function-parameter, method-receiver, and typed
function-return payloads. The focused program passes
`Holder(Nested(list, str), list, str)` directly as a function argument and
calls a method directly on a `Holder(...)` constructor temporary, with
`gc.collect()` inside the callee before and after pointer-field mutations.

This proves the currently generated direct constructor-temporary path preserves
pointer payload fields across explicit collection under all five GC backends.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`

## Open Boundary

This is coverage strengthening for constructor-temporary direct payload shapes.
It does not close broader value payload forms, remaining pcc-Python mirror
parity, bootstrap proof, or the full unified slot visitor production contract.
