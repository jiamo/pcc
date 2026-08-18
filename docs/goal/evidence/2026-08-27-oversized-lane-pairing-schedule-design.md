# Oversized emit lane: measured per-item receipts and a memory-safe pairing schedule

Date: 2026-08-27
Task: `PERF-P2-OVERSIZED-LANE-PAIRING` (new, designed-not-implemented)
Claim level: per-item measurement receipts plus a schedule computed from them.
No implementation, no stage claim. batch77 pcc1 (`dd808447`), frozen no62
manifest, `pcc_emit_rank --lane oversized --jobs 1`, performance lock held.

## Receipts (fresh process per item, serial)

```text
module                                    input MB   wall s   peak footprint GB
call_expression_lowering                     5.1      46.17        6.87
method_call_expression_lowering              4.6      35.69        4.82
cli_bootstrap                                4.5      28.94        4.39
port_abi_exports                             2.2      13.22        2.38
attr_load_lowering                           2.2      13.40        2.36
type_infer                                   2.1      13.72        2.34
cli_bootstrap_array_core                     2.1      13.65        2.29
serial total                                         164.8
```

The lane currently runs width 1 (`oversized_emit_jobs = 1` unless
`PCC_SELF_BACKEND_JOBS` is set explicitly, `pipeline_self_backend_emit.py`),
and each item already gets a fresh process (the documented 12 GiB batching
lesson).

## The schedule the receipts admit under the 8 GB budget

* The giant must run ALONE: any partner breaks 8 GB (6.87 + 2.29 = 9.16).
* `method` and `cli_bootstrap` must never overlap each other (9.21 GB), but
  each pairs safely with any small (<= 7.2 GB).
* Two lanes after the giant: `method -> cli` sequentially in one lane
  (64.6 s), the four smalls in the other (54.0 s).

```text
wall  = 46.2 (giant solo) + max(64.6, 54.0) = 110.8 s   vs 164.8 s serial
saving ~54 s of Stage2 wall (~6% of the 892 s chain), peak <= 7.2 GB
```

One-knob admission rule that reproduces this schedule: run width 2 with a
concurrent-input-bytes-sum cap of ~7 MB (footprint tracks input bytes at
roughly 1.3-1.4 GB/MB on these seven items; the cap is calibrated from the
measured pairs, not the linear model, which over-predicts pair sums).

## Deliberately not implemented yet

`pipeline_self_backend_emit.py` sits next to the in-flight Indexed Function
Kernel restructure (the worktree currently has a circular import mid-edit).
Implementing now invites a clobber. Also: the kernel lane targets exactly the
planner that dominates these 46/35/29-second walls, so absolute savings shrink
proportionally when it lands — the schedule stays valid, the receipts must be
re-taken on the kernel compiler before quoting a number.
