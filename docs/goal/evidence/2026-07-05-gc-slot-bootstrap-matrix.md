# Evidence: GC slot visitor bootstrap matrix

task_id: AUD-P0-GC-SLOT-VISITOR
date: 2026-07-05
status_after: DONE_WEAK

## Claim

The current GC slot/value-payload source state now has five-backend
pcc0->pcc1->pcc2->pcc3 bootstrap proof. The first GC0 bootstrap attempt failed
before any GC-runtime stage: host pcc could not link stage1 because the
IR scaffold emitted `IRBuilder_gep_dyn_inbounds` for dynamic inbounds GEP
indices, but `pcc/llvm_capi/ir.py` did not export that helper. The fix adds the
missing scaffold GEP wrappers and a focused regression for dynamic
`builder.gep(..., inbounds=True)`.

This proves the current covered GC slot/value-payload contract does not break
the five-GC self-host bootstrap matrix. It does not complete the broader slot
visitor task: unrepresented future object-slot/value-payload families and
remaining pcc-Python mirror parity are still open.

## Changed Files

- `pcc/llvm_capi/ir.py`
- `tests/python/test_ir_scaffold_variadic.py`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`

## Verification

Initial red boundary:

```bash
env -u LC_ALL uv run pytest -q -n0 tests/python/gc/test_pcc_bootstrap_full_gc0.py
# ERROR at shared stage1 setup:
# Undefined symbols for architecture arm64:
#   "_user_pcc_llvm_capi_ir_IRBuilder_gep_dyn_inbounds"
```

Focused scaffold regression:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/llvm_capi/ir.py \
  tests/python/test_ir_scaffold_variadic.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_ir_scaffold_variadic.py::test_gep_dynamic_indices_inbounds_uses_exported_helper
# 1 passed in 0.54s

env -u LC_ALL uv run pytest -q -n0 tests/python/test_ir_scaffold_variadic.py
# 17 passed in 2.80s

env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_llvm_capi_ir_parity.py \
  tests/c/test_llvm_capi_end_to_end.py
# 24 passed in 0.43s
```

Bootstrap matrix:

```bash
env -u LC_ALL uv run pytest -q -n0 tests/python/gc/test_pcc_bootstrap_full_gc0.py
# 1 passed in 142.91s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py
# 4 passed in 604.81s
```

Task-board GC contract gates:

```bash
env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.24s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 24.32s
```

## Open Boundary

`AUD-P0-GC-SLOT-VISITOR` remains `DONE_WEAK`. Bootstrap-complete proof for this
source state is now present, but broader value payload slots, remaining
pcc-Python mirror parity, and future object-slot/value-payload families not yet
represented by focused contract nodes remain open.
