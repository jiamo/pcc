# Investigation: process-tree guard swap-pressure false positive on high-RAM / small-swap hosts

## Status

fixed

## Problem

`scripts/run_process_tree_sample.py`'s Darwin preflight refused every guarded
stage build for a whole session with "swap is already pressured; refusing
guarded process tree", even though the host has 96 GiB physical RAM (about
52 GiB reclaimable at the time) and the capped tree peaks at ~4.7 GiB.

## Cause

The refusal fired on `swap_used*2 > swap_total and swap_free < 4 GiB`.  On a
96 GiB-RAM host macOS keeps only a small (4 GiB) dynamic swap file, which
Docker/VM background use keeps ">half used" indefinitely; macOS does not drain
it after the culprit exits.  So the tiny-swap ratio always looks "pressured"
even though the machine has tens of GiB of reclaimable physical memory and the
capped tree cannot thrash.  A 50-minute swap-watch confirmed the state never
clears on its own.

## Fix [CONFIRMED]

Waive the swap-pressure refusal when reclaimable physical memory clears
`_SWAP_PRESSURE_RECLAIMABLE_MARGIN` (2x) of the required budget
(`max_tree_rss + reserve`); the hard `reclaimable < required` floor still fails
closed on a genuinely starved host, and the swap refusal still applies when
reclaimable is low.  The preflight receipt records
`swap_pressure_waived_by_reclaimable`.  Tests:
`tests/python/test_process_tree_sample_tool.py::{test_swap_pressure_waived_when_reclaimable_ram_is_ample,
test_swap_pressure_still_refuses_when_reclaimable_is_low,
test_reclaimable_hard_floor_still_fails_closed}` (3 passed).  On the real host
the preflight now reports reclaimable 52.3 GiB vs required 16.0 GiB,
waived=True.
