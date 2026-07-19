# Evidence: valueclass module-global payload roots

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

Direct `@pcc.valueclass` payloads assigned to a module global can be read from a
user function and keep their pointer-bearing payload fields live across all five
GC backends.

This is a focused module-global direct-payload rooting/readback slice only. It
does not prove module-global payload teardown/reassignment ownership, the full
unified `py_obj_visit_slots` / `py_obj_update_slot` production contract,
pcc-Python mirror parity, pcc1/pcc2/pcc3 fixed point, or a full bootstrap
matrix.

## Changed Files

- `pcc/py_frontend/codegen/module_global_lowering.py`
- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`
- `docs/investigations/gc-5backend-valueclass-pointer-payload-roots-no-libpython.md`

## Failure

The minimized strict self-backend probe first compiled but failed at runtime on
GC0 before any relocation-specific backend:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_probe.py -o /tmp/pcc_value_module_global_probe_bin
# passed before the fix

PCC_GC_BACKEND=0 /tmp/pcc_value_module_global_probe_bin
# NameError: name 'global_holder' is not defined
```

The module-level declare pass allocated `_module_globals` only for scalar/object
assignments, so a top-level valueclass constructor assignment was invisible to
user functions.

## Fix

`module_global_lowering.py` now:

- accepts valueclass payload types when allocating module-global storage;
- declares top-level valueclass payload assignments as module globals;
- recursively zero-initializes literal struct global storage;
- enters GC frame roots for each pointer-bearing field address inside the
  module-global payload aggregate.

## Gates

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/module_global_lowering.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_probe.py -o /tmp/pcc_value_module_global_probe_bin
# passed

for backend in 0 1 2 3 4; do
  PCC_GC_BACKEND=${backend} /tmp/pcc_value_module_global_probe_bin
done
# all five backends printed the expected global payload readback

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.35s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 27.04s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_module_global_projection_self_backend \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_module_global_dyn_projection_boxes_valuebox \
  tests/python/test_py_cross_module_class_inference.py::CrossModuleClassInferenceTests::test_module_qualified_module_global_value \
  tests/python/test_py_module_augassign.py::test_module_global_augassign_uses_module_storage \
  tests/python/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown
# 5 passed in 2.04s
```

## Open Boundary

The task remains `DONE_WEAK`. Broader value payload forms outside the covered
direct local/parameter/method-receiver/typed-return/constructor-temporary/
walrus-target/reassignment-target/conditional-expression/loop-carried-local/
exception-finally-flow/closure-captured/tuple-unpacked/for-loop-target/
list-comprehension-target/set-comprehension-target/dict-comprehension-target/
list-subscript-target/tuple-subscript-target/bool-or-target/bool-and-target/
module-global-target direct payload slots remain open, as do module-global
payload teardown/reassignment ownership, pcc-Python mirror parity, and full
bootstrap proof.
