# Gateway buffer/backpressure — focused evidence

Date: 2026-08-14

Task: `GATEWAY-P2-BUFFER-BACKPRESSURE`

The fail-fast non-integration gate for `tests/python/test_gateway_buffers.py`
completed with `6 passed, 1 deselected`. It covers retained buffer views,
bounded byte accounting, high/low watermark transitions and close/release
behavior on the host/current-source path.

The source-current pcc1 self/no-libpython slow-peer gate and GC0..4 relocation
proof remain unrun, so this is `DONE_WEAK` evidence only.
