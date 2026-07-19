# Evidence: valuebox varargs and kwargs payload roots

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

The boxed valueclass payload-root contract now covers valueclasses that cross
object-shaped call boundaries through `*args` and `**kwargs` formals. The test
stores `Holder(Bag(list, str, int), str)` values in a varargs tuple and kwargs
dict, forces collection, mutates nested `bag.items` through the boxed values,
forces collection again, and reads nested payload fields back under every GC
backend.

This is a coverage-strengthening slice only. No implementation change was
needed: existing ValueBox projection plus tuple/dict slot tracing already covers
these call-boundary containers.

## Changed Files

- `tests/python/gc_production_contract/test_valuebox_roots.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`

## Verification

```bash
env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valuebox_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valuebox_roots.py
# 5 passed in 1.27s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 23.25s
```

## Open Boundary

`AUD-P0-GC-SLOT-VISITOR` remains `DONE_WEAK`. The boxed object-boundary set now
includes varargs tuple and kwargs dict formals, but broader value payload slots,
remaining pcc-Python mirror parity, and future object-slot/value-payload
families not yet represented by focused contract nodes remain open.
