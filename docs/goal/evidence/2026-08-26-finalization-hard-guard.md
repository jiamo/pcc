# Task-board finalization hard guard

Date: 2026-08-26  
Task: `CTL-P0-FINALIZATION-HARD-GUARD`

## Failure

The agent twice sent a final response while `goal_state.py resume` explicitly
reported `CONTINUE`.  Selection and dependency routing were correct; AGENTS
already prohibited stopping.  The missing layer was a runner-consumable
nonzero pre-final gate.

## Implementation

`scripts/goal_state.py finish-check` validates the board, computes the same
resume state, hashes the exact task-board bytes, and optionally writes a JSON
receipt.

- `CONTINUE`: prints `finalization: DENIED`, the selected task and reason;
  returns exit 4.
- invalid board: retains validation exit 2.
- `BLOCKED`, `MILESTONE_COMPLETE`, or board-wide `COMPLETE`: prints
  `finalization: ALLOWED` and returns zero.

AGENTS and the canonical goal protocol now require this command before a final
response.  Loop/runner integrations can treat exit 4 as a mechanical refusal
and continue without relying on model compliance alone.  This repository gate
cannot itself modify the Codex product UI; product-level enforcement must call
the command or implement the same state check.

## Evidence

The live unfinished board selected this task and produced exit 4.  Receipt:
`build/goal-finalization-guard/denied.json`; it binds state `CONTINUE`, selected
task `CTL-P0-FINALIZATION-HARD-GUARD`, and board SHA-256
`cd70f9a3b7412dc8609c1f2c0601c3fddbd9c4f817764311f4649e0b0c0f8cc6`.

Focused gates:

```text
goal state + relevant startup/protocol nodes
  23 passed in 0.16s

goal_state.py validate
  421 tasks validated

render-startup --check
  green after regeneration
```

The full `tests/test_goal_startup_docs.py` remains red only at the independently
tracked pre-existing startup-size assertion (current generated state exceeds
20 KiB, owned by `GOAL-P0-STARTUP-STATE-BOUNDED-RENDER`).  This guard does not
delete task state to hide that failure.

## Status

`DONE_STRONG` at repository/runner scope.  The interrupted radix dependency
must resume immediately.
