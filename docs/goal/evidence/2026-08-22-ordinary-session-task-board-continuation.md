# Runner-neutral ordinary-session task-board continuation

Date: 2026-08-22

## Claim

Repository task-board execution no longer depends on a product-specific Goal
mode. An ordinary request such as `继续任务板` routes to:

```bash
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py resume
```

`resume` validates the authoritative board and emits one stable state:

- `CONTINUE`: prints the dependency-ready selected task and requires evidence,
  row update, validation, and another `resume` invocation after completion;
- `BLOCKED`: no executable task remains in the active milestone because rows
  are explicitly blocked or have unmet dependencies;
- `MILESTONE_COMPLETE`: the active milestone is complete but unfinished rows
  remain elsewhere, so this is not full completion; or
- `COMPLETE`: every task-board row is `DONE_STRONG`.

Explicitly `BLOCKED` rows are not selected as executable work. The legacy
`next` inspection command remains available, while startup and work-loop
documentation use `resume` as the runner-neutral entrypoint. The protocol
requires an ordinary session to cross task boundaries while the state remains
`CONTINUE`; explicit human stop/cancel/switch instructions still take
precedence.

The current GC4 recovery checkpoint was also made durable in its task row: the
C `class-span-order` exact node is recorded green, the interrupted strict node
is recorded unproven, and raw-copy-unlocked work is explicitly deferred until
the selected-source/page lifetime boundary is protected.

## Source identity

This is current shared-filesystem evidence, not a clean-commit or release
claim. The implementation/protocol inputs used by the focused gates were:

```text
edc1686d782dad49f0549ff0bc7205ab70270d988f39451793229b05ae0e1279  AGENTS.md
ad868d080fc8b72a17cd8826e5828b332f06c0bf4dd5bbe152922972e3540e60  docs/goal/goal-prompt.md
9b76ab664ada0a7e365767cd4ce119181731744f7b829fcd41ceca49a8529b29  scripts/goal_state.py
fed016825068769b3ad8f32f5285b31944ff68b803fde687785fa47123628195  tests/test_goal_state.py
125742966b21620577b1ea218be6103308e1c3ef381f2d9983aef3da9c711da3  tests/test_goal_startup_docs.py
```

## RED

The new state-machine test initially failed at the first new node because
`scripts.goal_state` had no `resume_state` attribute:

```text
1 failed, 8 passed in 0.16s
```

After implementation, the unscoped startup-doc file stopped at its known,
separately registered startup-size failure: the generated state exceeded the
existing 20 KiB assertion. That failure belongs to
`GOAL-P0-STARTUP-STATE-BOUNDED-RENDER`; it was neither weakened nor claimed
fixed here. This slice therefore uses exact startup-contract nodes rather than
misreporting the independently red size node as part of its green evidence.

## Focused gates

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/test_goal_state.py tests/test_goal_startup_docs.py::test_protocol_keeps_routed_claim_contracts tests/test_goal_startup_docs.py::test_repository_has_one_goal_protocol_entrypoint tests/test_goal_startup_docs.py::test_current_goal_state_matches_structured_sources tests/test_goal_startup_docs.py::test_ordinary_sessions_use_the_runner_neutral_resume_loop
................                                                         [100%]
16 passed in 0.12s

gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
OK: 383 tasks validated

gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py render-startup --check docs/current-goal-state.md
OK: docs/current-goal-state.md
```

A direct `resume` invocation returned `state: CONTINUE`, selected
`CTL-P0-ORDINARY-SESSION-CONTINUATION`, printed its complete execution fields,
and printed the required post-completion `resume` action.

After this row was promoted to `DONE_STRONG`, the same command returned
`state: CONTINUE` again and automatically selected
`GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`. Its first printed boundary is the
unproven strict `class-span-order` exact node, confirming that completion of one
row advances an ordinary session to the next authoritative checkpoint rather
than silently terminating or inventing a new design.

## Nonclaims

No compiler, runtime, GC implementation, strict archive, bootstrap, broad
pytest suite, or performance gate ran. This change does not make an AI process
immortal or keep an application session alive after external termination; it
makes recovery and cross-task continuation explicit and durable for any runner
that reads repository instructions and can invoke the script. The independent
startup-state size P0 remains open.
