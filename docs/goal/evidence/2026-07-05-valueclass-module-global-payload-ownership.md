# Evidence: valueclass module-global payload ownership

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

Direct `@pcc.valueclass` payloads stored in module globals now release their
old pointer-bearing payload fields on reassignment and clear those fields at
module teardown. The same field addresses are registered as module roots, so
the overwrite and shutdown paths update the real root slots rather than a
shadow copy.

This is a focused module-global direct-payload ownership slice only. It does
not prove the full unified `py_obj_visit_slots` / `py_obj_update_slot`
production contract, pcc-Python mirror parity, pcc1/pcc2/pcc3 fixed point, or
a full bootstrap matrix.

## Changed Files

- `pcc/py_frontend/codegen/module_global_lowering.py`
- `pcc/py_frontend/codegen/module_lifecycle_lowering.py`
- `pcc/py_frontend/codegen/assignment_store_lowering.py`
- `pcc/py_frontend/codegen/assignment_statement_lowering.py`
- `pcc/py_frontend/codegen/host_contract.py`
- `pcc/py_frontend/codegen/_l1_codegen_static_methods.py`
- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`
- `docs/investigations/gc-5backend-valueclass-pointer-payload-roots-no-libpython.md`

## Failure

The minimized strict self-backend reassignment probe matched CPython on live
readback but failed to finalize the old module-global payload field:

```bash
env -u LC_ALL uv run python /tmp/pcc_value_module_global_reassign_probe.py
# new-holder
# new
# 1
# del:old

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_reassign_probe.py \
  -o /tmp/pcc_value_module_global_reassign_probe_bin
# passed before the fix

for backend in 0 1 2 3 4; do
  PCC_GC_BACKEND=${backend} /tmp/pcc_value_module_global_reassign_probe_bin
done
# all five backends printed new-holder/new/0 before the fix
```

The module-global valueclass payload path raw-stored the aggregate and returned.
It never cleared the old pointer field root, so the old object reference stayed
live instead of being released when the global was overwritten.

## Fix

`module_global_lowering.py` now has field-slot helpers for module-global
valueclass payloads. Reassigning a module-global payload:

- clears every old pointer-bearing field with `pcc_gc_store_root(slot, NULL)`;
- stores the new aggregate payload;
- refreshes every new pointer-bearing field through `pcc_gc_store_root(...)`
  so backend #3/#4 see the real root slot update.

`module_lifecycle_lowering.py` uses the same clear helper during module fini.
The L1 host contract and generated static-method table include the new helper
surface.

## Gates

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_reassign_probe.py \
  -o /tmp/pcc_value_module_global_reassign_probe_bin
# passed

for backend in 0 1 2 3 4; do
  PCC_GC_BACKEND=${backend} /tmp/pcc_value_module_global_reassign_probe_bin
done
# all five backends printed new-holder/new/1/del:old

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_teardown_probe.py \
  -o /tmp/pcc_value_module_global_teardown_probe_bin
# passed

for backend in 0 1 2 3 4; do
  PCC_GC_BACKEND=${backend} /tmp/pcc_value_module_global_teardown_probe_bin
done
# all five backends printed main_done and stderr del:shutdown

env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/module_global_lowering.py \
  pcc/py_frontend/codegen/module_lifecycle_lowering.py \
  pcc/py_frontend/codegen/assignment_store_lowering.py \
  pcc/py_frontend/codegen/assignment_statement_lowering.py \
  pcc/py_frontend/codegen/host_contract.py \
  pcc/py_frontend/codegen/_l1_codegen_static_methods.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.33s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 24.26s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown \
  tests/python/test_py_module_augassign.py::test_module_global_augassign_uses_module_storage
# 2 passed in 0.80s
```

## Open Boundary

The task remains `DONE_WEAK`. Broader value payload forms outside the covered
direct local/parameter/method-receiver/typed-return/constructor-temporary/
walrus-target/reassignment-target/conditional-expression/loop-carried-local/
exception-finally-flow/closure-captured/tuple-unpacked/for-loop-target/
list-comprehension-target/set-comprehension-target/dict-comprehension-target/
list-subscript-target/tuple-subscript-target/bool-or-target/bool-and-target/
module-global-target direct payload slots, boxed list/tuple/dict-value
container literal, boxed list.append/list-setitem/dict-setitem/attribute-store
object boundaries, generic pcc-Python trace/subtract/promote/remap/clear
consumer, and object-slot families remain open.
