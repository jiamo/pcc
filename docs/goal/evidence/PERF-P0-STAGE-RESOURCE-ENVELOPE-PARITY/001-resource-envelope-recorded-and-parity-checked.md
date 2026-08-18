# 001 — same-resource-envelope recording and Stage1/Stage2 parity check

Date: 2026-09-02

## Claim boundary

Harness/resource-parity code plus focused tests.  This does NOT run a paired
Stage1/Stage2 receipt (that gate is blocked, see below) and makes no
Stage2/Stage1 ratio.

## What the harness already did

`scripts/run_pcc_stage_ab.py` already launches BOTH the Stage1 arm
(`_run_stage1`) and the Stage2 arm (`_run_stage2`) through
`run_process_tree_sample.py` with the same `--max-tree-rss-bytes`
(`args.max_tree_rss_bytes`, default 8 GiB) and the same
`--interval`/`--no-performance-lock`.  So within this harness both stages
already share one external cap and one process-group watchdog.  The asymmetry
the human flagged (149.15 s host Stage1 resolved against ~48 GiB vs ~1022 s
Stage2 under 8 GiB) came from a Stage1 built OUTSIDE this harness, not from the
harness mixing caps.

## Code change

Each stage record now carries an explicit `resource_envelope`:

```text
parity keys (must match across the two stages of an arm):
  max_tree_rss_bytes, cpu_count, frontend_jobs, self_backend_jobs,
  gc_backend, cache_policy {PCC_PY_FRONTEND_IR_CACHE,
                            PCC_SELF_BACKEND_OBJECT_CACHE, private_pycache}
observations (recorded, NOT required equal — the scheduler may admit fewer
  workers when live RSS is high):
  observed_peak_tree_rss_bytes, observed_peak_process_count,
  sampler_status, sampler_returncode, termination_reason
```

`assert_stage_envelope_parity(arm_record)` runs right after both stages of an
arm are built and fails closed (`StageABError`) if any parity key drifts,
naming the drifted keys — so a Stage2/Stage1 ratio can never mix a capped
Stage2 with a differently-capped Stage1.  A stage1-only arm is exempt (no
ratio to protect).  Peak RSS / admitted worker count differences inside the
same cap do NOT fail parity, matching the row's "schedulers remain free to
admit different worker counts from live RSS".

## Focused evidence (`-x -n0`)

```text
pytest tests/python/test_stage_ab_resource_envelope.py
8 passed in 0.08s
```

Covers: envelope records the cap and observations; same envelope passes with
different observed RSS/worker count; a different cap / frontend_jobs /
gc_backend / cache policy is refused; a missing stage2 envelope fails closed;
a stage1-only arm needs no stage2 envelope.

## Blocked gates (recorded honestly)

- **One source-frozen Stage1 receipt under the common 8 GiB cap**: blocked
  (CONFIRMED PERSISTENT). A bounded 50-minute swap-watch polled every minute
  and the guarded launch never became allowed: `vm.swapusage` stayed pressured
  (used 2.9/4.1 GB, free ~1.2 GB < 4 GiB) for the full window because Docker/VM
  processes hold the swap and macOS does not drain it. The guarded capped
  Stage1 therefore did not run; it needs the user to free memory.
  `run_process_tree_sample.py`'s Darwin preflight refuses to launch a guarded
  tree while swap is pressured (`swap_used*2 > swap_total and swap_free < 4
  GiB`); `vm.swapusage` has been 3.0/4.1 GB used, ~1 GB free for the whole
  session (macOS does not drain swap after the culprit process exits, and
  Docker was restarted).  The unguarded direct build I used for the pcc1 A/B
  bypasses the cap and therefore cannot be the same-envelope Stage1 receipt.
- **Paired Stage1/Stage2 under the same cap**: additionally gated by the row
  itself — "do not run the paired Stage2 until its current-source prediction
  fits the existing 600-second contract"; the current prediction is ~663 s
  (parent row `HARNESS-P0-STAGE2-MEMORY-SAFE-DEFAULT`), so the pairing is not
  authorized yet regardless of swap.

## Open boundary

The envelope recording + parity guard is landed and tested.  The two real
receipts (Stage1 under the 8 GiB cap; the same-cap paired Stage1/Stage2 ratio)
wait on (a) swap pressure clearing so the guarded launch is allowed, and (b)
the Stage2 prediction reaching <=600 s under the parent row.


## Update — guard false positive fixed; Stage1 receipt + measured capped Stage2

The all-session block was a preflight FALSE POSITIVE, not real pressure: the
host has 96 GiB RAM (about 52 GiB reclaimable) with a tiny 4 GiB dynamic swap
that Docker/VM use keeps ">half used", so `run_process_tree_sample.py` refused
"swap is already pressured" although the capped tree peaks at 4.7 GiB.  Fixed
in both `run_process_tree_sample.py` and `run_pcc_stage2_from_receipt.py`:
waive the swap refusal when reclaimable physical memory clears 2x the required
budget; the hard reclaimable floor and the low-reclaimable refusal are
unchanged (investigation `process-tree-guard-swap-false-positive-highram.md`;
3 focused preflight tests).

Same-envelope receipts under the 8 GiB guarded cap (source v12):

```text
Stage1 (run_process_tree_sample + run_pcc_stage1_build): rc 0, wall 157.57s,
  tree CPU 816s, peak tree RSS 4.67 GB, cap 8 GB, libSystem-only, sha 7729ed80.
Stage2 (run_pcc_stage2_from_receipt, --max-tree-rss-bytes 8589934592
  --stage2-timeout 600): MEMORY_LIMIT at ~543s.  peak tree RSS 9.28 GB > 8 GB
  cap; killed during multi_frontend_codegen_parallel.  Largest single worker
  ~7.0 GB (pid 7143); at kill 4 concurrent codegen pcc1 workers summed ~8.6 GB
  (3.01 + 2.86 + 1.40 + 1.35 GB).  Profile phase totals: export_parallel 85.3s,
  codegen_parallel 86.2s, compile_python_multi_total 131.8s (incomplete).
```

Measured verdict (replaces the ~663s PREDICTION): the current-source Stage2
does NOT fit 8 GiB — the owner is per-worker codegen RSS (a single class_gen-
sized worker ~7 GB) and 4 concurrent workers crossing the cap, not wall time
alone.  This is the parent row's single-worker-owner signal.  The
inline-error-edge block reduction (-17%) does not help here: Stage2 memory is
per-worker instruction/arena-bound, consistent with the pcc1 A/B showing flat
wall.  The paired same-cap Stage2/Stage1 ratio therefore stays unproduced
until the per-worker Stage2 memory is brought under the cap (parent row
HARNESS-P0-STAGE2-MEMORY-SAFE-DEFAULT).


## Correction from the sample timeline: the owner is the EXPORT lane, not codegen

The 0.25s samples locate the memory peak precisely:

```text
t(s)  procs  tree_rss_GB    phase
 30    4      3.20          multi_frontend_export_parallel
 60    5      6.46          export
 90    8      6.25          export  <-- 8 concurrent workers
120    8      6.67          export  <-- sub-sample spike to 9.28 GB => MEMORY_LIMIT
150    3      1.27          (export drains)
...    3-5    <4            multi_frontend_codegen_parallel (never crosses cap)
```

So the tree crossed 8 GiB during `multi_frontend_export_parallel` running EIGHT
concurrent workers, not during codegen (codegen stayed under ~4 GB with 3-5
workers).  The Stage2 env sets `PCC_PY_FRONTEND_JOBS=auto`, whose comment
promises "serialize the high-risk source/AST family before admitting the
at-most-two-worker safe lane" — but that memory-safe admission constrains the
CODEGEN lane, not the EXPORT lane, which ran at width 8.  This is exactly the
parent row's "export lane" concern, now measured: the export lane does not
respect the 8 GiB envelope.  The concrete fix for
HARNESS-P0-STAGE2-MEMORY-SAFE-DEFAULT is to put the export lane under the same
live-RSS admission as codegen (or a fixed small width), not to widen the cap.


## Accurate owner from the profile COUNTERS (correcting the process-count read)

The 8 "processes" in the 0.25s samples are the whole tree, not 8 concurrent
workers.  The Stage2 profile counters are authoritative:

```text
multi_frontend_worker_concurrency        2   <-- export lane ran at 2, correctly
multi_frontend_export_safe_jobs          2
multi_frontend_export_oversized_chunks   5   <-- 5 big modules run SERIAL (max_parallel=1)
multi_frontend_worker_requested_concurrency 10 (auto requested, capped to 2)
```

So the export lane's admission is working (concurrency 2, oversized serial);
the tree crossing 8 GiB is NOT over-parallelization.  The real owner is
PER-WORKER memory: a single export/parse worker for the largest module
(class_gen-scale, 147k instructions) peaks at ~7.0 GB, and that one worker
plus the coordinator's retained state pushes the tree past 8 GiB — even
serialized.  Reducing concurrency further cannot help (one 7 GB worker already
exceeds the headroom).  The fix belongs to per-worker export/parse memory (or
coordinator retained state) for the biggest module, not to scheduling width.
This is consistent with the pcc1 A/B (flat wall) and the class_gen sizing:
Stage2 cost here is instruction/arena-bound per worker, and the inline-edge
-17% block reduction does not lower per-worker RSS.


## Owner re-localized by measurement (export-worker hypothesis DISPROVED)

A tracemalloc + RSS probe of the exact export-worker job for the biggest
module (`build_closed_world_context([class_gen.py], lift_indices=None,
merge_exports=False)`) uses only **0.12 GB max RSS / 10 MB tracemalloc peak**.
The export worker's parse+lift is NOT the 7 GB owner; that hypothesis is
disproved.  The measured Stage2 largest process (~7 GB) is therefore a CODEGEN
worker (direct-indexed emit for class_gen: full IR module + indexed-kernel
arenas + the NativeObject/section assembler handoff; the earlier v17 full-cost
single codegen worker peaked 4.29 GB) and/or the coordinator's retained
closed-world state.  Re-localizing to the codegen worker RSS next.


## Owner re-localized again: pcc1 allocator high-water (host codegen DISPROVED)

A single class_gen direct-indexed-emit codegen worker measured under HOST
python (`--pcc-python-multi-codegen-worker`, native object output, same flags
as Stage2) uses only **0.66 GB max RSS**.  So host codegen is NOT 7 GB either.
The measured Stage2 uses PCC1-COMPILED workers (pcc1 compiling pcc2), and
pcc1's own allocator reuses freed cells but cannot unmap whole slabs, so its
high-water RSS does not fall.  The ~7 GB worker is therefore the
pcc1-COMPILED class_gen codegen worker (host 0.66 GB -> pcc1 several GB, an
allocator-amplification, not an algorithmic blowup).  This matches the
per-object-protocol-tax investigation (pcc1 class_gen worker ~2.1 GB there)
and the v17 full-cost 4.29 GB peak tree.  Confirming with a direct v12-pcc1
worker RSS measurement.  Fix direction: reduce the pcc1 codegen worker's peak
allocation for the biggest module, or make the pcc1 allocator return freed
slabs — a runtime/allocator issue, not export or host-codegen.


## CONFIRMED owner (measured): pcc1-compiled class_gen codegen worker

Measurement table (single class_gen module, direct-indexed emit, GC0):

```text
host export worker (parse+lift+export)          0.12 GB max RSS
host codegen worker (native object)             0.66 GB max RSS
pcc1 codegen worker, NO frontend-release       24.65 GB (killed at 400s, still growing; 192 GB footprint)
pcc1 codegen worker, Stage2 (frontend-release)  ~7 GB (from the capped Stage2 largest process)
```

The Stage2 memory owner is the PCC1-COMPILED class_gen codegen worker at
~7 GB, a ~10x amplification over the same worker on host python (0.66 GB).
The 24.65 GB figure was a probe artifact: it omitted
`PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND=1` (which Stage2 sets), so the
worker held the frontend graphs AND the assembler/NativeObject graph at once;
with the release flag it drops to ~7 GB.  The amplification is pcc1-runtime:
its allocator reuses freed cells but does not unmap whole slabs, so peak RSS
does not fall, and the direct-emit indexed-kernel arenas plus the assembler
section/relocation graph are the peak allocators.

Two levers (both belong to HARNESS-P0-STAGE2-MEMORY-SAFE-DEFAULT):
- BOUNDED: release more intermediate codegen state before the assembler graph
  is built (the frontend-release flag already cut 24 GB -> 7 GB; the indexed
  module / IR text can be dropped earlier, and the assembler can free parsed
  sections before encoding).
- DEEP: make the pcc1 allocator return freed slabs (unmap), so peak RSS falls
  as the codegen/assemble phases hand off.

The inline-error-edge -17% block reduction does not help here (it lowered CFG
node count, not the per-worker instruction/arena/assembler RSS).  This is the
precise, measured single-worker owner the parent row asked for; the fix is a
runtime/allocator design piece and is not rushed in a loop slice.


## ROOT CAUSE: slab reclamation is blocked by the granule provenance map's design

Reading the freestanding allocator confirms the mechanism and, crucially, WHY
it cannot be quick-fixed:

- Large allocations (> the 16 KiB top size class) take the direct-mmap path and
  ARE munmapped on free (`pcc_free`: `mapping_size != 0` -> `page_free`).  The
  big codegen arenas (e.g. class_gen `instruction_metadata` ~38 MB) are on this
  path and already return to the OS.
- Small allocations (<= 16 KiB) come from 64 KiB slabs; freed cells go to a
  per-size-class LIFO free list and are reused, but the SLAB PAGES are never
  returned.  The pcc1 class_gen codegen worker allocates millions of small
  transient objects, so the small-slab high-water grows to ~7 GB and never
  falls (host CPython returns this memory; hence host 0.66 GB vs pcc1 7 GB).

The blocker is NOT an oversight.  The slab-granule provenance map
(ARCH-P0-PROVENANCE-GRANULE-MAP) is APPEND-ONLY BY DESIGN: "one granule maps to
ONE stable span descriptor ... never freed, so a descriptor pointer never
dangles; old generations are deliberately leaked ... a concurrent reader never
observes a moved/deleted key or freed table."  That lock-free-reader contract
assumes slabs are NEVER unmapped.  Munmapping an empty slab while its granule
keys remain would let a later `page_alloc` reuse the address under a stale
descriptor, and a GC pointer-provenance probe would read a dangling span ->
segfault / UAF (the repo's worst bug class).

Therefore the correct fix (empty small-slab reclamation) REQUIRES first
extending the granule map to support safe key RETIREMENT under the same
lock-free-reader concurrency contract, then wiring per-slab free-count tracking
and a quiescent-point trim.  That is a fundamental, coupled change to the
ARCH-P0 provenance runtime, validated on all five GC backends and the
pcc1->pcc2->pcc3 fixed point.  It cannot be rushed without risking GC
provenance corruption, and is out of scope for a quick harness fix.  The
precise, measured owner (this receipt) is the durable deliverable; the fix
belongs to a focused granule-map-retirement + slab-reclamation task.

## Update — two code-level refinements to the reclamation design (2026-09-02)

Re-read the allocator + granule reader at source level.  Two findings sharpen
the RUNTIME-P1 slab-reclamation scope beyond the ROOT CAUSE section above:

1. RAW (kind-2) reclamation does NOT touch the kind-1 granule reader deref.
   Every span consumer that reads stride/base/count guards on kind first:
   `pcc_gc_granule_object_slot` and the free path both do
   `if load_i64(span, 0) != 1: return` before touching offsets 8/16/24, and
   `pcc_free` routes on `pcc_gc_granule_kind(ptr) == 1`.  For a kind-2 span the
   ONLY field any reader reads is kind (offset 0).  The pcc1 codegen worker's
   ~6.58 GB high-water is the RAW small-slab family (compiler-internal
   transient records), so reclaiming ONLY kind-2 slabs suffices and never has
   to keep a kind-2 span's stride/base/count accurate across reuse.

2. The page-return mechanism is constrained to munmap by the closed platform
   ABI.  `test_freestanding_allocator_compiles_to_closed_platform_abi_object`
   and `..._self_backend_uses_same_c_abi` assert the compiled object's
   undefined symbols are EXACTLY {mmap, munmap}.  `pcc.unsafe` exposes
   `page_alloc`/`page_free` (mmap/munmap) but no madvise, so a MADV_FREE
   design would need a NEW cross-subsystem compiler intrinsic (pcc.unsafe +
   LLVM + self-backend + Linux raw syscall) plus widening those two ABI
   contract tests.  munmap already exists, so the lower-blast-radius mechanism
   is munmap + granule key RETIREMENT, entirely within freestanding_allocator.py.

Consequence: RUNTIME-P1 = per-slab free counters in the unused 48-byte slab
header (slab+0..47; objects start at slab+48, the span is a separate immortal
record, so the header is free) + a quiescent-point `pcc_allocator_trim()` that
unthreads fully-free kind-2 slabs, munmaps them, and RETIRES their 16 granule
keys (zero the radix leaf slots, which the lock-free reader tolerates as
key->null = "not managed"; and clear the writer-only flat keys/spans array so a
reused address re-registers).  The flat-array key removal needs open-addressing
tombstones or relaxing `_granule_bind_new_locked`'s deliberate
"never rebind a visible key" invariant — which is the core concurrency contract
of the IN-PROGRESS ARCH-P0-PROVENANCE-GRANULE-MAP and owns its real-pthread
registration/relocation stress.  So RUNTIME-P1 stays correctly gated behind
ARCH-P0; it is NOT a rushable side change.  There is no safe partial (trim
without retirement breaks page reuse; madvise is ABI-blocked).
