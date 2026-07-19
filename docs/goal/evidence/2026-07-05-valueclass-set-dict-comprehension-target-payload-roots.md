# Evidence: valueclass set/dict comprehension target payload roots

Task: `AUD-P0-GC-SLOT-VISITOR`

Status: `DONE_WEAK` coverage-strengthening slice. This proves indexed
set-comprehension and dict-comprehension targets that bind direct
`@pcc.valueclass` payload values can read nested pointer-bearing payload fields
after an explicit collection in the comprehension `if` clause. It does not
prove the full unified slot visitor contract, pcc-Python mirror parity, or a
pcc1/pcc2/pcc3 bootstrap fixed point.

## Repro

The strict self-backend probe covered:

```python
labels = {
    current.nested.label
    for current in [Holder(...), Holder(...)]
    if keep()
}
table = {
    current.title: current.nested.label
    for current in [Holder(...), Holder(...)]
    if keep()
}
```

where `keep()` runs `gc.collect()` and returns `True`.

## Result

No implementation change was required in this slice. The previous
comprehension-target fix already routes indexed comprehension targets through
the shared valueclass payload conversion, nested-attr type recovery, and
payload-field root registration path.

The formal regression is appended to
`tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`.

## Gates

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_comp_collections_probe.py -o /tmp/pcc_value_comp_collections_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_comp_collections_probe_bin
# all five backends printed:
# 2
# True
# True
# dict-first-nested
# dict-second-nested

env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.19s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 26.28s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_comprehension_scope_no_libpython.py \
  tests/python/test_py_comprehension_iterators.py \
  tests/python/test_native_comprehension_over_generator.py \
  tests/python/test_python_iteration_parity.py \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[list_comprehension]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[dict_set_comprehension]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[nested_list_comprehension]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[multifor_list_comprehension]'
# 21 passed in 8.02s
```

Bootstrap was not run for this focused coverage slice, so no fixed-point claim
is attached.
