# Evidence: valuebox list-setitem payload ownership

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

Direct `@pcc.valueclass` constructor payloads that are boxed into a ValueBox
for list-literal/list-setitem object boundaries now transfer owned
pointer-bearing constructor fields correctly. After `py_valuebox_set_field`
retains each field, the constructor-owned field reference is released, and the
boxed temporary itself is released after the list store retains it.

This is a focused boxed ValueBox ownership slice for list literal plus list
subscript assignment. It does not prove tuple/dict-value literal ownership,
dict-setitem ownership, attribute-store ownership, pcc1/pcc2/pcc3 fixed point,
or the full unified `py_obj_visit_slots` / `py_obj_update_slot` production
contract.

## Changed Files

- `pcc/py_frontend/codegen/type_abi_lowering.py`
- `pcc/py_frontend/codegen/ownership_lowering.py`
- `pcc/py_frontend/codegen/cpy_bridge_lowering.py`
- `pcc/py_frontend/codegen/exact_int_lowering.py`
- `pcc/py_frontend/codegen/literal_lowering.py`
- `tests/python/gc_production_contract/test_valuebox_roots.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`
- `docs/investigations/gc-5backend-valueclass-pointer-payload-roots-no-libpython.md`

## Failure

The minimized strict self-backend repro matched live readback but leaked the
old constructor field across a list overwrite:

```python
cell = [Holder(Track("old"), "old-holder")]
gc.collect()
cell[0] = Holder(Track("new"), "new-holder")
gc.collect()
```

CPython printed `new-holder/new/1/del:old`. Before the fix, strict
self-backend pcc printed `new-holder/new/0` under `PCC_GC_BACKEND=0..4`.

IR inspection showed the boxed ValueBox temporary was released after
`py_list_set`, but fields extracted from the constructor payload were retained
by `py_valuebox_set_field(...)` without releasing the constructor-owned field
reference.

## Fix

`_emit_valueclass_payload_to_object(...)` now accepts `consume_fields=False` by
default. When the source expression is a direct valueclass constructor payload,
callers pass `consume_fields=True`, causing each pointer field to be released
after the ValueBox field setter retains it. Existing valueclass variables still
box with `consume_fields=False`, so their borrowed/rooted payload fields are not
stolen.

The direct object conversion path enables field consumption for constructor
payloads. The container bridge carries the same flag for list/dict/tuple literal
paths when the original AST expression is a direct valueclass constructor.

## Gates

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_valuebox_list_overwrite_probe.py \
  -o /tmp/pcc_valuebox_list_overwrite_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_valuebox_list_overwrite_probe_bin
# all five backends printed new-holder/new/1/del:old

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_nonlocal_payload_probe.py \
  -o /tmp/pcc_value_nonlocal_payload_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_nonlocal_payload_probe_bin
# all five backends printed the expected nested payload readback plus 1/del:old

env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valuebox_roots.py \
  pcc/py_frontend/codegen/ownership_lowering.py \
  pcc/py_frontend/codegen/type_abi_lowering.py \
  pcc/py_frontend/codegen/cpy_bridge_lowering.py \
  pcc/py_frontend/codegen/exact_int_lowering.py \
  pcc/py_frontend/codegen/literal_lowering.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valuebox_roots.py
# 5 passed in 1.15s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.23s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 24.52s
```

## Open Boundary

The task remains `DONE_WEAK`. Remaining open work includes broader value
payload slots and remaining pcc-Python mirror parity outside the covered direct
payload slots, covered boxed readback boundaries, and this boxed
list-literal/list-setitem constructor-field ownership slice. Tuple/dict-value
literal ownership, list.append/dict-setitem/attribute-store ownership,
object-slot families not yet covered, and bootstrap proof remain open.
