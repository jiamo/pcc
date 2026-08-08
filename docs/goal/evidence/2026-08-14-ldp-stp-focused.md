# PERF-P2-LDP-STP focused evidence — 2026-08-14

Mode: host pcc source, AArch64 Darwin self-backend assembly emission; no
bootstrap or measured performance claim.

Command:

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/c/test_self_backend_target_passes.py -k 'pairs_adjacent_64bit or rejects_aliasing_and_unencodable_pairs or atomic_markers_and_exclusive_region or pairs_normal_aggregate_memory'
```

Result: `4 passed, 23 deselected in 0.14s`.  Adjacent x-register loads/stores
pair, while aliasing, reverse order, range, width, volatile, atomic, fence and
exclusive-monitor controls remain scalar.

Open: source-current runtime parity, stage2 wall-time/RSS comparison, and
bootstrap baseline.
