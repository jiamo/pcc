# M0 publication-card removal and exit evidence

Date: 2026-07-11

Milestone: `M0`

Task: `M0-EXIT`

## Source identity

- Local source commit before this task-board edit: `58c595ac0bea18c2f74af52581d259f29aac5d6d`.
- The task-board edit leaves the working tree dirty and makes no clean-commit or
  published-CI claim.

## Changed behavior

- Removed `M0-CI-WORKFLOW-CONTRACT` and `M0-GITHUB-STATUS-CHECKS` from the
  executable task board at the user's explicit direction. Their remaining
  boundaries required publishing a commit and observing external GitHub CI;
  they no longer gate local goal execution.
- Removed the deleted task dependencies from the M0 startup and exit cards.
- Closed `M0-EXIT` and advanced the active milestone to `M1` after all
  remaining M0 dependencies were already `DONE_STRONG`.

## Gates

```text
gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate
OK: 74 tasks validated

gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py next
M0-EXIT selected before milestone transition
```

The post-transition validation and selection are recorded after this evidence
file and task row are installed.

## Supported claim

The executable queue no longer contains the two external-publication-only CI
cards, and M0's remaining dependency set is complete.

## Not proven

This does not claim that the removed workflows passed on GitHub, that local
commit `58c595ac` is published, or that a clean uploaded manifest has
`claimable_commit=true`.
