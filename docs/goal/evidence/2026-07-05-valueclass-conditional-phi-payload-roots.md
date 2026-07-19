# Evidence: Direct Valueclass Conditional Phi Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `pcc/backend/self_backend_parse.py`
- `tests/c/test_self_backend.py`
- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`

## Claim

The direct valueclass payload GC regression now covers a conditional
expression that keeps both branches in direct payload form. The frontend emits
a nested aggregate phi with the shape `{ { ptr, ptr }, ptr, ptr }` for
`Holder(Nested(list, str), list, str)`. The self backend now parses phi result
types with the same depth-aware type splitter used by the rest of the textual
IR parser, so nested literal aggregate phi types are accepted instead of being
rejected before codegen.

The five-backend regression builds one no-libpython self-backend executable,
runs it under `PCC_GC_BACKEND=0..4`, and checks both the true and false
conditional branches after explicit collections, a typed callee mutation, and
final direct payload readback.

## Gates

- `env -u LC_ALL uv run python -m py_compile pcc/backend/self_backend_parse.py`
  - result: passed
- `env -u LC_ALL uv run pytest -q -n0 tests/c/test_self_backend.py::test_self_backend_supports_nested_literal_aggregate_phi_in_ir`
  - result: `1 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`

## Open Boundary

This is coverage strengthening for direct valueclass payload conditional
expressions and the self-backend nested literal aggregate phi parser path. It
does not close broader value payload forms, remaining pcc-Python mirror parity,
bootstrap proof, or the full unified slot visitor production contract.
