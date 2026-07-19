# Evidence: Direct Valueclass Exception/Finally Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`

## Claim

The direct valueclass payload GC regression now covers a pointer-bearing
payload local that crosses `try` / `except` / `finally` control flow. The
focused program creates a `Holder(Nested(list, str), list, str)`, rebinds the
same direct payload local inside the `try` block, forces `gc.collect()` before
and after the rebinding, raises and catches `ValueError`, forces collection in
the handler and `finally`, then mutates and reads the final payload after the
exception path completes.

No implementation change was needed for this slice; the existing direct
payload field-root registration covers this exception/finally lifetime shape.
The new test locks that coverage into the common 5-GC production contract.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`

## Open Boundary

This is coverage strengthening for direct payload locals crossing
`try`/`except`/`finally`. It does not close broader value payload forms,
remaining pcc-Python mirror parity, bootstrap proof, or the full unified slot
visitor production contract.
