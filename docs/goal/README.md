# docs/goal — retired 2026-09-06

The structured task board (`task-board.yaml`, 490 rows) and its evidence
directory (`evidence/`, 924 files) were removed on 2026-09-06. They served a
single-file execution queue plus a per-slice evidence log; the queue is now
GitHub issues and the durable knowledge is distilled under `docs/knowledge/`.

- **Unfinished work** — the 241 rows that were not `DONE_STRONG` became issues
  with `priority:*`, `status:*` and `task-board` labels, split across the three
  repositories. The mapping is
  [`docs/task-board-migration-2026-09-06.md`](../task-board-migration-2026-09-06.md).
- **The last board and all evidence files** remain in git history at commit
  `2574f585`; nothing was rewritten.
- **The protocol documents** moved to
  [`docs/archive/goal/`](../archive/goal/): `goal-prompt-through-2026-09-06.md`
  and `current-goal-state-through-2026-09-06.md`.
- **What replaced them** — `docs/knowledge/` (generated decision pages plus
  handoffs), `docs/investigations/` (unchanged, the evidence those pages
  summarise), and issues for the queue. See the "Working agreement" section of
  [`AGENTS.md`](../../AGENTS.md).

What stays in this directory: `head-truth-manifest.json` and
`m1-package-canary.json`, which are consumed by `scripts/head_truth_gate.py`
and `scripts/head_truth_manifest.py` and are unrelated to the board.
