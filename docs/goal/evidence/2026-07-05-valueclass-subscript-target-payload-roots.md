# Evidence: valueclass list/tuple subscript target payload roots

Task: `AUD-P0-GC-SLOT-VISITOR`

Status: `DONE_WEAK` coverage-strengthening slice. This proves direct
`@pcc.valueclass` payload values loaded from typed list and tuple subscript
expressions can be rebound to direct locals, survive explicit collection, and
mutate/read nested pointer-bearing payload fields under all five GC backends.
It does not prove the full unified slot visitor contract, pcc-Python mirror
parity, or a pcc1/pcc2/pcc3 bootstrap fixed point.

## Repro

The strict self-backend probes covered:

```python
values = [Holder(...), Holder(...)]
picked = values[1]
gc.collect()
touch_holder(picked)
```

and:

```python
values = (Holder(...), Holder(...))
picked = values[1]
gc.collect()
touch_holder(picked)
```

where `touch_holder(...)` mutates `picked.nested.items` and `picked.trailer`
across explicit collections, then reads nested pointer fields back.

## Result

No implementation change was required in this slice. The existing direct
assignment/subscript path already preserves the valueclass payload projection
and registers pointer-bearing payload roots for the rebound local.

The formal regression is appended to
`tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`.

## Gates

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_subscript_probe.py -o /tmp/pcc_value_subscript_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_subscript_probe_bin
# all five backends printed the expected sub-second payload readback

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_tuple_subscript_probe.py -o /tmp/pcc_value_tuple_subscript_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_tuple_subscript_probe_bin
# all five backends printed the expected tuple-sub-second payload readback

env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.32s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 27.66s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_subscript_raise.py \
  tests/python/test_native_list_index_error.py \
  tests/python/test_native_tuple_index_range.py \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_tuple_unpack_rebind_to_borrowed_value_does_not_overrelease \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[list_tuple_unpack_slice_mutation]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[negative_index_and_slices]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[tuple_list_constructors]'
# 8 passed in 3.52s
```

Bootstrap was not run for this focused coverage slice, so no fixed-point claim
is attached.
