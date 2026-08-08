# Gateway reverse proxy — focused evidence

Date: 2026-08-14

Task: `GATEWAY-P2-REVERSE-PROXY`

The fail-fast non-integration reverse-proxy gate completed with `40 passed,
1 deselected`. It exposed and fixed two constructor-contract defects: an idle
pool limit may exceed `max_active` because idle connections retain active
leases, and default high/low watermarks are now derived from a smaller explicit
`max_buffered_bytes` instead of making otherwise-valid small buffers fail.

The source-current pcc1 self/no-libpython live streaming proxy and GC0..4
slow-peer/cancellation gates remain open, so this task is `DONE_WEAK`.
