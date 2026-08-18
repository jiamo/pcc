# 011 — current-source Stage2 memory-safety closure

Date: 2026-09-04

## Claim

The finite harness claim is closed: a default compiled Stage2 can no longer
recreate the incident's unbounded ten/eight-worker fan-out, every compiled
stage is owned by an external process-tree guard, admission decisions and the
largest worker are durable, and one current frozen source completed Stage2
below the 8 GiB cap with a runnable self/no-libpython pcc2.

This is not a speed claim.  The completed Stage2 took about 1350 seconds and
therefore fails the separate same-resource `Stage2 <= Stage1` performance
contract.

## Evidence readback

- Evidence 010 proves compiled export/codegen width <=2 for the risky lanes,
  oversized serialization, ordinary-bootstrap external guarding, full argv /
  manifest ownership, and focused scheduler/owner gates.
- `PERF-P0-STAGE-RESOURCE-ENVELOPE-PARITY/004` records frozen source v18:
  Stage1 164.39s / 5.01 GB and Stage2 COMPLETE in about 1350s / peak 7.28 GiB
  under the same 8 GiB cap.
- The produced pcc2 is 210,782,744 bytes, links only libSystem, passes
  `--help`, compiles the function-bearing canary, and that canary prints 42.
- The post-success `stage2-record.json` omission was a harness serialization
  bug after compilation; its missing Namespace fields were fixed with a
  focused test.  Process-tree, bootstrap terminal line, admission, link and
  manual executable receipts independently establish the finite safety claim.

## Ownership transfer

The 8.2x wall failure is retained, not normalized away.  Per-worker runtime
protocol cost proceeds through `PERF-P0-PCC1-WORKER-OBJECT-PROTOCOL-TAX`, then
the frozen-data-plane/emit work and finally
`PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST`.  A longer diagnostic watchdog did not
raise the memory cap or weaken process-group cleanup.

