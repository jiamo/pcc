# Direct task-board mutations and immediate dispatch

Date: 2026-08-23

## Claim

The task-board protocol now defines runner-neutral operations for adding,
updating, and explicitly authorized removal of task rows without introducing a
CRUD CLI. An agent directly patches `docs/goal/task-board.yaml`, validates it,
and invokes `resume`.

Normal task additions accept the requested P0, P1, or P2 priority and retain
the established `rank`, priority, id ordering. Priority does not imply an
interrupt. Only an explicit human request may add `dispatch: IMMEDIATE`.

An unfinished `IMMEDIATE` row preempts normal ready rows even when the normal
row has a lower rank or stronger priority. It does not bypass dependencies:
`resume` first selects a dependency-ready task in the immediate row's
unfinished dependency closure, or returns `BLOCKED` if no such work is
executable. After the immediate row reaches `DONE_STRONG`, it is ignored by
selection and `resume` returns to the normal authoritative queue.

Updates target one exact row and preserve unrelated fields and linked
evidence. Removal is destructive and therefore requires an explicit exact id,
a reverse-dependency check, a retained removal receipt, and separate authority
for dependency rewrites or deletion of linked documents/evidence.

Generated full-board and startup views now use the same `resume_state`
selection as the CLI, preventing a recovery document from naming a normal task
while `resume` selects an immediate prerequisite.

## Source identity

This is current shared-filesystem evidence, not a clean-commit or release
claim:

```text
a2cb1efc0e242a5ec53c776a7101f7eb0af2afa70b1fa2178e3522e0f25a6946  AGENTS.md
f1a4a4b8ccd16d57c859c3acdfa2e9249fc2eeaf85e5eed236a926c27458c960  docs/goal/goal-prompt.md
ce0b83b6a7cbd80b635480b36435a9954ed15f92c5fd6aaa811011ed930e700a  scripts/goal_state.py
ece780a42004b50af937e16c863cc090c663ced72f9cb9e7627e66466315a532  tests/test_goal_state.py
c0763db391063f695222e1b0eaf965e52dd86195b38a1efdf2504bf9db98d859  tests/test_goal_startup_docs.py
```

## RED

Before the selector understood dispatch, a P2 immediate row at rank 99 lost to
a normal P0 row at rank 0:

```text
FAILED test_immediate_dispatch_preempts_normal_rank_and_priority
1 failed, 12 passed in 0.14s
```

After implementation, the first combined run reached only the expected stale
generated-state assertion because the new task row had not yet been rendered;
regeneration removed that failure.

## Focused gates

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/test_goal_state.py tests/test_goal_startup_docs.py::test_ordinary_sessions_use_the_runner_neutral_resume_loop tests/test_goal_startup_docs.py::test_task_board_mutations_are_direct_validated_operations tests/test_goal_startup_docs.py::test_current_goal_state_matches_structured_sources
...................                                                      [100%]
19 passed in 0.11s

gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
OK: 384 tasks validated

gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py render-startup --check docs/current-goal-state.md
OK: docs/current-goal-state.md
```

A direct `resume` invocation selected
`CTL-P0-TASK-BOARD-MUTATION-PROTOCOL`, printed `dispatch: IMMEDIATE`, and
retained the post-completion resume instruction.

After promotion to `DONE_STRONG`, `resume` returned `CONTINUE` and selected the
interrupted `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` row at its persisted
strict `class-span-order` checkpoint, proving that the immediate marker no
longer preempts the normal queue once its row is terminal.

## Nonclaims

There is intentionally no generic add/update/remove CLI or UI. The script does
not infer urgency, rewrite dependencies, or delete rows/documents. No GC,
compiler, runtime, bootstrap, broad pytest, or performance gate ran. The
separately registered startup-state size regression remains open.
