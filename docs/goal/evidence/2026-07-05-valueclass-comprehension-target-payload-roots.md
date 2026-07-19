# Evidence: valueclass comprehension target payload roots

Task: `AUD-P0-GC-SLOT-VISITOR`

Status: `DONE_WEAK` slice. This proves list-comprehension targets that bind
direct `@pcc.valueclass` payload values can read nested pointer-bearing payload
fields after an explicit collection in the comprehension `if` clause. It does
not prove the full unified slot visitor contract, pcc-Python mirror parity, or
a pcc1/pcc2/pcc3 bootstrap fixed point.

## Repro

The minimized strict self-backend probe used:

```python
values = [
    current.nested.label
    for current in [
        Holder(Nested([130], "comp-first-nested"), ["comp-first-head"], "comp-first-holder"),
        Holder(Nested([131], "comp-second-nested"), ["comp-second-head"], "comp-second-holder"),
    ]
    if keep()
]
```

where `keep()` runs `gc.collect()` and returns `True`.

Before the fix, all five GC backends failed with `AttributeError: label`.
Removing the collection still failed, while `current.title` succeeded. That
localized the bug to nested valueclass payload attribute type recovery inside
the comprehension target scope, not to a collector-specific relocation path.

## Fix

Changed files for this slice:

- `pcc/py_frontend/codegen/attr_load_lowering.py`
- `pcc/py_frontend/codegen/comprehension_lowering.py`
- `pcc/py_frontend/codegen/host_contract.py`
- `pcc/py_frontend/codegen/_l1_codegen_static_methods.py`
- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`

`attr_load_lowering.py` now resolves valueclass payload expression types through
the current environment, so `current.nested.label` can infer that
`current.nested` is a `Nested` payload even when the comprehension AST did not
propagate that nested field type.

`comprehension_lowering.py` now registers pointer-bearing payload field roots
for indexed valueclass comprehension targets and clears those hidden target
pointer fields before restoring the enclosing comprehension scope.

The formal regression is appended to
`tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`.

## Gates

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_comp_probe.py -o /tmp/pcc_value_comp_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_comp_probe_bin
# all five backends printed comp-first-nested / comp-second-nested

env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/attr_load_lowering.py \
  pcc/py_frontend/codegen/comprehension_lowering.py \
  pcc/py_frontend/codegen/host_contract.py \
  pcc/py_frontend/codegen/_l1_codegen_static_methods.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.14s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 26.70s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_comprehension_scope_no_libpython.py \
  tests/python/test_py_comprehension_iterators.py \
  tests/python/test_native_comprehension_over_generator.py \
  tests/python/test_python_iteration_parity.py
# 17 passed in 6.91s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_frontend_ir_pass_pipeline.py \
  tests/python/test_ir_scaffold_symbols.py \
  tests/python/test_ir_scaffold_simple_methods.py
# 267 passed in 31.82s
```

Bootstrap was not run for this focused frontend/codegen slice, so no fixed-point
claim is attached.
