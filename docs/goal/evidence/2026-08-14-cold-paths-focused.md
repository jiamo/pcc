# PERF-P2-COLD-PATHS focused evidence — 2026-08-14

Mode: host pcc source, AArch64 Darwin self-backend assembly emission; no
bootstrap artifact and no runtime performance claim.

The focused regression initially showed that block placement moved `success`
before `error` but still emitted `b.eq success; b error`.  The fold proof was
being discarded by precise-stackmap labels inside the same IR block.  Stackmap
anchors now preserve the containing block's canonical-edge proof; ordinary
local labels still clear it.

Command:

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/c/test_self_backend_aarch64_cold_paths.py
```

Result: `2 passed in 0.13s`.  The canonical post-call error check now branches
only to the cold error block and falls through to success; the noncanonical
intervening-work control retains source block order.

Open: stage2 wall-time/RSS comparison and the bootstrap baseline gate.
