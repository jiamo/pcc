# goal-order single pack 0379-0392

This pack is organized in goal.md order and focuses on real gates and
front-end type debt that block no-libpython bootstrap work.

## No.5 Backend #0 production/default audit

`scripts/pcc_backend0_default_audit.py` records Backend #0 default audit
commands and JSON evidence.  It has a dry-run mode for review and a normal mode
that executes configured commands under `PCC_GC_BACKEND=0`.

## No.13 Bootstrap five-GC matrix

`scripts/pcc_five_gc_matrix.py` records:

```text
pcc0/pcc1/pcc2 × backend0..4
```

Each matrix entry runs with `PCC_PYTHON_LIBPYTHON=off`.

## No.17 layer1 split / ownership

`pcc.py_frontend.mixin_ownership` builds the cross-module base→derived graph
needed for split layer1 mixin self inference.

This pack also fixes two concrete type debts exposed by the split:

- `typing.Optional[ir.X]` / `Optional[ir.X]` / string `Optional[ir.X]` no
  longer collapse to `DynType` during annotation parsing.
- `pcc.llvm_capi.ir.Function` is a `Value`, matching builder API usage.

## Gate

```bash
bash scripts/run_goal_order_gate.sh
```
