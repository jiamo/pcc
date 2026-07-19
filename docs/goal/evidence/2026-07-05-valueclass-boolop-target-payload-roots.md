# Evidence: valueclass bool-op target payload roots

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

Direct `@pcc.valueclass` payloads produced by short-circuit `or` and `and`
expressions preserve pointer-bearing payload roots when rebound to a local.

This is a focused coverage slice only. It does not prove the full unified
`py_obj_visit_slots` / `py_obj_update_slot` production contract, pcc-Python
mirror parity, pcc1/pcc2/pcc3 fixed point, or a full bootstrap matrix.

## Changed Files

- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`
- `docs/investigations/gc-5backend-valueclass-pointer-payload-roots-no-libpython.md`

No implementation source files were changed in this slice.

## Probe

Strict self-backend probe:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_boolop_probe.py -o /tmp/pcc_value_boolop_probe_bin
# passed
```

Runtime matrix:

```bash
for backend in 0 1 2 3 4; do
  PCC_GC_BACKEND=${backend} /tmp/pcc_value_boolop_probe_bin
done
# all five backends printed the expected bool-left and bool-second payload
# readback after explicit gc.collect() and typed-callee mutation
```

## Gates

```bash
env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.33s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 26.75s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_constructor_condition_truthiness_self_backend \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_conditional_expr_projection_self_backend \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_conditional_expr_projection_boxes_valuebox \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[bool_short_circuit]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[conditional_expression]'
# 5 passed in 1.77s
```

## Open Boundary

The task remains `DONE_WEAK`. Broader value payload forms outside the covered
direct local/parameter/method-receiver/typed-return/constructor-temporary/
walrus-target/reassignment-target/conditional-expression/loop-carried-local/
exception-finally-flow/closure-captured/tuple-unpacked/for-loop-target/
list-comprehension-target/set-comprehension-target/dict-comprehension-target/
list-subscript-target/tuple-subscript-target/bool-or-target/bool-and-target
direct payload slots are still open, as are pcc-Python mirror parity and full
bootstrap proof.
