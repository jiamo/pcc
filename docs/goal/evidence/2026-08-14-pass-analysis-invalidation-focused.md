# Pass analysis invalidation focused evidence — 2026-08-14

Mode: host-side IR pass unit/parity contracts; no runtime or bootstrap.

- `tests/c/test_ir_passes_manager.py` — 21 passed.
- `tests/c/test_ir_passes_newgvn.py` — 36 passed.

The focused tests exercise scoped analysis keys, preserved results, explicit
and conservative unknown invalidation, deleted units, query/hit/miss/recompute
counters and NewGVN's dominator consumer.

The task remains weak: no pinned stage2 analysis telemetry or performance
benefit has been recorded, and frontend semantic/fallback plus sequential
fixed-point gates remain pending after source freeze.
