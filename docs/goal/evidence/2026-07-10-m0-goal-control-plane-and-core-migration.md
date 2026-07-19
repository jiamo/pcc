# M0 goal control plane and core task migration

Date: 2026-07-10

Task ids:

- `M0-GOAL-CONTROL-PLANE`
- `M0-CORE-TASK-MIGRATION`

Changed files:

- `scripts/goal_state.py`
- `tests/test_goal_state.py`
- `docs/goal/task-board.yaml`

Implementation:

- Upgraded the structured board to schema version 2 with an explicit active
  milestone and ordered milestone registry.
- Added required `milestone`, `depends_on`, `rank`, and `exit_criteria` fields
  to every task.
- Changed `next` to consider only dependency-ready tasks in the active
  milestone, ordered by explicit rank, priority, and stable task id.
- Added validation for missing execution fields, unknown milestones and
  dependencies, later-milestone dependencies, self-dependencies, and cycles.
- Migrated finite M1-M4 rows for `B-P0-PKG`, `S-P0-SELF`, `G-P0-GC`,
  `G-P0-GCPERF`, `V-P1-VAL`, and `T-P0-VTHREAD-*`.
- Routed existing GPU, distributed, ds4, and unrelated audit breadth to the
  deferred M5 lane. Their prior evidence and open boundaries remain intact.

Gates:

- `gtimeout 60s env -u LC_ALL uv run pytest -q -n0 tests/test_goal_state.py`
  -> `3 passed in 0.24s`.
- `gtimeout 30s env -u LC_ALL uv run python -m py_compile scripts/goal_state.py tests/test_goal_state.py`
  -> exit 0.
- `gtimeout 60s env -u LC_ALL uv run python scripts/goal_state.py validate`
  -> `OK: 72 tasks validated`.
- `gtimeout 60s env -u LC_ALL uv run python scripts/goal_state.py next`
  -> selected `M0-GOAL-CONTROL-PLANE` by milestone/rank while the slice was
  still `IN_PROGRESS`.
- `gtimeout 15s git diff --check -- docs/goal/task-board.yaml scripts/goal_state.py tests/test_goal_state.py`
  -> exit 0.

Claim:

The structured board now has an explicit, validated milestone/dependency
control plane. YAML file position and open-ended deferred `DONE_WEAK` rows no
longer select active M0-M4 work. The package-first M1-M4 critical path is now
represented in the unique task board.

Open boundary:

This evidence does not claim M0 complete. The commit-bound HEAD truth
manifest, GitHub status checks, and startup-state compaction remain open M0
cards. No bootstrap, package success, five-GC equality, NumPy, performance, or
virtual-thread scale claim is made by this governance-only slice.
