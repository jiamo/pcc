# Evidence: valueclass method keyword-argument payload roots

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

The direct valueclass payload-root contract now covers method calls where a
pointer-bearing valueclass payload is supplied through a keyword argument. The
test exercises both a constructor temporary passed as
`toucher.touch_holder_arg(h=Holder(...))` and an existing direct payload local
passed as `toucher.touch_holder_arg(h=selected)`, with explicit collections
before callee mutation and readback under all five GC backends.

This is a coverage-strengthening slice only. No implementation change was
needed: existing method-call keyword resolution and direct payload frame-root
registration already cover this shape.

## Changed Files

- `tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`

## Verification

```bash
env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.34s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 23.20s
```

## Open Boundary

`AUD-P0-GC-SLOT-VISITOR` remains `DONE_WEAK`. The covered direct-payload set now
includes method keyword-argument calls, but broader value payload slots,
remaining pcc-Python mirror parity, and future object-slot/value-payload
families not yet represented by focused contract nodes remain open.
