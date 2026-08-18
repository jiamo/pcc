# 004 — hoist walk metadata skip (−48.9% worker) and the refreshed Stage2 prediction

Date: 2026-08-31. Continues evidence 002/003 and
`docs/investigations/pcc1-worker-object-protocol-tax.md`.

## The change

`pcc/py_frontend/codegen/hoist_free_names.py` `compute_free_names`:

- The generic reflection loops (walk's fallback, the ident-less call branch,
  `_collect_target_names`) now skip the `span` and `ty` fields. They are
  metadata records (SourceSpan; structural types) that can never contain a
  `Name`, yet their traversal dragged ~2 dataclass instances plus ~7 scalar
  fields through the full dynamic per-node gauntlet for EVERY semantic node —
  a ~3x hidden multiplier on the visited-object count.
- `walk` gains a fast scalar bail (None / str / int / float / bool / bytes)
  at the top, so the metadata scalars that still arrive exit before the
  isinstance/call-shape chain.

Free-name semantics are unchanged by construction: free names are `Name`
idents, and span/ty subtrees cannot contain one.

## Verification

- Hoist behavioral + shape gates: 15 passed.
- Stage1 v15 (snapshot with the change) COMPLETE: wall 149.15s, user 750.14s,
  peak tree 4.6 GiB, smoke `42`, libSystem-only pcc1.
- class_gen worker canary, adjacent alternating v14/v15 pairs (user CPU):
  21.66/21.68/21.72 vs **11.09/11.12/11.07** — candidate wins 3/3,
  **−48.9%**, tight variance, and `module_87.ll` is byte-identical to the
  v13 baseline.
- Commit gates after the change: bootstrap-gate baseline 2 passed;
  test_py_multi_file_compile 46 passed.

## Refreshed Stage2 prediction (still no Stage2 run needed)

Six additional single-module replays with v15 pcc1 across the size spectrum
(type_infer 231KB 9.67s, pipeline 150KB 5.11s, py_parse 88KB 6.49s,
arm64_encode 38KB 3.65s, darwin_ops 16KB 2.31s, self_backend_prepare 2.7KB
1.08s) plus class_gen (271KB 11.09s) fit:

```
per-module cost ≈ 1.85s + 32.9s/MB      (v9-era model was ~3.4x this slope)
sum over all 224 modules ≈ 662s core
lane wall at jobs=2 ≈ 331s
whole Stage2 ≈ 331 + 230 (measured checkpoint+link reserve) ≈ 561s ≤ 600s
```

The 600s/8GiB contract is now predicted feasible for the first time (margin
~7%; fit scatter ±1.7s/module). The retained v5 plan state predates the
worker-cost change, so `--prediction-state` against it would refuse — the
single authorized capped Stage2 run therefore cites THIS fit as its
prediction and omits the stale-state flag.

## Cumulative worker trajectory (class_gen canary, user CPU)

```
v11/v12 (baseline)          23.3–24.9s
+ exception-free probe      22.5–22.7s   (−3.1%, CONFIRMED)
+ retirement fan-out gate   21.6–22.0s   (−1.0%, noise; retained neutral)
+ span/ty metadata skip     11.07–11.12s (−48.9%, CONFIRMED)
total                       ≈ 2.1x
```
