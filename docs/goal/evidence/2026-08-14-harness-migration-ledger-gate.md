# HARNESS-P0-MIGRATION-LEDGER-GATE

Mode: Python-only repository governance over the dirty PCC worktree and pinned
DeepSeek Harness upstream baseline.

The Harness migration ledger now has a strict schema, ordered numeric entries,
one committed PCC change or one pending dirty-worktree tail, exact upstream
ranges or native-only rationale, changed-domain and task references, structured
verification results, GUI impact evidence, and explicit remaining boundaries.
Repository guidance and the entry template require the same fields for future
non-mechanical Harness migration changes.

Validation rejects malformed metadata, duplicate or noncontiguous entries,
unknown task ids, stale upstream pins, invalid verification rows, missing GUI
evidence, unbound Harness implementation commits, and a dirty implementation
without one pending tail. It reads only Git metadata, the task board, the
upstream pin, and ledger Markdown; it does not inspect environment variables,
credential providers, or secret files.

Evidence:

- `tests/python/test_harness_migration_ledger.py`: 5 passed in 0.65s.
- `projects/harness/migration/validate_ledger.py`: reported `OK: Harness migration ledger is complete and current`.
- `scripts/goal_state.py validate`: the Harness rows parsed, but the repository-wide
  gate stopped on the pre-existing unrelated row
  `PY-P1-FRONTEND-TYPE-TAG-AUTOGEN`, whose referenced evidence file is absent.
- Entry `0002-assembled-runtime-current-pcc1.md` records the current pending
  implementation slice, exact upstream range, current-source pcc1 identity,
  67 Harness source tests, four PCC regressions, three native integration
  tests, runtime/GUI/CLI checks, and dependency scans.

The ledger implementation and focused gates are complete. The task remains
open until the repository-wide task board is valid. Periodic fetching and
automatic creation of bounded follow-up tasks remain owned by
`HARNESS-P1-UPSTREAM-WATCH-AUTOMATION`.
