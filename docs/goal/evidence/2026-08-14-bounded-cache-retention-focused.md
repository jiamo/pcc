# Bounded compiler-cache retention focused evidence — 2026-08-14

Mode: synthetic cache roots, policy/concurrency/CLI and mocked bootstrap-shim
object-cache contracts.

The retention plus frontend-IR cache files completed with 29 passed. The
object-cache/cache-retention selection in the bootstrap-shim file completed
with 2 passed and 105 unrelated cases deselected. No repository or user
historical cache was pruned by these fixtures.

Cold publication, warm frontend/object hits, bounded on-disk measurement and
sequential pcc1 -> pcc2 -> pcc3 equality remain open.
