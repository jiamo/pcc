# Evidence: Agent Startup Task-Board Routing

task: goal-management scaffold

status: `DONE_WEAK`

## Changed Files

- `AGENTS.md`
- `docs/goal/task-board.yaml`
- `codex-goal-prompt.md`
- `docs/current-goal-state.md`

## Claim

Repository startup instructions now route directly launched agents into the
structured goal-task system without requiring a `/goal` or loop-specific
prompt. `AGENTS.md` tells agents to read `docs/goal/goal-prompt.md`, inspect
`docs/goal/task-board.yaml` through `scripts/goal_state.py next`, and treat
`DONE_WEAK` as unfinished.

The task board also contains a new agent-neutral P0 row for the GPU TVM/TIRx
host-device split. The row explicitly requires proof of a Metal kernel artifact
plus CPU host-launch boundary and forbids whole-program GPU claims.

## Gates

- `env -u LC_ALL uv run python scripts/goal_state.py validate`
  - result: `OK: 10 tasks validated`
- `env -u LC_ALL uv run python scripts/goal_state.py next`
  - result: `AUD-P0-GC-SLOT-VISITOR`
- `git diff --check -- AGENTS.md docs/goal/task-board.yaml`
  - result: passed

## Open Boundary

This is a workflow-routing slice only. It does not complete the GPU TVM/TIRx
task, the broader goal-management migration, or any compiler/runtime task.
