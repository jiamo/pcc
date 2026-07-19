# Evidence: Valueclass Tuple-Unpack Payload Roots

task: `AUD-P0-GC-SLOT-VISITOR`

status: `DONE_WEAK`

## Changed Files

- `pcc/py_frontend/codegen/assignment_store_lowering.py`
- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
- `docs/investigations/gc-5backend-valueclass-pointer-payload-roots-no-libpython.md`

## Claim

The direct valueclass payload-root contract now covers tuple-literal unpack
assignment into fresh pointer-bearing valueclass payload locals. The regression
unpacks two `Holder(Nested(list, str), list, str)` values into `left` and
`right`, forces collection, mutates both through a typed callee, forces
collection again, and reads both payloads back under every GC backend.

Before the fix, the minimized strict self-backend probe failed at compile time
with `Layer 1 tuple-unpack target 'left' has unsupported type ClassType`.
Tuple-unpack name storage now accepts valueclass payload target types, allocates
payload storage, and registers pointer-bearing payload fields as borrowed GC
roots after the store.

## Gates

- `env -u LC_ALL uv run python -m py_compile pcc/py_frontend/codegen/assignment_store_lowering.py tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: passed
- `/tmp` strict self-backend tuple-unpack probe under `PCC_GC_BACKEND=4`
  - result: compiled and printed the expected `unpack-left-*` /
    `unpack-right-*` readback
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract`
  - result: `140 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_native_float_tuple_unpack.py`
  - result: `2 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_list_unpack_assignment.py tests/python/test_py_unpacking.py tests/python/test_gc_effectiveness.py::test_tuple_unpack_instance_return_no_growth tests/python/test_gc_effectiveness.py::test_tuple_unpack_dict_self_cycle_reclaims_between_iterations`
  - result: `5 passed`
- `env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_tuple_unpack_rebind_to_borrowed_value_does_not_overrelease`
  - result: `1 passed`

## Open Boundary

This is a focused tuple-unpacked direct-payload assignment/rooting slice. It
does not close broader value payload forms, remaining pcc-Python mirror parity,
pcc1/pcc2/pcc3 bootstrap proof, or the full unified slot visitor production
contract.
