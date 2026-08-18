# RUNTIME-P1 Step 2: decisive capped-Stage2 measurement — trim does NOT reduce the live peak

## The run (fresh pcc1 built WITH the new allocator)

- Rebuilt the in-repo runtime archive `libpy_runtime_pcc_py.a` with the new
  allocator (provenance stale members 0; exports `pcc_allocator_trim`,
  `_granule_retire_slab_locked`, `pcc_allocator_reclaimable_slab_bytes`).
- Froze source snapshot v13 (= v12 + the Step 1/2 allocator) and built a fresh
  capped Stage1 from it: rc 0, wall 181.9s, peak tree 4.82 GiB < 8 GiB,
  libpython False, pcc1 sha 45c21502. (The old inline-edge-stage1-capped-v2
  pcc1 predated the allocator, so a fresh Stage1 was mandatory — measuring the
  old pcc1 would have proven nothing.)
- Ran the capped Stage2 from that receipt (`run_pcc_stage2_from_receipt.py
  --max-tree-rss-bytes 8589934592 --stage2-timeout 600 --self-backend-jobs 2`).

## Result: still MEMORY_LIMIT; the worker peak did not move

Big pcc1 codegen worker (the plan-state coordinator, one per stage):

```text
                 peak RSS   at     curve shape         >256MB drops
NEW (trim)       6.55 GiB   152s   MONOTONIC rise      0
OLD (control)    6.58 GiB   142s   MONOTONIC rise      0
peak tree        8.24 GiB (new)  vs  8.65 GiB (old)    cap 8 GiB -> MEMORY_LIMIT both
```

The worker RSS climbs steadily to ~6.5 GiB and never falls (0 reclaim drops in
either arm).  The 30 MiB difference in worker peak and the tree-peak drop
(8.65 -> 8.24 GiB) are within run-to-run noise / concurrent-worker phase
overlap; neither is a trim effect on the single-worker owner.

## Verdict: the reclaimability assumption is DISPROVEN for this workload

Step 2's trim reclaims only WHOLE fully-free kind-2 slabs.  During the worker's
monotonic growth there are no fully-free slabs: the ~6.5 GiB is LIVE working
set, not freed-but-retained slabs.  This matches HARNESS-P0's confirmed owner:
the peak allocators are the **direct-indexed-kernel arenas + the assembler
section/relocation graph**, held simultaneously through codegen+assembly (pcc1
codegen ~7 GB WITH frontend-release, 24.65 GB WITHOUT).  A quiescent-point
whole-slab trim cannot lower a single monotonic live peak.

This is the Evidence-Discipline outcome the Step 2 evidence explicitly flagged
as unproven ("a genuinely live peak working set is a different owner").
Measured: it is a live peak.  The real lever is UPSTREAM — release intermediate
codegen state before the assembler graph is built (the frontend-release lever
already cut 24.65 -> 7 GB; more of that is the path to <=8 GiB).  That is
HARNESS-P0-STAGE2-MEMORY-SAFE-DEFAULT / pcc1-worker-object-protocol-tax work
(codegen-state lifetime), NOT allocator slab reclamation.

## What Step 2 is still worth

The trim is a correct, validated allocator improvement in its own right:
it returns idle whole slabs to the OS and makes the retained footprint
self-limiting across allocation phases (obligation 5, long-running runtime
efficiency).  It does NOT regress this workload (6.55 vs 6.58 GiB, wall 152 vs
142s, both within noise) and its correctness is proven host-side (evidence 002:
allocator two-phase e2e + threaded churn + ABI matrix + the full ARCH-P0 S1
granule/provenance/layout 13-test gate under GC0..4).  It is kept as a general
improvement, not claimed as the Stage2-fit solution.

## Cross-confirmation: the active plane investigation independently DENIED every bounded lever (2026-09-03)

`docs/investigations/pcc1-frontend-direct-indexed-kernel-plane.md` (the active
IMMEDIATE native-data-plane owner) has measured-DENIED the bounded memory levers,
independently confirming this row's Step 2 verdict:

- **Streamed per-module AST/capture release [DENIED]**: releasing each module's
  frontend/AST state per codegen-loop iteration changed neither the batch peak
  nor its ratio (2.687 -> 2.664 GB, 2.02x). Verbatim: "The retained high water
  is the allocator and other compiler state accumulated across modules, not
  simultaneous AST ownership ... process exit is still the only proven reclaim
  boundary." So the HARNESS-P0 "release capture per-module" BOUNDED lever is
  already refuted.
- **InstructionRecord slots + lazy-metadata [DENIED]**: host footprint
  885.98 -> 828.65 MB (-6.47%) did NOT transfer; source-frozen pcc1 peak
  footprint 6.491 -> 6.312 GB (0.9724, below the >=0.95x bar) with wall/CPU/
  instructions slightly worse.
- **Native worker process reuse [DENIED]**: reusing one process across five
  modules cost 2.03x peak RSS vs five singleton processes; singleton
  process-exit is the memory-optimal boundary.

Deeper mechanism (consistent with this row's monotonic, 0-reclaim-drop
measurement): the coordinator's ~6.5 GiB is allocator FRAGMENTATION plus
accumulated long-lived compiler state. Freed cells are scattered across
partially-used slabs, so no whole slab is ever fully free (this row's trim has
nothing to reclaim), and a non-moving raw allocator cannot compact them.
"Process exit is the only proven reclaim boundary." Reducing it further is an
open architectural problem (defragmentation would need a moving allocator, or a
fundamental reduction in accumulated coordinator state) that the active plane
investigation is grinding through with measured denials. There is no bounded
fix available; this row's allocator trim is kept as a general improvement, and
the Stage2 memory fit is not closable from the allocator or any adjacent
bounded lever.
