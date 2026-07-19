# Agent Goal State Workflow

This directory owns the repository's single goal execution system.

The workflow is agent-neutral. Codex, Claude, or another coding agent should be
able to follow the same protocol, task board, evidence format, and shell
commands.

The startup-state migration is complete:

- `docs/goal/goal-prompt.md` is the single stable execution protocol.
- The former root compatibility entrypoint has been removed; active routing
  names the canonical protocol directly.
- `docs/current-goal-state.md` is generated from the task board and checked
  truth manifest; do not append work logs to it.
- `docs/goal/task-board.yaml` is the machine-readable task board for migrated
  rows.
- `docs/goal/head-truth-manifest.json` is the checked machine truth record.
- `docs/goal/evidence/` holds one small evidence file per completed slice.
- `docs/goal/goal-prompt.md` applies to Codex `/goal`, Claude loops, and direct
  human-launched agent runs.

Use:

```bash
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py next
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py render
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py render-startup \
  --check docs/current-goal-state.md
```

Do not promote a task to `DONE_STRONG` from this file alone. The evidence file
and current gates must prove the exact claim.

For multi-agent loops, keep one agent as the owner of task selection,
validation, and task-board updates. Worker agents may edit their assigned files
and propose evidence, but the owner should run the gates and write the final
task-board status.
