# Evidence: valuebox object-boundary payload ownership

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

Boxed `@pcc.valueclass` constructor payloads that cross the covered object
boundaries now release their constructor-owned pointer fields after the
container/object owner takes its reference. The focused contract covers:

- tuple literal values;
- dict value literal values;
- `list.append(...)`;
- dict subscript assignment;
- normal object attribute store.

Together with the previous list-literal/list-setitem ownership slice, the
covered ValueBox object-boundary shapes in `test_valuebox_roots.py` now prove
both readback liveness and old-field finalizer release under all five GC
backends.

This is still not a full unified slot visitor closure. It does not prove
bootstrap fixed-point behavior, arbitrary object-boundary APIs, every
value-payload storage form, or pcc-Python mirror completion outside the covered
families.

## Changed Files

- `pcc/py_frontend/codegen/attr_store_lowering.py`
- `tests/python/gc_production_contract/test_valuebox_roots.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`
- `docs/investigations/gc-5backend-valueclass-pointer-payload-roots-no-libpython.md`

## Failure

The expanded `test_valuebox_roots.py` first failed on all five backends after
tuple literal, dict literal, `list.append`, and dict-setitem ownership had
already finalized correctly. The remaining failure was attribute-store
ownership:

```text
...
5
del:dict-set-old
5
```

Expected output continued with:

```text
6
del:attr-old
```

This showed that `cell.value = FinalizerHolder(Track("attr-old"), ...)`
stored a readable ValueBox, but replacing `cell.value = None` did not release
the old constructor field.

## Fix

`attr_store_lowering.py` already had a direct valueclass constructor projection
branch. That branch now:

- boxes the constructor payload with `consume_fields=True`, matching the
  container object-boundary paths;
- releases the owned boxed ValueBox temporary after `_emit_attr_store_value(...)`
  stores it.

The second release was the missing part: without it, the object attribute owned
one reference and the expression temporary owned another, so clearing the
attribute still left the ValueBox and its field alive.

## Gates

```bash
env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valuebox_roots.py
# first expanded run before the attr-store fix: 5 failed; output stopped at
# event count 5 after del:dict-set-old

env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/attr_store_lowering.py \
  tests/python/gc_production_contract/test_valuebox_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valuebox_roots.py
# 5 passed in 26.97s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.21s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 23.75s
```

## Open Boundary

The task remains `DONE_WEAK`. Remaining open work includes broader value
payload slots outside the direct/valuebox forms covered by the current 5-GC
contract, remaining pcc-Python mirror parity outside the covered generic
consumer/object families, object-slot families not yet covered by focused
gates, and bootstrap-complete proof.
