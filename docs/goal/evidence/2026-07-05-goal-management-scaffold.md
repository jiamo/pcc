# Evidence: Structured Goal Management Scaffold

task: operational goal workflow

status: `DONE_WEAK`

## Changed Files

- `docs/goal/README.md`
- `docs/goal/task-board.yaml`
- `docs/goal/goal-prompt.md`
- `scripts/goal_state.py`

## Claim

The repository now has a minimal structured goal-management scaffold:

- a machine-readable task board for the migrated `AUD-*` rows;
- per-slice evidence files under `docs/goal/evidence/`;
- a reusable agent-loop prompt for Codex `/goal`, Claude loops, or another
  coding agent;
- a standard-library script with `next`, `validate`, and `render` commands.

## Gates

- `env -u LC_ALL uv run python scripts/goal_state.py validate`
  - result: `OK: 9 tasks validated`
- `env -u LC_ALL uv run python scripts/goal_state.py next`
  - result: prints `AUD-P0-GC-SLOT-VISITOR`
- `env -u LC_ALL uv run python scripts/goal_state.py render`
  - result: renders a concise task-board summary
- `env -u LC_ALL uv run python -m py_compile scripts/goal_state.py`
  - result: passed

## Open Boundary

This does not yet replace `codex-goal-prompt.md` or
`docs/current-goal-state.md`. It is the first safe migration step.
