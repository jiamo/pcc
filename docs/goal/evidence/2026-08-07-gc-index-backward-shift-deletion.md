# 2026-08-07 — GC index engine: backward-shift deletion (tombstone rehash storm removed); GC4 longrun 8.0x the 08-03 baseline

## Claim (mode-labeled)
Strict no-libpython/self-backend AArch64 Darwin, pinned
`pcc-gc-longrun-churn` workload (100k rounds, live set 2048, 6.4M ops per
backend). Engine change only (`src/py_gc_index_table.c` +
`py/freestanding_gc_index_table.py`, differential-equal): deletion is now
backward-shift (gap-free probe chains, no tombstone state, `used == live`,
steady-state churn performs zero rehashes).

## Measurements (this machine, current source)
- GC4 three identical runs: 330,595 / 321,333 / 319,233 ops/s → median
  **321,333** (08-03 baseline: 40,047 → 8.0x; post-hash-only median was
  294,320). Every run: 6,400,000 ops, zero steady-tail drift, peak RSS
  9,093,120 B (< 10MB threshold), zpage retained gap 508,840 B.
- GC0-3 same run family: 604,514 / 383,739 / 376,891 / 369,110 ops/s —
  no regressions (all improved or flat vs post-hash numbers).
- Profile basis (exit-criteria requirement): pre-change `sample` attributed
  the dominant GC4 time to `pcc_gc_index_py_find_slot` + rehash
  `calloc`/`memset` (tombstone-clearing rehash storm); recorded in
  docs/investigations/gc-frame-index-entry-pool-perf.md Update
  (2026-08-07, session 3).

## PERF-P1-GC4-FREESTANDING-LONGRUN threshold check
- median >= 240,000 ops/s: **met** (321,333)
- 6.4M ops, zero drift, RSS <= 10MB: **met** (every run)
- zpage retained gap <= 504,992 B: **NOT met** — deterministic 508,840 B
  (+3,848; exactly one extra 4KB zpage page + 248B live at the final
  sample), identical before/after both engine slices → predates the engine
  work; owner decision required on the byte-pinned axis (threshold not
  changed by the agent).

## Gates (all at this tree state)
- tests/python/test_freestanding_gc_index_table.py — 5 passed (includes
  C-oracle vs pcc-Python-port differential execution).
- tests/python/test_gc_backend_generational.py — 80 passed (design pin
  updated: gap-free backward-shift instead of tombstone delete).
- Freestanding GC battery — 197 passed; 7 failures shown pre-existing by
  HEAD-engine control rebuild (see investigations below).
- Behavior batteries: PCC_GC_BACKEND=4 and =3 over tests/python/test_gc_*.py;
  every failure reproduced identically with the HEAD engine (control) and
  is filed separately:
  gc4-trashcan-del-chain-dealloc-recursion-overflow.md,
  gc3-cycle-collect-undercount-10k-cycles.md.
- tests/python/gc/test_pcc_bootstrap_full_gc4.py re-run at this tree state
  (third pass of the day; required by the runtime change).

## Not claimed
- No five-backend bootstrap matrix in this slice.
- GC4 remains the slowest backend (321k vs gc3 369k); further narrowing is
  future profile-guided work, not this slice.
- The pre-existing GC4 trashcan overflow and GC3 collect undercount are NOT
  fixed here (separate investigations + board rows).
