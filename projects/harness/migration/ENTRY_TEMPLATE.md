# Concise migration slice title

- Schema: pcc.harness.migration.v1
- Sequence: 0000
- PCC change: pending:lowercase-slice-id
- Upstream range: <inclusive-start-commit>..<inclusive-end-commit>
- Native-only rationale: not-applicable
- Changed domains: packages/core/example, pcc/reusable-facility
- Tasks: HARNESS-P1-EXAMPLE
- GUI impact: none

## Behavior migrated

- Name observable behavior and failure behavior, not source files alone.

## PCC facilities

- Use `none`, or name reusable compiler/runtime facilities and their regressions.

## Verification

- NOT-RUN | `gtimeout ...` | State why the gate has not run yet.

## GUI evidence

- NOT-APPLICABLE | This slice does not change visible GUI behavior.

## Remaining boundaries

- Name every known gap; use `none` only when the slice's finite boundary is complete.
