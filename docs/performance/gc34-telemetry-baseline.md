# GC3/GC4 Performance Telemetry Baseline

Date: 2026-07-02
Track row: `G-P0-GCPERF`
Mode: strict no-libpython self backend, reported by
`benchmarks/run_gc_advantage_matrix.py` and guarded by
`tests/python/test_gc34_perf_telemetry_contract.py`.

This slice is telemetry only. It does not change collector semantics, does not
weaken finalizers, weakrefs, barriers, roots, or owned-local cleanup, and does
not claim `G-P0-GCPERF` completion or collector performance equivalence.

## Stable Fields

The matrix JSON now preserves `work_steps`, which the benchmark binary already
emitted. This makes GC #3 collector bookkeeping visible beside the existing
pause, RSS, heap-capacity, relocation, and GC #4 zpage fields.

The focused contract pins these per-row labels and metrics:

- labels: `case`, `target_gc`, `target_metric`, `backend`, `mode`, `claim`
- common counters: `elapsed_us`, `pause_count`, `pause_sum_us`,
  `max_pause_us`, `work_steps`, `rss_bytes`, `heap_bytes`,
  `heap_capacity_bytes`
- relocation counters: `reloc_forwards`, `reloc_barriers`,
  `evacuated_bytes`
- GC #4 pressure counters: `zpage_count`, `zpage_capacity_bytes`,
  `zpage_used_bytes`, `zpage_allocated_bytes`,
  `zpage_reclaimable_gap_bytes`, `zpage_span_bytes`, `zpage_free_pages`,
  `zpage_free_capacity_bytes`, `zpage_free_span_bytes`

Derived non-ranking pressure fields for future reports are:

- `heap_pressure_bytes = heap_capacity_bytes - heap_bytes`
- `zpage_retained_gap_bytes = zpage_span_bytes - zpage_used_bytes`

## Gate

```bash
env -u LC_ALL uv run pytest tests/python/test_gc34_perf_telemetry_contract.py -q -n0
```

For the broader existing matrix smoke:

```bash
env -u LC_ALL uv run pytest tests/python/test_gc_advantage_matrix.py -q -n0
```
