# GOAL-P0-STARTUP-STATE-BOUNDED-RENDER: count-independent startup projection

## Problem

`render_startup_markdown` (scripts/goal_state.py) rendered EVERY unfinished
active-milestone task as a row in the "Active task table", so the generated
docs/current-goal-state.md grew with the task count. The checked doc had grown
to 35,393 bytes (368 M5 unfinished rows), and the checked copy was also stale
(it still recorded 415 milestone / 195 DONE_STRONG). Two tests were RED:
`test_historical_ledgers_are_preserved_and_startup_docs_are_compact`
(asserts the state doc < 20,000 bytes) and
`test_current_goal_state_matches_structured_sources` (checked doc must equal
the regenerated render).

## Fix

Hard-bound the active-task table to the `_STARTUP_TABLE_MAX_ROWS = 40`
highest-priority (lowest rank, then id) unfinished rows, followed by a single
"_N more unfinished rows are not shown ... the complete queue is
docs/goal/task-board.yaml_" line. The bound is count-INDEPENDENT: hundreds of
unfinished tasks still produce at most 40 table rows + one summary line. The
milestone/status counts, the dependency-ready selected task (id + title + open
boundary), the authority/routing links, and the checked-truth manifest / gate
table are all unchanged. docs/goal/task-board.yaml remains the only executable
queue; no task status, dependency, or history was rewritten to shrink the
projection.

Regenerated docs/current-goal-state.md via `render-startup --write`: 11,979
bytes (< 20,000), and no longer stale (M5 counts now match the live board).

## Gates (all green)

- `tests/test_goal_state.py tests/test_goal_startup_docs.py`: 28 passed,
  including the new `test_startup_projection_is_bounded_under_hundreds_of_tasks`
  (400 synthetic unfinished tasks -> render < 20 KiB, exact counts retained,
  deterministic dependency-ready selection with id/title/open-boundary shown,
  routing links present, exactly 40 table rows + the omitted-count line), and
  the two previously-RED compact/match tests are now green.
- `scripts/goal_state.py validate`: OK, 469 tasks.
- `scripts/goal_state.py render-startup --check docs/current-goal-state.md`: OK
  (checked doc deterministically matches the generator).
