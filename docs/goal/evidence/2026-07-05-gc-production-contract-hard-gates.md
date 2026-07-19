# Evidence: GC production contract hard gates

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

The current `tests/python/gc_production_contract` suite is now a hard 0..4
backend gate with no active xfail harnesses. The exception-root and
finalizer-resurrection tests previously kept empty `_XFAIL = set()` scaffolds
from resolved bugs; those are now explicit `PCC_GC_BACKEND=0..4`
parametrizations. The weakref/finalizer docstring now matches the current
policy: backend divergence must be root-caused, not hidden behind a local xfail.

This is a contract-hardening slice only. It does not prove bootstrap
fixed-point behavior and does not expand the production contract beyond the
currently collected 140 nodes.

## Changed Files

- `tests/python/gc_production_contract/test_exception_roots.py`
- `tests/python/gc_production_contract/test_finalizer_resurrection.py`
- `tests/python/gc_production_contract/test_weakref_finalizer.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`

## Verification

```bash
env -u LC_ALL uv run pytest -q -n0 -rxX \
  tests/python/gc_production_contract/test_exception_roots.py \
  tests/python/gc_production_contract/test_finalizer_resurrection.py
# 10 passed in 1.01s

env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_exception_roots.py \
  tests/python/gc_production_contract/test_finalizer_resurrection.py \
  tests/python/gc_production_contract/test_weakref_finalizer.py
# passed

rg -n "pytest\\.mark\\.xfail|_XFAIL" tests/python/gc_production_contract
# no output

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.21s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 23.60s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract --collect-only
# 140 tests collected
```

## Open Boundary

The task remains `DONE_WEAK`. The 140-node GC production contract is hard-gated
on all five backends, but broader value payload slots outside the current
contract, remaining pcc-Python mirror parity outside covered families, future
object-slot families not yet represented by a focused contract node, and
bootstrap-complete proof remain open.
