# Oversized emit lane: width-2 admission waves implemented, 177.6s -> 119.4s

Date: 2026-08-27
Task: `PERF-P2-OVERSIZED-LANE-PAIRING`
Claim level: implemented as the DEFAULT oversized-lane schedule; unit-tested;
one snapshot chain (HEAD + the two scheduler files) with the fixed point
green and one cold profiled stage2 receipt. Single-GC0 chain; the five-GC
matrix ride-along is the open boundary. Snapshot artifacts:
scratchpad admsnap/build/bootstrap-admission-v1 (pcc1/pcc2/pcc3, profile/,
chain.log, stage2cold.log).

## Implementation

`pipeline_self_backend_emit.py`:
- `pack_admission_waves(command_bytes, byte_cap, width)` — first-fit-
  decreasing over per-command input-byte weights.
- `run_emit_worker_pool` weighs each command from its OWN batch contents
  (`_pack_batches` reorders by descending bytes, so the caller's item_bytes
  does NOT align with command order — using it directly would have paired
  the two giants), then runs the oversized lane as sequential waves of
  width <= 2 whose byte sums respect the cap.
- Default cap 7,000,000 input bytes, calibrated from the 2026-08-27
  per-item receipts (footprint ~1.3-1.4 GB per input MB; every admitted
  pair measured <= 7.2 GB). `PCC_SELF_BACKEND_OVERSIZED_BYTE_CAP` overrides
  (0 = serial as before); an explicit jobs env keeps the legacy unbounded
  override and disables admission rather than stacking both.
- Pools are sequential, so the wave peak is the machine peak.

`pipeline.py`: the injection wrapper forwards `admission_byte_cap`.

Unit gates (tests/python/test_self_backend_oversized_admission.py, 3
passed): the receipt byte-set packs into exactly the measured-safe schedule
(giant alone; no wave over cap; width <= 2; every command exactly once);
degenerate caps stay serial; the pool-level test drives run_emit_worker_pool
with a recording executor and re-derives each wave's bytes from the written
manifests, proving the reorder-safe weighing end to end; cap 0 reproduces
the single serial call.

## Chain + receipts (snapshot = git archive HEAD + the two files)

```text
stage1 239.3s  ->  pcc1 187,737,224 bytes
stage2 667.8s  ->  pcc2      (first pass, in-chain)
stage3 248.1s  ->  pcc3
verify: pcc2 and pcc3 BYTE-IDENTICAL — self-host gate passed (GC0)

cold profiled stage2 rerun (fresh object cache, quiet machine):
  total                                   664.2 s
  link_self_native_emit_oversized_workers 119.37 s   (batch77 serial: 177.64)
  link_self_native_emit_safe_workers      241.75 s   (batch77: 309.65)
  oversized pool processes                7 (all ran; no cache shortcut)
```

## Honest attribution

The lane counter is the claim: 177.64 -> 119.37 s (-58.3 s, -33%), matching
the wave-math prediction (46.2 + max-of-pairs = ~124.5 s from the per-item
walls; slightly better because batch77's per-item walls carried morning
machine load). The stage2 TOTAL delta (867.8 -> 664.2 cold) is NOT all
admission: the safe lane and frontend also shrank with zero code changes,
which is machine-load epoch — do not quote -203 s as the win. The cache-
warmth hypothesis for the first pass was tested and DENIED: the cold rerun
reproduced the wall within 4 s.

Stage anchors after this slice: stage1 239.3 s, stage2 664.2 s cold — under
the task's 686.160 anchor for the first time, still ~2.8x stage1; the
remaining Stage2 mass is worker compute (the Indexed Function Kernel lane)
plus the 241.8 s safe lane.

## Open boundary

- Five-GC matrix ride-along (this chain is GC0).
- Re-take the per-item receipts after the kernel lane lands (walls shrink;
  the cap stays valid because footprints track input bytes, not speed).
- Machine-peak is bounded by wave math + measured per-item footprints
  (<= 7.2 GB); no direct whole-machine RSS sampler ran.
