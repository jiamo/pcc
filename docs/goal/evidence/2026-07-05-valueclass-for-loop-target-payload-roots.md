# Evidence: valueclass for-loop target payload roots

Task: `AUD-P0-GC-SLOT-VISITOR`

Status: `DONE_WEAK` slice. This proves direct `@pcc.valueclass` payload values
loaded from a list/tuple-backed `for` loop target are materialized as native
payload aggregates and have pointer-bearing payload fields registered as GC
roots. It does not prove the full unified slot visitor contract, pcc-Python
mirror parity, or a pcc1/pcc2/pcc3 bootstrap fixed point.

## Repro

The minimized strict self-backend probe iterated over a list literal of
`Holder(Nested(list, str), list, str)` values:

```python
for current in [
    Holder(Nested([120], "for-first-nested"), ["for-first-head"], "for-first-holder"),
    Holder(Nested([121], "for-second-nested"), ["for-second-head"], "for-second-holder"),
]:
    gc.collect()
    touch_holder(current)
    gc.collect()
    last = current
```

Before the fix, the loop target slot had aggregate payload storage but received
the raw `PyObject*` returned by `py_list_get`, so payload field reads decoded
random pointers and backend #4 crashed after printing a random integer and
`<null>`.

## Fix

`pcc/py_frontend/codegen/for_loop_lowering.py` now detects valueclass payload
element types on the list/tuple indexed loop path, converts the fetched object
through `_emit_object_to_valueclass_payload(...)`, stores the resulting native
payload aggregate, and registers pointer-bearing payload field roots for the
loop target with `_ensure_valueclass_payload_gc_roots(...)`.

The formal regression is appended to
`tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`.

## Gates

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_for_probe.py -o /tmp/pcc_value_for_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_for_probe_bin
# all five backends printed the expected for-first / for-second payload readback

env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/for_loop_lowering.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.06s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 24.46s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_for_generic_iterable.py \
  tests/python/test_python_iteration_parity.py \
  tests/python/test_native_float_add_generic.py
# 14 passed in 5.38s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_typed_int_unboxed.py::test_typed_list_int_loop_defaults_to_boxed_tagged_shape \
  tests/python/test_py_typed_int_unboxed.py::test_unsafe_i64_typed_list_int_loop_keeps_accumulator_unboxed \
  tests/python/test_py_typed_int_unboxed.py::test_typed_list_i64_runtime_helpers_match_c_fast_path \
  tests/python/test_py_typed_int_unboxed.py::test_unsafe_i64_typed_list_int_loop_falls_back_for_heap_int_elements
# 4 passed in 0.84s
```

Bootstrap was not run for this focused frontend/codegen slice, so no fixed-point
claim is attached.
