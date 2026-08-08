# Gateway HTTP/1 codec — focused evidence

Date: 2026-08-14

Task: `GATEWAY-P2-HTTP1-CODEC`

The fail-fast non-integration codec plus local-streaming gate completed with
`46 passed, 1 deselected`. The focused set covers incremental framing,
fragmentation, bounds/security rejection, keep-alive lifecycle and the local
body-stream handoff.

The source-current pcc1 self/no-libpython origin/security corpus and GC0..4
streaming ownership proof remain open, so this task is `DONE_WEAK`.
