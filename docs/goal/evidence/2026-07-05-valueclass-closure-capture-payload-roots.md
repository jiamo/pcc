# Evidence: Valueclass Closure-Capture Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `pcc/py_frontend/codegen/assignment_statement_lowering.py`
- `pcc/py_frontend/codegen/unary_call_lowering.py`
- `pcc/py_frontend/codegen/user_function_lowering.py`
- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
- `docs/investigations/gc-5backend-valueclass-pointer-payload-roots-no-libpython.md`

## Claim

The direct valueclass payload-root contract now covers a pointer-bearing
`@pcc.valueclass` payload captured by a nested closure. The regression keeps
`Holder(Nested(list, str), list, str)` in direct payload form, captures it in
`inner()`, forces collection before and after calling `touch_holder(captured)`,
and then reads nested payload fields back under every GC backend.

The fix records valueclass constructor payload locals with their effective
payload type instead of an unannotated `DynType`, then teaches the object ABI
argument boundary to box actual valueclass payload IR through the ValueBox
projection path. This avoids asking generic `DynType` marshalling to handle an
aggregate when a hidden closure argument crosses from direct payload form to an
object-shaped call boundary.

## Gates

- `env -u LC_ALL uv run python -m py_compile pcc/py_frontend/codegen/assignment_statement_lowering.py pcc/py_frontend/codegen/user_function_lowering.py pcc/py_frontend/codegen/unary_call_lowering.py tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: passed
- `/tmp` strict self-backend closure probe under `PCC_GC_BACKEND=4`
  - result: compiled and printed the expected `closure-nested` /
    `closure-holder` readback
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_native_closure.py`
  - result: `4 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_fallback_baseline.py::test_pipeline_and_codegen_host_contract_do_not_drift`
  - result: `1 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`

## Open Boundary

This is a focused closure-captured direct-payload ABI/GC slice. It does not
close broader value payload forms, remaining pcc-Python mirror parity,
pcc1/pcc2/pcc3 bootstrap proof, or the full unified slot visitor production
contract.
