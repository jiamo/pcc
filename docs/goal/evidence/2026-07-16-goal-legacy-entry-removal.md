# Evidence: Legacy Goal Entrypoint Removal

task: `M3-GOAL-LEGACY-ENTRY-REMOVAL`

status: `DONE_STRONG`

source identity: shared local worktree on 2026-07-16; this evidence makes no
clean-commit or release claim.

## Changed behavior

- The former root compatibility entrypoint is absent.
- Maintained README, agent, design, plan, investigation, script, and test
  routing names `docs/goal/goal-prompt.md` as the sole protocol and
  `docs/goal/task-board.yaml` as the executable queue.
- `scripts/compact_goal_startup_docs.py` no longer recreates a compatibility
  pointer when rerun.
- A regression test scans maintained sources and rejects renewed routing
  through the legacy filename.
- The large pre-migration protocol and state archives remain preserved under
  `docs/archive/goal/` as non-executable history.

## Gates

- `gtimeout 60s env -u LC_ALL uv run pytest -q -n0 tests/test_goal_state.py tests/test_goal_startup_docs.py`
  - result: `10 passed in 0.53s`
- `gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate`
  - result: `OK: 78 tasks validated`
- `gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py render-startup --check docs/current-goal-state.md`
  - result: `OK: docs/current-goal-state.md`

## Claim boundary

This proves the repository control-plane migration and maintained-reference
cleanup. It does not claim anything about compiler semantics, NumPy behavior,
bootstrap performance, or the five-GC runtime matrix; no compiler or full-GC
gate was needed for this documentation/control-plane-only task.

## Open boundary

None for this task.
