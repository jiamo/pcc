# Oversized-lane attribution corrected; XOR location fingerprint accepted

Date: 2026-08-27
Task: `PERF-P0-STAGE2-COLD-CACHE-REGRESSION`
Claim level: one accepted emit-local optimization (three-pair receipt below)
plus a retraction of this row's own prior attribution. **The complete-stage
transfer is running and unclaimed; no new Stage2 baseline is declared here.**

## Retraction first: "the cost of correctness" was wrong

Update No.73 attributed the oversized lane's 61.729 s -> 185.412 s to the
restored reload work. Measured, that is false twice over:

```text
same frozen item 311, same machine, back to back
pcc1 31d6ac3b (reloads OFF)        81.12 s   529,592 asm lines
pcc1 6615a64f (reloads ON, fixed)  55.50 s   550,296 asm lines (+3.9%)
```

The correct compiler is **1.46x faster per item** while emitting more
assembly, and reload planning is **0.2%** of the pcc1 emit worker (0.02 s on
host). The 892.439 s vs 686.160 s gap is therefore **unattributed** — and the
Stage2 profile's own numbers (7 oversized in 61.7 s) contradict the measured
81.1 s for one of them, proving the frozen no62 items are a valid A/B input
but not a reconstruction of either run's lane. Retracted in Update No.74.

## The real hot spot, from a caller-attributed pcc1 profile

Item 311 under the correct pcc1, 18,478 samples:

```text
build_function_stack_map_plan            85.4%
  _py_dict_get (tail-called subscript)   27.5%   <- largest single item
  _block_entry_states                    11.7%
  __nested_add_record                     9.2%
  _managed_live_after                     9.1%
  _managed_value_origins                  4.4%
  _planned_managed_reloads                0.2%
```

The 27.5% is the `interned_locations` memo key: a sorted tuple of group ids,
rebuilt per version change — list + sort + tuple + element-wise tuple hash
(visible as `_mul_u32_low`) + dict growth (`calloc`/`malloc` under the probe).
The memo's key cost more than the `_locations` merge it avoids.

## Update No.75 — XOR fingerprint, accepted on the pre-registered line

XOR of the group ids replaces the sorted tuple: order-independent,
self-inverse, one tagged-lane integer, zero allocation. XOR admits collisions,
so the entry's stored `tuple(active.values())` — already kept for the
id()-liveness rule — is verified by identity on every hit; a collision is a
slow path, never a wrong answer.

Semantic checks: closure rc=0; host item-343 emit byte-identical to the
pre-change reference; candidate Stage1 clean (345.693 s, pcc1 `e9762f9e`).

Three alternating pairs, frozen item 311, `/usr/bin/time -lp` parenting pcc1
directly (a first attempt put `gtimeout` between them, attributing the
per-process counters to gtimeout — footprint read ~1 MB against pcc1's real
8.7 GB; discarded and re-run with the order fixed):

```text
pair   wall B/C            user B/C            ins C/B   footprint C/B
1      55.72/53.97 1.032   52.36/49.13 1.066   0.9099    0.8020
2      54.20/46.58 1.164   51.33/44.61 1.151   0.9105    0.8035
3      55.20/52.23 1.057   51.65/48.19 1.072   0.9104    0.8025

median wall 1.057   median CPU 1.072   ins 0.910   footprint 0.803
assembly: all six runs byte-identical (ff943e10afe802c4…)
```

All four pre-registered gates pass. Honest weighting: the host carried heavy
unrelated load (load average 13+), so wall clearing 1.05 by 0.007 is the
weakest number; the load-independent instruction count (**-9.0%**, stable to
0.06% across pairs) and CPU (+7.2%) carry the acceptance. Unpredicted bonus:
peak footprint fell **8.68 GB -> 6.96 GB (-20%)** — millions of fingerprint
tuples and their managed-pointer index entries no longer exist.

Focused gates: 52 passed, 2 deselected.

## Open

The acceptance buys one empty-cache Stage2 + Stage3, now running under
`gtimeout 3000s` from a verified-empty cache (`build/bootstrap-no75-v1`,
pcc1 `e9762f9e`). Its wall time will be a correctness receipt on this loaded
host, not a baseline. The five-GC matrix remains required before any
DONE_STRONG that touches this path.

## Acceptance chain result (appended after completion)

```text
Stage2  977.866 s  rc=0        Stage3  293.291 s  rc=0
pcc2 == pcc3 byte-identical    stale-managed rejections: 0
cold cache (verified empty before launch), pcc1 e9762f9e
```

Chain-to-chain wall deltas against the setprobe chain (+9.6% Stage2, -31.6%
Stage3) are load noise on this host and are **not** attributed to No.75 in
either direction. The controlled three-pair A/B (instructions 0.910x,
byte-identical assembly) remains the only performance claim. These walls are
correctness receipts.
