# Startup state compaction evidence

Date: 2026-07-10

Task: `M0-STARTUP-STATE-COMPACTION`

## Change

The executable startup set no longer embeds historical work logs:

```text
docs/goal/goal-prompt.md: canonical protocol, about 8,600 bytes
codex-goal-prompt.md: compatibility pointer, 246 bytes
docs/current-goal-state.md: 2,016,845 -> about 4,000 bytes
```

The originals are preserved without deletion as read-only history:

```text
docs/archive/goal/codex-goal-prompt-through-2026-07-09.md: 1,039,570 bytes
docs/archive/goal/current-goal-state-through-2026-07-09.md: 2,016,845 bytes
```

The compact protocol retains the stable claim-routing section numbers used by
`AGENTS.md`. `docs/current-goal-state.md` is now deterministically rendered by
`scripts/goal_state.py render-startup` from the validated task board and checked
truth manifest. `scripts/compact_goal_startup_docs.py` performs the guarded,
one-time archive migration and refuses to archive truncated sources.

## Gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/test_goal_state.py tests/test_goal_startup_docs.py \
  tests/test_head_truth_manifest.py tests/test_head_truth_workflows.py
16 passed in 0.19s

gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
OK: 76 tasks validated

gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py \
  render-startup --check docs/current-goal-state.md
OK: docs/current-goal-state.md

gtimeout 30s git diff --check
exit 0
```

## Claim boundary

This proves startup size, historical preservation, routing content, and
generated-content freshness. It does not turn archived status prose into current
evidence; task rows, finite evidence files, and truth manifests remain the
authorities.
