# Investigation: gc3/gc4 stage2 ~10-14x slower than gc0 — per-frame index-entry malloc

## Status
active — entry-pool/open-addressing slices are bootstrap-verified from prior turns; the 2026-06-15 working-tree slice restores the full five-GC bootstrap matrix and removes the GC4 pathological zpage/frame-index hot paths. Remaining work is residual GC3/GC4 collector bookkeeping and backend #4 RSS/heap pressure, not bootstrap correctness recovery.

## Update (2026-06-15): GC4 zpage/frame-index hot paths fixed; full matrix green

Changes:

- GC4 zpage owner removal now uses the owner index instead of scanning all
  zpages, and known-object-size/freeing paths avoid all-zpage address scans for
  ordinary objects.
- Frame-root restoration now replaces an existing frame-index entry in place
  instead of remove+insert on the hot path.
- The pcc-Python runtime mirrors the C changes.
- A separate GC1/GC2 stale-shell crash was fixed by adding the zero-flag
  tracing-backend early return to the actual pcc-Python `py_decref` mirror.

Verification:

- `PCC_BOOTSTRAP_FULL_REBUILD=1 ... test_pcc_bootstrap_full_gc1.py`
  -> 1 passed in 60.49s.
- `PCC_BOOTSTRAP_FULL_REBUILD=1 ... test_pcc_bootstrap_full_gc2.py`
  -> 1 passed in 50.61s.
- `PCC_BOOTSTRAP_FULL_REBUILD=1 ... gc0/gc3/gc4`
  -> 3 passed in 166.28s.
- Full same-invocation five-GC matrix
  -> 5 passed in 273.15s, with byte-identical `pcc2`/`pcc3` for every backend.
- `tests/python/gc_production_contract`
  -> 130 passed in 17.16s.

Conclusion: the earlier GC4 bootstrap slowdown is no longer acceptable as an
open correctness/perf blocker. GC4 remains heavier than GC0 in short workloads
and long-run RSS/heap, so G-P2 continues as performance work.

## Problem Description
Under self-host bootstrap (`pcc1 -> pcc2`, single-job), the tracing/relocating
GC backends were far slower than the refcount default:

- gc0 (refcount-cycle, CPython-modeled) stage2 ~107s — baseline.
- gc3 (generational-minor-major, **OCaml**-modeled) stage2 226s (~2x... ~10x of
  gc0's lighter path).
- gc4 (colored-relocating / GenZGC, **ZGC**-modeled) stage2 310s.

All five backends are *correct* (byte-identical pcc2/pcc3); this is purely a
performance gap. Backend→reference map: `docs/refs_docs/gc-research/README.md`
(#3 ocaml, #4 zgc).

## Repro
Build one backend-agnostic pcc1 once, reuse per backend (no stage1 rebuild):

```bash
scripts/bootstrap.sh --backend self --out-dir build/probe --stage 1   # build pcc1 once
# then per backend, reusing that pcc1:
cp build/probe/pcc1{,.shared}; cp build/probe/pcc1{.shared,}
PCC_GC_BACKEND=3 PCC_PY_FRONTEND_JOBS=1 \
  scripts/bootstrap.sh --backend self --out-dir build/probe --stage 3 --reuse-stage1
```

(`--reuse-stage1` added 2026-06-04 to skip the backend-agnostic stage1 rebuild.)

Profile the slow backend by sampling the live `pcc1`/`pcc2` while it compiles.

## Test [CONFIRMED]
Per-backend byte-identity (`pcc2 == pcc3` after signature-normalize) is the
correctness gate; bootstrap stage2 `elapsed_ms` is the perf metric. Both
observed directly.

## Proposals
- No.1  LIFO shadow-stack to drop the per-frame `frame_index` hash   [DENIED]
- No.2  Pool the `PccGcPtrIndexEntry` allocations (keep the hash)     [CONFIRMED]
- No.3  Pool the per-frame frame-NODE malloc in `note_frame_enter`    [focused-confirmed; bootstrap perf open]

## No.1 LIFO shadow-stack (drop the hash)
### Code Change
Replace `pcc_gc_note_frame_enter/leave`'s `pcc_gc_frame_index` hash
(insert/remove + per-entry malloc) with a pure LIFO linked-stack: leave pops the
head, falls back to a linear scan for non-head frames.
### DENIED
Regressed gc3 from ~226s to a **900s timeout**. Frames are entered/left at
**slot granularity** (return / print-tuple / owned-local / unary-call all emit
`pcc_gc_note_frame_leave(slots)`), which interleave **non-LIFO**, so the
"fallback" linear scan became the common path → O(n^2) over the ~300-deep
recursive-descent parser. The hash is O(1) regardless of stack position and is
the *correct* structure — the cost was never the lookup. Reverted.

## No.2 Pool `PccGcPtrIndexEntry` (CONFIRMED)
### Code Change
`pcc/py_runtime/src/py_gc_index_table.c`: add a free-list + malloc-fallback
(`pcc_gc_ptr_index_entry_alloc/free`, mirroring the existing
`pcc_gc_object_index_entry` pool) so insert/remove on the frame / forwarding /
identity / zpage indexes recycle entries instead of malloc/free every op. C-only
(the py runtime externs into it — no py mirror needed).
### CONFIRMED
Built clean; verified **byte-identical on all 5 backends** (gc0/1/2/3/4). Stage2:
gc3 226→167s, gc4 310→230s (**~26% each**, likely conservative — measured under
heavy app contention); gc0/1/2 neutral (not frame_index-heavy). A live sample
after the fix shows `malloc` self-samples dropping 2540→845 and the
`frame_index`/`ptr_index` hash ops leaving the hot list. Mechanism: the
per-frame entry malloc/free is gone; the deep parser recursion re-enters/leaves
at similar depths and recycles from the free-list.

## No.3 frame-NODE malloc pool (focused-confirmed / bootstrap perf open)
`note_frame_enter` still `calloc(sizeof(PccGcFrameNode) + n_slots*8)` per frame
(part of the residual 845 malloc samples). Pool via an `n_slots`-bucketed
free-list (≤16 pooled, larger malloc-fallback); mirror C + py. **Risk: this is
frame-root code (same family as the DENIED No.1 — a wrong change = leak/UAF) and
the win is marginal (the dominant remaining cost is the parser's recursive
descent, not GC). Do it deliberately with full 5-backend byte-identity
verification, not rushed.**

## Notes / gotchas
- Do **not** edit runtime source while a bootstrap verify runs: it triggers a
  stale-archive rebuild race → spurious `undefined pcc_gc_* / linker command
  failed` that looks like a backend bug but is the in-flight edit.
- macOS foreground app contention (WeChat/Docker) + background-task throttling
  inflate timings ~5x and can stop a wrapped `timeout` from firing. Benchmark on
  a quiet machine, foreground, for trustworthy numbers.

## Report
The ~26% gc3/gc4 stage2 speedup came from No.2 (pool the index entries), NOT
from removing the hash (No.1, which regressed and was reverted). Correctness was
the gate throughout: No.1 was reverted the moment gc3 timed out; No.2 was kept
only after all five backends verified byte-identical. The entry-pool change is
validated but uncommitted as of 2026-06-04. No.3 (frame-node pool) remains the
next, smaller, higher-risk perf step.

## Update (2026-06-04): review found a thread-safety regression in No.2
A careful review of the No.2 entry pool flagged three issues. Root cause of the
high-severity one: `py_gc_index_table.c` has **no internal locking** — its design
contract is "caller holds the GC graph lock", which the frame/zpage/forwarding
paths honor. But `pcc_gc_object_id()` -> identity-index insert runs **without**
the graph lock, and it shares the **same** `PccGcPtrIndexEntry` free-list. So the
pool turned a previously-safe situation (plain `malloc`/`free` is internally
thread-safe and shares no list) into a **data race** under `PCC_WITH_THREADS=1`:
an unlocked identity insert vs a locked frame/zpage/forwarding remove on one
free-list head. The original 5/5 byte-identity verification was **single-job
(`PCC_PY_FRONTEND_JOBS=1`)**, so the threaded race was never exercised — the
"correctness-safe" claim was only *single-threaded*-safe.

### Code Change
- **HIGH (race):** make the free-list `_Thread_local` (+ a `_Thread_local`
  count), matching the existing `_Thread_local` GC scratch in `py_gc_backend.c`.
  Per-thread free-lists share no mutable state → race-free with no lock; the
  single-threaded bootstrap keeps the full recycling win; threaded callers
  degrade to `malloc`/`free` (correct, no worse than the pre-pool original).
- **MEDIUM (RSS):** cap the free-list at `PCC_GC_PTR_INDEX_FREE_CAP` (16384,
  ~384 KB/thread, above the deep-parser frame working set). `entry_free` past the
  cap calls `free()` instead of pooling, so a `clear()` of a large
  forwarding/identity index — or a transient spike — can no longer pin RSS at its
  high-water mark. (The free-list is shared across all four index families, so a
  per-index `clear()` must not drain the whole list; the cap is the correct knob.)
- **LOW (doc):** reworded the `## Status` first line so the index regenerator's
  first-prose-line summary is a complete clause (it was truncating mid-sentence at
  "but"). Re-ran `scripts/regen_investigations_index.py`.

A **follow-up review** then caught two consequences of the TLS choice:
- **MEDIUM (TLS leak on thread exit):** a `_Thread_local` free-list fixes the race
  but leaks its (≤cap) cached entries when a worker thread exits — the head
  pointer vanishes with TLS, the `malloc`'d entries do not. A thread-churning
  long-running service would lose memory without bound (north-star #5). Fix: a
  non-static `pcc_gc_ptr_index_tls_pool_drain()` (frees the list, zeroes the
  count) called as the **first** line of `pcc_gc_thread_unregister_buffers()`
  (py_gc_backend.c) — before its backend4 store-buffer early-return, so it runs
  for every exiting thread regardless of backend. The trampoline calls that on the
  exiting thread, so the `_Thread_local` pool resolves correctly. The main thread
  isn't drained (process exit reclaims it — one-time, bounded).
- **MEDIUM (doc overclaim):** the `## Status`/INDEX summary still presented "5/5
  byte-identical" as a *current* fact, but the TLS/cap/drain code differs from that
  validated version. Reworded so the index states only what the current code has
  re-verified (gc3+gc4), with the 5/5 attributed to the pre-TLS pool.

### Verification (2026-06-04, under heavy app contention)
- Syntax: clean (`cc -std=c11 -fsyntax-only`).
- **Threaded smoke (the race territory):** `PCC_GC_BACKEND=2 PCC_WITH_THREADS=1`
  `test_gc_backend_concurrent.py` → **6 passed**. A data race can't be *proven*
  absent by one run, but TLS removes the shared-state hazard by construction and
  the threaded path is clean.
- **Functional, single-job:** gc3 `test_gc_backend_generational.py` (frame /
  identity / minor index — the pool's main path) 37+ passing, no failure;
  `test_gc_backend23_production.py` 3 passed; gc4 `test_gc_backend4_production.py`
  29 passing then a contention **timeout** (no failure — the loaded machine
  crawls ~5×). Every test that ran passed. One stray `F` seen only in a *combined*
  generational+backend23 run did NOT reproduce when each file ran alone → a
  contention/isolation flake, not a deterministic regression (consistent with the
  change being single-threaded-equivalent — it cannot selectively fail one test).
- **Byte-identity (authoritative gate) — gc3 [CONFIRMED]:** `pcc1(new
  archive)->pcc2->pcc3`, `--python-libpython off`, **pcc2/pcc3 byte-identical**
  (differ only by Mach-O signature metadata). Proves the TLS/cap change does not
  perturb single-threaded output. Bonus: stage2 = 170.6s ≈ the pre-TLS pool's
  167s (un-pooled was 226s) → the ~26% win is preserved; cap=16384 sits above the
  frame working set so it does not degrade recycling.
- **Byte-identity — gc4 [CONFIRMED]:** complementary run stressing the
  forwarding/zpage index pool (gc3 stressed frame/identity) — pcc2/pcc3
  byte-identical, stage2 229s ≈ the pre-TLS pool. gc0/gc1/gc2 deferred — the
  index-table change is backend-agnostic and gc3+gc4 cover both pool-consumer
  patterns.
- **Drain re-verify [pending]:** the gc3/gc4 byte-identity above is PRE-drain. The
  drain is single-threaded-inert (it runs only on worker-thread exit, which the
  single-job bootstrap never reaches), so byte-identity carries; a post-drain gc3
  `pcc2==pcc3` re-confirm and a `PCC_WITH_THREADS=1` concurrent re-run (which
  exercises the thread-exit drain) are queued.

## Update (2026-06-10): drain re-verify CONFIRMED — both queued items closed

The pool + TLS/cap/drain code is committed (6c71f9fd; drain call verified
present as the first line of `pcc_gc_thread_unregister_buffers()` at
`py_gc_backend.c:2084`, before the backend4 store-buffer early return, with
the TLS free-list + `PCC_GC_PTR_INDEX_FREE_CAP` in `py_gc_index_table.c`).
Observed on the post-drain tree:

- **Post-drain byte-identity, all five backends (supersedes the queued
  gc3-only re-confirm and upgrades gc0/1/2 from "carried" to directly
  verified):** full matrix
  `env -u LC_ALL uv run pytest tests/python/gc/test_pcc_bootstrap_full_gc{0..4}.py -q -n0`
  -> 5 passed in 469.21s with fresh stage2/stage3 per backend and the gate's
  normalized `pcc2 == pcc3` checks green.
- **Thread-exit drain exercised (`PCC_WITH_THREADS=1` concurrent re-run):**
  `PCC_GC_BACKEND=2 ... tests/python/test_gc_backend_concurrent.py -q -n0` ->
  6 passed in 41.40s; `PCC_GC_BACKEND=4` -> 6 passed in 39.95s (drain +
  backend4 store-buffer unregister in the same exit path); `PCC_GC_BACKEND=3`
  -> 6 passed in 33.92s.

Fresh per-backend stage2 profile from the same matrix run (jobs=6, 113 IR
modules) for the next perf step's baseline: gc1 `compile_python_total`
45730ms, gc2 42672ms, gc3 43433ms, gc4 63849ms. gc4 remains the collector-
attributable outlier (codegen worker sum 191261ms vs gc3 135239ms, export
42798ms vs 21297ms — read-barrier cost over frontend work), while
`multi_frontend_worker_codegen_max_ms` is only ~8.3-13.4s of a 31-49s
codegen wall, so the critical path is no longer one oversized module;
duplicated worker parse remains ~22-24s CPU per stage. Next candidates, in
evidence order: (a) gc4 read-barrier inclusive cost via folded stacks over a
gc4 codegen window, (b) a no-sidecar worker/export parse-reuse design (AST
wire stays DENIED), (c) No.3 frame-NODE pool — deliberate, full 5-backend
byte-identity only.

## Update (2026-06-10): gc4 codegen-window self-sample capture — index maintenance dominates; read-barrier-as-primary DENIED

Capture: stage2-only gc4 probe reusing the fresh matrix `pcc1`
(`PCC_GC_BACKEND=4`, jobs=6, `--stage 2 --reuse-stage1`,
stage2 `elapsed_ms=69987`), sampled by a loop of macOS `sample <pid> 3`
over all live probe processes for a 150s window: 70 codegen-worker captures.
Self counts below are exact sums of `sample`'s "Sort by top of stack"
sections; "incl" figures are approximate subtree-line sums (can
double-count) — use them for ordering only.

- Total self samples 159,812, of which `__wait4` (parent idle) 38,727;
  non-wait base ≈ 121,085.
- **GC helpers ≈ 50,116 self = 41.4% of non-wait worker CPU.** Dominated by
  index-table maintenance, NOT the load barrier:
  `pcc_gc_object_index_insert` 8,383 + `pcc_gc_ptr_index_upsert` 8,086 +
  `pcc_gc_ptr_index_insert_raw` 6,813 + `pcc_gc_object_index_find` 4,484 +
  `pcc_gc_frame_index_remove` 3,302 + `py_gc_index_insert` 2,011 ≈ 33k
  (~27% of non-wait). Allocation/tracking context confirms:
  `pcc_gc_note_object_allocated_sized` incl ≈ 8,261,
  `user_py_gc_backend__backend4_zpage_link_node` incl ≈ 7,470,
  `pcc_gc_note_frame_enter` incl ≈ 11,850.
- **Read barrier is small:** `pcc_gc_load_ptr` self 4,500 (3.7% of
  non-wait) + `pcc_gc_note_relocation_read` 1,352 + `pcc_gc_note_load`
  incl ≈ 2,496. The "gc4 outlier = read-barrier over frontend work"
  hypothesis is DENIED as the primary cost; the outlier is per-alloc/track
  index upkeep for relocation readiness.
- Hottest non-GC symbols (shared, backend-agnostic):
  `user_py_obj_ops_compare__cmp_threeway` 10,192 self (largest single CPU
  symbol; incl ≈ 17,742), `py_class_attrs_dict` 3,795,
  `user_py_class__strs_eq` 3,548, `user_py_class__class_lookup_in_mro`
  1,913, nanov2 malloc/free family ≈ 7-8k. Residuals worth a later look:
  `user_py_gc_backend__init_config` 2,145 self and
  `user_py_gc_backend__counter_inc` 2,057 despite the config fast path.
- Evidence-ordered candidates for the next G-P2-GCPERF design (all
  design-first, none has "full confidence" today): (a) gc4 alloc-path
  index maintenance — can object/zpage/ptr index inserts be batched or
  cheapened per-op without weakening relocation correctness; (b) shared
  runtime `cmp_threeway` / class-string lookup fast paths (helps all five
  backends); (c) No.3 frame-NODE pool (frame-enter incl confirms it is
  real but smaller). Artifacts: `/tmp/pcc-gc4-flame-20260610/` (volatile),
  probe profile `build/bootstrap-probe-gc4-flame/profile/stage2.json`.

## Update (2026-06-10 evening): candidate (b) landed as sorted()-merge — TESTING, matrix pending

Root-cause refinement for the `cmp_threeway` 10.2k-self hotspot: the
caller is `py_obj_sorted`, which was an O(n^2) INSERTION sort in BOTH
runtime tiers — the comparison COUNT, not the per-compare cost, was the
lever (a word-at-a-time str-compare attempt moved the sort microbench
only ~5%: 2.12s -> 2.01s user). Both tiers now run a bottom-up STABLE
merge sort (ping-pong between `out` and a scratch py_list; elements
moved borrowed — raw barrier loads + `py_list_append` stores, zero
refcount traffic; scratch dropped via length=0 so aliasing slots never
decref elements; right run wins only when strictly smaller =
stability). The port's word-at-a-time str compare is kept (harmless,
slightly positive). Microbench (2000 shared-prefix symbols x 40
sorted): port 2.01s -> 0.05s user (~40x); cc tier 0.03s.
DISCOVERED, recorded, NOT touched (one proposal at a time): the C
insertion-sort path leaked one ref per `py_list_get` (get increfs, set
stores raw, no decref) — the leak shape is now confined to the
malloc-failure fallback branch.
Evidence so far: CPython differential (incl. stability via
`sorted([2.5, 1, 3.5, 2, True])` keeping `1, True` input order) clean
on the port tier (backends 0..4) and cc tier;
`tests/python/test_native_sorted_merge.py` -> 3 passed (port + cc +
relocating-backend matrix); `gc_production_contract` -> 130 passed.
NOT yet run: the five-GC bootstrap matrix — claim stays TESTING until
it passes; expected side benefit on stage walls is unmeasured.

## Update (2026-06-10 night): sorted()-merge claim upgraded — five-GC matrix green

Five-GC bootstrap matrix on the merge-sort source state -> 5 passed in
461.73s; fallback baselines -> 18 passed. The slice is now
bootstrap-verified DONE_WEAK (was TESTING). Honest metric notes: the
matrix ran ~57s faster than the previous run (519.17s) but matrix
wall-clock varies 426-520s across the day — NOT claimed as the
merge-sort's contribution. A `tests/python/gc` full-session timing this
turn read 405.81s, but the runtime archives had been wiped this slice
(cold object cache) — not comparable to the recorded best-warm 247.56s;
a warm re-measure is the follow-up before any <200s-target claim
movement. Candidate (a) gc4 index-maintenance batching and (c)
frame-NODE pool remain open design-first items.

## Update (2026-06-10 night #2): the `tests/python/gc < 200s` target is metric-confused — recalibrated

Two consecutive full-session timings on the merge-sort source state:
405.81s (cold object cache) then 480.74s (warm) — the warm run came
out SLOWER, and both sit far above the recorded best-warm 247.56s.
Diagnosis: `tests/python/gc` today contains the five
`test_pcc_bootstrap_full_gc{0..4}.py` files, i.e. the session IS
approximately the full five-GC three-stage bootstrap matrix (462-520s
by itself today) plus small tests; day-scale machine load dominates
the residual. A "<200s for tests/python/gc" target is structurally
unreachable in this composition without halving the bootstrap itself,
and the 247.56s best-warm almost certainly predates the matrix files
living in that directory. RECALIBRATION: G-P2-GCPERF's wall-clock
metric should be (i) the five-GC matrix wall (today's range 426-520s)
and (ii) per-backend stage2 `compile_python_total` (last good baseline
gc1 45.7 / gc2 42.7 / gc3 43.4 / gc4 63.8), each measured same-day
back-to-back when comparing. The <200s directory target is retired as
ambiguous — superseded by those two concrete metrics.

## Design (2026-06-10 night): candidate (a) — open-addressing rewrite of the GC index tables

Evidence recap: gc4 codegen-window self-samples put index-table
maintenance at ~27% of non-wait worker CPU (object/ptr/frame index
insert/upsert/find). Source audit of `py_gc_index_table.c`:

- `pcc_gc_object_index_*` already uses a chunk pool + free list (no
  per-insert malloc), but buckets are CHAINED: every find/insert walks
  a linked list whose entries live scattered across chunks — one
  dependent-load cache miss per hop, plus the `entry->next` write on
  insert. The ptr-index family (`pcc_gc_ptr_index_upsert`: forwarding /
  zpage-owner / zpage-page / frame indexes) shares the same chained
  shape; the legacy `py_gc_index_*` (py_obj_gc.py) additionally pays a
  duplicated find+hash on insert and a raw malloc per entry.
- Thread-safety today relies on the GC graph lock for frame/zpage/
  forwarding callers while the identity index runs thread-local
  unlocked (entry-pool split is already documented in-source at line
  ~197) — any rewrite must preserve exactly this split.

Proposal: convert the hot tables (object index + ptr-index family) to
OPEN ADDRESSING with linear probing — flat `{key, node}` slot arrays,
power-of-two caps, the existing avalanche hash, max-load 1/2, and
tombstone-free deletion via backward-shift (Robin-Hood-style compaction
on remove) so probe chains never accumulate dead slots. Expected
effects: find/insert touch one or two adjacent cache lines instead of
a pointer chain; the entry pool, free list, and chunk walker disappear
for those tables (less code, no pool-vs-lock split to maintain for
them); remove becomes O(probe-run) shift but stays bounded by load
factor. Risks to verify: (i) backward-shift delete correctness under
the wrap-around boundary (focused C unit probe + the existing
`gc_production_contract` slot-graph bricks); (ii) rehash-in-progress
visibility — same single-writer discipline as today (graph lock /
thread-local), no new locking; (iii) memory: flat arrays at load 1/2
vs chunked entries — comparable or better for the observed populations
(object index init cap is already 16384). Verification plan (next
turn, full slice): focused index unit probe; `gc_production_contract`
130; per-backend focused gates; five-GC matrix; same-day back-to-back
stage2 `compile_python_total` for gc4 vs the gc1/gc2/gc3 baseline to
measure the actual win; pcc2->pcc3 byte proof is NOT required (no
metadata shape change — the tables are process-local acceleration
structures, not object-graph layout).

## Update (2026-06-10 night #3): candidate (a) low-risk subset landed — gc4 -18.4% same-day back-to-back

Instead of the full open-addressing rewrite (design above, still
available), landed the zero-semantic-risk subset: (1) the three chained
INSERT paths (`py_gc_index_insert`, `pcc_gc_ptr_index_insert`,
`pcc_gc_ptr_index_insert_raw`) now hash once and walk the chain once
(they used to run a full find — hash + chain walk — then re-hash and
walk again on the miss path); (2) load factor tightened 3/4 -> 1/2 on
all five tables (expected chain length halves; rehash is amortized).
No structural change: same chained buckets, same entry pools, same
thread-local vs graph-lock split, no metadata shape change.

Measured (same-day, back-to-back, warm object cache both sides):
`test_pcc_bootstrap_full_gc4.py` BEFORE 148.31s -> AFTER 121.07s
(**-18.4%**, gc4 being the index-heaviest backend). Full five-GC
matrix on the changed source -> 5 passed in 416.54s (today's range was
426-520s; the prior run was 461.73s — suggestive cross-backend benefit
but matrix variance means the single-file delta is the claim-grade
number). `gc_production_contract` + sorted-merge regressions -> 133
passed. Fallback baselines not rerun: the change is C-runtime-only
(no frontend/port lowering paths touched). The open-addressing rewrite
remains a recorded follow-up if the next profile still shows index
maintenance dominating.

## Correction (2026-06-10 night #4): merge-sort refcount audit — two leaks fixed, claim narrative corrected

While wiring J2' the `pcc_gc_store_ptr` source was read directly
(py_obj.c:356): it INCREFS the new value and DECREFS the old one —
i.e. py_list_append/py_list_set are REFERENCE-BALANCED, contradicting
yesterday's "borrowed moves, zero refcount traffic" merge-sort design
narrative. Consequences audited with a new __del__-count probe:

1. The ping-pong reset (`length = 0` bare write) skipped the old-value
   decrefs — one leaked reference per element per pass (values stayed
   correct; objects never freed). FIXED both tiers: reset now clears
   each slot through the balanced store (py_list_set NULL / store_ptr
   NULL) before zeroing length; the final move-back and scratch drop
   do the same. Every element stays alive via its other list's slot
   throughout (no mid-sort zero-ref window).
2. The C fill path's list/tuple branch never dropped its OWNED
   `py_obj_getitem` result after the (increfing) append — the
   PRE-EXISTING leak recorded yesterday as DISCOVERED is now FIXED
   (the iter branch and the port fill were already balanced; the set
   branch appends a borrowed key, correctly left alone).

Observed: __del__ probe prints `0 64` (all 64 elements released) on
BOTH tiers == CPython; value differential still clean; new
parametrized regression `test_sorted_merge_releases_all_elements`
(port + cc); suite 5 passed; gc_production_contract + sorted suite
135 passed; five-GC matrix -> 5 passed in 493.89s. The microbench
claim (port 2.01s -> 0.05s) is unaffected (the balanced clears add
O(m) per pass). Lesson recorded: read the barrier-helper SOURCE before
asserting its refcount contract — the earlier "py_list_set does not
incref" inference came from reading only the call sites.

## Update (2026-06-10 ~23:15): post-optimization gc4 quick resample — qualitative only, NOT baseline-comparable

A lightweight resample during a live gc4 stage2/3 window (4x 3-second
`sample` captures of the pcc1/pcc2 workers, ~81.5k stack-frame lines)
gives FRAME-COUNT (inclusive) statistics, which are NOT comparable to
the baseline's 70-capture SELF-sample methodology (GC helpers 41.4% /
index ~27% of non-wait worker CPU). Qualitative read: index-named
symbols appear in only ~2.1% of stack-frame lines and gc-ish symbols
~8.2%; the hot inclusive frames are the codegen emit chain
(stmt dispatch / emit_methods), consistent with the index single-probe
+ load-factor change having moved the bottleneck back toward shared
frontend work — but NO numeric claim is made on this methodology. A
baseline-comparable self-sample re-run (same 70-capture harness) is
the recorded next step before choosing between the open-addressing
rewrite and other G-P2-GCPERF candidates.

## Update (2026-06-12): sample aggregation harness landed; current gc4 baseline recorded

The prior quick resample was methodologically weak, so this slice did not
touch runtime/collector code. It made the next profile step reproducible.

Code:

- Added `scripts/pcc_sample_aggregate.py`, a macOS `sample(1)` top-of-stack
  self-count aggregator. It reads one or more capture files/directories,
  parses the "Sort by top of stack" section, aggregates duplicate symbols, and
  reports total self, non-wait self, category totals, and top symbols.
- Categories currently separated for the G-P2 workflow: wait, GC index, GC read
  barrier, other GC, compare/sort, class lookup, allocator, and other.
- Added `tests/python/test_pcc_sample_aggregate.py` to lock both direct parser
  behavior and `--json` CLI output. The fixture covers both leading-count
  sample lines and the trailing-count format used in investigation notes.

Evidence:

- `tests/python/test_pcc_sample_aggregate.py -q -n0` -> 2 passed in 0.21s.
- Touched-file `py_compile` for the script and test passed.
- Current same-worktree focused GC4 bootstrap baseline:
  `tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s` -> 1 passed in
  107.73s, with pcc2/pcc3 byte-identical.
- Focused GC4 profile totals from
  `build/bootstrap-pytest-self-gc4/profile`: stage2 `compile_python_total`
  50889ms; stage3 `compile_python_total` 50022ms.
- Hygiene passed: `git diff --check`, touched-file trailing-whitespace search,
  and residual `pcc`/`pytest`/`bootstrap.sh` process checks were clean.

Status: G-P2 remains `IN_PROGRESS`, not done. This is profiling
infrastructure plus a current focused baseline only. The next claim-grade step
is to run a baseline-comparable self-sample capture through the new aggregator
(same 70-capture methodology as the 2026-06-10 gc4 codegen-window baseline),
then decide whether the full open-addressing GC index rewrite still targets the
dominant hotspot or whether the bottleneck has moved to shared frontend/codegen.

## Update (2026-06-12 #2): baseline-comparable gc4 self-sample confirms index maintenance still dominates

Ran the recorded claim-grade self-sample step on the same worktree and the
same focused GC4 bootstrap output directory. The stage2-only probe reused the
existing pcc1 (`PCC_GC_BACKEND=4`, jobs=6, `--stage 2 --reuse-stage1`) and
completed successfully:

- stage2 `elapsed_ms=79199`; shell `real 1m18.033s`, `user 4m36.218s`,
  `sys 0m13.509s`.
- `build/bootstrap-pytest-self-gc4/profile/stage2.json`:
  `compile_python_total=50889ms`.
- `build/bootstrap-pytest-self-gc4/stage2.result.json`:
  `compile_wall_ms=51193`, `wall_ms=52279`, `returncode=0`.
- Capture directory: `/tmp/pcc-gc4-sample-20260612-012600`, 70 non-empty
  macOS `sample(1)` files.

The first aggregator pass revealed a parser classification bug:
`__wait4_nocancel` was being counted as `other`, which inflated the non-wait
base. The harness now classifies both `__wait4` and `__wait4_nocancel` as
`wait`; `tests/python/test_pcc_sample_aggregate.py` covers that shape.
Corrected aggregate:

```text
total_self 166100
non_wait_self 118457
wait 47643
gc_index 32930 27.8% nonwait
gc_other 19033 16.1% nonwait
gc_read_barrier 6281 5.3% nonwait
class_lookup 11782 9.9% nonwait
allocator 11267 9.5% nonwait
compare_sort 9667 8.2% nonwait
other 27497 23.2% nonwait
```

Top symbols keep the same ordering as the pre-optimization baseline, with
index maintenance still hot:

```text
7671 pcc_gc_object_index_insert
6180 pcc_gc_ptr_index_insert_raw
6082 pcc_gc_ptr_index_upsert
4600 pcc_gc_object_index_find
3539 pcc_gc_frame_index_remove
2351 pcc_gc_ptr_index_rehash
```

Conclusion: the open-addressing GC index rewrite is still evidence-ordered as
the next runtime slice. The low-risk single-probe/load-factor subset helped,
but it did not move the bottleneck away from object/ptr/frame index
maintenance. The next code step should be the structural flat-slot rewrite
with a focused C probe for collision/delete compaction, then
`gc_production_contract`, the focused GC4 bootstrap gate, and finally the
five-GC matrix before any performance claim is upgraded.

## Update (2026-06-12 #3): open-addressing rewrite implemented — TESTING

Implemented the structural table change scoped to
`pcc/py_runtime/src/py_gc_index_table.c`:

- Added a shared flat `PccGcIndexSlot { key, node }` helper with linear probing,
  power-of-two capacities, load factor 1/2, and backward-shift delete
  (`hole_dist < scan_dist`) so removes do not leave tombstones.
- Converted the hot ptr-index family (forwarding, forwarding-target, identity,
  frame, zpage owner, zpage page) from chained entry nodes to flat slots.
- Converted the object index from chained chunk/slab entries to flat slots.
- Converted the legacy `py_gc_index_*` table used by `py_obj_gc.py` to the same
  flat-slot helper.
- Kept `pcc_gc_ptr_index_tls_pool_drain()` as a no-op stable hook because the
  thread-exit path still calls it, but the new table has no thread-local entry
  pool to drain.

Focused evidence before broad gates:

- `cc -std=c11 -I pcc/py_runtime/include -I pcc/py_runtime/src -fsyntax-only
  pcc/py_runtime/src/py_gc_index_table.c` -> passed.
- `tests/python/test_pcc_sample_aggregate.py -q -n0` -> 2 passed.
- Focused C/source-shape gate:
  `tests/python/test_gc_backend_generational.py::test_gc_frame_index_accepts_raw_slot_pointer_keys
  tests/python/test_gc_backend_generational.py::test_gc_open_addressed_indexes_preserve_probe_chains_after_delete
  tests/python/test_gc_backend_generational.py::test_gc_indexes_use_open_addressed_slots_and_backward_shift_delete
  -q -n0` -> 3 passed in 12.87s.

The new C probe deliberately searches for keys that collide in the initial
frame-index and object-index masks, checks duplicate insert preserves the old
node, removes entries from the middle of the probe chain, and then verifies the
remaining keys still resolve. This targets the highest-risk part of the rewrite:
delete compaction losing a later root/forwarding/object entry.

Status: `TESTING`, not complete. Next gates are the common
`gc_production_contract`, focused GC4 bootstrap, and the full five-GC matrix.
No runtime performance claim is upgraded yet.

## Update (2026-06-12 #4): GC contract and focused GC4 bootstrap green — matrix pending

The open-addressing rewrite has passed the first broad correctness layer:

- `tests/python/gc_production_contract -q -n0` -> 130 passed in 60.26s.
- `tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s` -> 1 passed in
  118.48s; the gate reports pcc2 and pcc3 byte-identical under
  `PCC_GC_BACKEND=4`.
- Focused GC4 profile on this run: stage2/stage3 `compile_python_total`
  55763ms / 47407ms; `compile_wall_ms` 56052ms / 47526ms; returncode 0.

No performance claim is drawn from this focused run because its jobs/settings
and host conditions differ from the 70-capture measurement probe. The remaining
claim gate is the full five-GC bootstrap matrix.

## Update (2026-06-12 #5): five-GC matrix green — open-addressing rewrite is bootstrap-verified DONE_WEAK

The full five-GC bootstrap matrix passed on the open-addressing source state:

```text
tests/python/gc/test_pcc_bootstrap_full_gc0.py
tests/python/gc/test_pcc_bootstrap_full_gc1.py
tests/python/gc/test_pcc_bootstrap_full_gc2.py
tests/python/gc/test_pcc_bootstrap_full_gc3.py
tests/python/gc/test_pcc_bootstrap_full_gc4.py
-q -n0 -s
```

Observed: 5 passed in 514.90s. Each backend reported pcc2 and pcc3
byte-identical:

- GC0: passed.
- GC1: passed.
- GC2: passed.
- GC3: passed.
- GC4: passed.

Matrix profile `compile_python_total` (stage2/stage3):

```text
gc0 38396ms / 36339ms
gc1 58673ms / 44803ms
gc2 41100ms / 39635ms
gc3 40870ms / 50579ms
gc4 58759ms / 88963ms
```

Claim boundary: this upgrades the open-addressing rewrite only to a
bootstrap-verified `DONE_WEAK` runtime slice. It does NOT prove a net
performance win: the matrix is noisy, the pre-change sample probe used a
different isolated stage2 window, and the GC4 stage3 number in this matrix is
high enough that a post-change sample/same-day measurement is required before
the next performance claim. G-P2 remains open.

## Update (2026-06-12 #6): post-change self-sample — correctness slice stands, performance win NOT proven

Post-change GC4 stage2-only probe (`PCC_GC_BACKEND=4`, jobs=6,
`--stage 2 --reuse-stage1`) completed with stage2 `elapsed_ms=60896`. A first
sequential 1-second sample loop captured only 38 non-empty files and is weak
evidence only; it is kept out of the claim. The stronger rerun sampled live
workers in parallel with the original 3-second duration and captured 72
non-empty files:

- Capture directory: `/tmp/pcc-gc4-sample-openaddr-3s-20260612-015851`.
- `total_self=169419`, `wait=47539`, `non_wait_self=121880`.
- `gc_index=36185` = 29.7% non-wait.
- `gc_other=22988` = 18.9% non-wait.
- `gc_read_barrier=6861` = 5.6% non-wait.
- GC helpers total = 66034 = 54.2% non-wait.

Top index symbols:

```text
8407 pcc_gc_ptr_index_remove_raw
8008 pcc_gc_object_index_insert
7489 pcc_gc_ptr_index_upsert
6339 pcc_gc_object_index_find
3524 pcc_gc_index_rehash_slots
2086 pcc_gc_ptr_index_insert_raw
```

Interpretation:

- The structural rewrite is correctness-verified, but the performance
  hypothesis is NOT confirmed. Index maintenance remains at least as prominent
  in the self-sample as before (pre-change corrected sample: `gc_index` 27.8%
  non-wait; post-change 29.7%).
- The stage2 `elapsed_ms` improved relative to the pre-change sampled probe
  (79199ms -> 60896ms), but the profile JSON for the post-change ad-hoc probe
  was not refreshed (mtime predates the probe), so the only stable
  post-change profile numbers remain the full-matrix values recorded above.
  Treat elapsed as suggestive only, not as a claim-grade speedup.
- The next evidence-ordered optimization should not repeat the broad
  "chains are the problem" hypothesis. It should target the residual shape now
  visible in the post-change sample: frame/raw remove cost
  (`pcc_gc_ptr_index_remove_raw`) and rehash cost (`pcc_gc_index_rehash_slots`),
  or pivot to a different shared frontend/codegen hotspot after a focused
  design. G-P2 remains open.

## Update (2026-06-12 #7): final-source unsigned-distance cleanup reverified

After the first full matrix, diff review hardened the backward-shift distance
calculation from signed `& mask` arithmetic to explicit unsigned distances:

```c
uint64_t scan_dist =
    ((uint64_t)scan - (uint64_t)ideal) & (uint64_t)mask;
uint64_t hole_dist =
    ((uint64_t)hole - (uint64_t)ideal) & (uint64_t)mask;
```

This is the same algorithm, but it avoids relying on signed bitwise behavior
for negative wrap-around distances. Because it touched the runtime file after
the matrix, the broad gates were rerun on the final source:

- C syntax-only for `py_gc_index_table.c` -> passed.
- Focused raw-key/collision/delete-compaction/source-shape gate -> 3 passed in
  12.83s.
- `tests/python/gc_production_contract -q -n0` -> 130 passed in 59.42s.
- Full five-GC bootstrap matrix -> 5 passed in 444.05s, with pcc2/pcc3
  byte-identical under GC0..GC4.

Final matrix `compile_python_total` stage2/stage3:

```text
gc0 22275ms / 24043ms
gc1 37441ms / 44013ms
gc2 34849ms / 43136ms
gc3 38655ms / 47761ms
gc4 62669ms / 60630ms
```

Final status for this slice: bootstrap-verified `DONE_WEAK` for correctness of
the open-addressed index structure. Still no net performance claim: the
post-change self-sample kept index maintenance hot, so the next G-P2 slice
needs a narrower design around remove/rehash cost or a measured pivot.

## Design (2026-06-12): residual index cost — split initial capacities

Post-open-addressing sample narrowed the residual index-specific cost to
`pcc_gc_ptr_index_remove_raw` and `pcc_gc_index_rehash_slots`. Source audit:
the structural rewrite gave every ptr-index family table the same 256-slot
initial capacity, and the legacy `py_gc_index_*` table also still starts at
256. That is appropriate for rarely-populated forwarding/identity/zpage
indexes, but too small for the frame/raw and pcc-Python legacy GC index paths
exercised by bootstrap codegen workers.

Selected minimal slice:

- Add an `initial_cap` field to `PccGcPtrIndex`.
- Keep the default ptr-index initial capacity at 256.
- Give `pcc_gc_frame_index` a heavier initial capacity so frame enter/leave
  does not immediately form long probe runs or rehash during parser recursion.
- Raise legacy `py_gc_index_*` initial capacity for the pcc-Python GC tracking
  path.
- Leave the open-addressing algorithm, load factor, deletion, barriers,
  finalizers, roots, and owned-local cleanup unchanged.

Expected effect: reduce early `pcc_gc_index_rehash_slots` and shorten the
probe runs that make `pcc_gc_ptr_index_remove_raw` hot. Risk is bounded to
memory footprint: a few tens of KB for the frame table and legacy table once
they are first used. Correctness gates stay the same as the open-addressing
slice: focused C/source-shape probe, `gc_production_contract`, and the full
five-GC matrix before any claim upgrade.

## Update (2026-06-12 #8): initial-capacity split DENIED by measurement; replaced with tombstone deletion

The initial-capacity split was implemented and passed correctness gates, but
measurement denied it as a performance slice:

- C syntax-only -> passed.
- Focused raw-key/collision/delete/source-shape gate -> 3 passed in 14.82s.
- `tests/python/gc_production_contract -q -n0` -> 130 passed in 61.89s.
- Full five-GC matrix -> 5 passed in 515.41s, pcc2/pcc3 byte-identical.
- Matrix `compile_python_total` stage2/stage3:

```text
gc0 25577ms / 23103ms
gc1 53470ms / 41066ms
gc2 36672ms / 52232ms
gc3 42462ms / 51794ms
gc4 78917ms / 82202ms
```

Post-change measurement:

- GC4 stage2-only elapsed `72850ms`.
- 72 non-empty 3-second captures in
  `/tmp/pcc-gc4-sample-capacity-3s-20260612-023146`.
- Aggregate: `total_self=170062`, `non_wait_self=122361`,
  `gc_index=36271` = 29.6% non-wait, GC helpers total ≈ 53%.
- Top index symbols still included `pcc_gc_ptr_index_remove_raw` 8979 self.

Conclusion: the larger initial tables did not reduce the hot remove path and
added memory overhead. The experiment is DENIED and the code was replaced with
a narrower remove-path design: tombstone deletion for the open-addressed GC
indexes. The new approach keeps linear probing but changes remove from
backward-shift compaction to marking a slot `PCC_GC_INDEX_SLOT_DELETED`; insert
reuses the first tombstone found, and `used` counts trigger same-cap rehash when
tombstones consume the load budget. This directly targets
`pcc_gc_ptr_index_remove_raw` without raising steady-state table sizes.

## Update (2026-06-14): frame-node pool + frame-index replace; short path fixed, bootstrap still open

Current working-tree slice:

- GC3 retained-span/minor-block reuse is in place for the correctness-first
  fallback path.
- GC3/GC4 skip GC graph tracking for leaf object tags.
- GC4 skips zpage allocation for leaf object tags and clears stale
  `PY_FLAG_GC_ZPAGE_ALLOC` on that path.
- `pcc_gc_note_frame_enter/leave` now recycle small `PccGcFrameNode` records
  through a size-bucketed pool in both C and the pcc-Python runtime mirror.
- `pcc_gc_frame_index_replace(...)` replaces the previous remove+insert shape
  for duplicate frame slot keys.

Focused correctness evidence:

- C syntax-only passed for touched runtime sources, including a
  `PCC_WITH_THREADS=1` syntax pass.
- pcc-Python runtime mirrors and tests passed `py_compile`.
- Runtime archives rebuilt cleanly.
- Focused GC3/GC4 tests passed: 8 passed in 29.04s.
- Full `tests/python/gc_production_contract` passed: 130 passed in 17.25s.

Latest dict-heavy benchmark rerun (`/tmp/pcc_dict_gc_bench.py`) now reads:

```text
CPython median: 0.034591s
self GC#0: 0.021498s, 0.622x
self GC#3: 0.033099s, 0.957x
self GC#4: 0.035883s, 1.037x
llvm GC#0: 0.021773s, 0.629x
llvm GC#3: 0.032640s, 0.944x
llvm GC#4: 0.036215s, 1.047x
```

This retires the original short-workload regression numbers
(`GC #3 1.702x`, `GC #4 3.680x`) as current evidence. It does not close the
bootstrap performance gap. Fresh GC3 stage2 probes were stopped before gate
completion, so they are not correctness/performance gates. Samples still show
`pcc_gc_ptr_index_insert_raw` and frame-root enter/leave dominating the long
path, with high RSS in workers. The next serious optimization should reduce
the frontend's short-lived frame-root scopes or consolidate generated rooting
regions; another hash-table-only tweak is unlikely to move the bootstrap
critical path enough.

## Update (2026-08-07): pcc-Python index engine — find_slot dominates GC3/GC4 again; primary clustering from identity-like hash

Context: the user reports gc3/gc4 bootstrap "too slow" again after the
2026-08-03/04 migration of the GC index engine to
`pcc/py_runtime/py/freestanding_gc_index_table.py`.

Measured with a 12-line allocation-churn program (200 rounds x 500 dict/list/str
rows), compiled by the current gc4-dir pcc1:

```
PCC_GC_BACKEND=0  0.32s
PCC_GC_BACKEND=3  0.71s   (2.2x)
PCC_GC_BACKEND=4  3.98s   (12.4x)
```

`sample` on the GC4 run: ~70% of samples in `pcc_gc_index_py_find_slot`
(probe loop offsets), ~25% in `memset` (rehash calloc zeroing). Bootstrap
mirror of the same cost: gc3 stage3 wall 231s vs gc1/gc2 48s on 2026-08-06.

Analysis:
- The engine is open-addressing + linear probing + tombstones. The insert
  path DOES gate on `used + 1 > cap/2` with `used` counting EMPTY->occupied
  transitions (tombstones included), matching the C oracle
  (`src/py_gc_index_table.c`), so tombstone-full lookup livelock is not
  reachable through this engine's own insert path.
- The hash is `(ptr>>3) ^ (v>>17) ^ (v>>33)` in BOTH tiers. For bump/arena
  allocation the keys are consecutive; this hash maps consecutive keys to
  consecutive slots, which is the worst case for linear probing (primary
  clustering: giant contiguous runs, probe cost ~run/2 per absent-key lookup).
  The C comment explicitly chose to avoid multiplies; that trade-off is what
  the migration re-amplified (pcc-compiled probe iterations are also more
  expensive than the C loop).

Planned fix (both tiers in lockstep, differential-equal): Fibonacci hashing —
one 64-bit multiply (`v * 0x9E3779B97F4A7C15`, then fold high bits) breaks
consecutive-key clustering; expected to collapse the probe-loop share on both
the object index and the frame index. Held until the in-flight gc4 deadlock
instrumentation builds land (see
gc4-pcc2-graph-lock-deadlock-stage2-miscompile.md), then measured with the
churn reproducer + one explicitly chosen backend bootstrap stage before any
matrix run.

Also observed while differentialing: `PCC_RUNTIME_CC=cc` no longer links for
this configuration (undefined symbols) — the C tier of the runtime is
currently not buildable as a differential control for these paths; separate
follow-up needed.

## Update (2026-08-07, later): Fibonacci hash landed in both tiers — GC4 churn 5.5x faster

Applied the planned fix (identical bits in `src/py_gc_index_table.c::py_gc_index_hash_ptr`
and `py/freestanding_gc_index_table.py::pcc_gc_index_py_hash_ptr`):
`v = (ptr>>3) * 0x9E3779B97F4A7C15; v ^= v>>32` (port uses the two's-complement
literal -7046029254386353131 with wrapping_mul_i64).

Churn reproducer, same binary shape as the 2026-08-07 baseline:

```
            old hash    fibonacci
GC0         0.32s       0.26s
GC3         0.71s       0.43s   (1.65x)
GC4         3.98s       0.73s   (5.5x; 12.4x-vs-GC0 -> 2.8x)
```

Not yet run: focused gc3/gc4 bootstrap stage timing and the scheduler/GC test
gates — required before claiming the bootstrap-level win. The frame index and
all pcc_gc_index_py_* consumers share this hash, so both the object-tracking
and frame-registry probe costs drop together.

## Update (2026-08-07, session 3): longrun remeasured post-hash (GC4 40k -> 294k median); profile still index-dominated; backward-shift deletion lands (294k -> 321k median, GC4 now 8.0x the 08-03 baseline)

Pinned longrun (100k-round churn, strict no-libpython/self,
`scripts/run_gc_longrun_gate.py`) remeasured with the Fibonacci hash in the
runtime (the 2026-08-03 baselines predate it):

```text
            2026-08-03     post-hash (3 runs, median)
GC0         443,581        560,665
GC1         262,984        385,844
GC2         260,257        370,928
GC3         252,475        358,021
GC4          40,047        294,320   (7.3x; zero drift; RSS 9.50MB)
```

GC4 still slowest; `sample` on the churn binary attributed the remaining time:
`pcc_gc_index_py_find_slot` 536 samples + `memset` 375 + `calloc` 231 +
`rehash_slots` 60 + `find` 88 — i.e. the index ENGINE again, but now the
tombstone-clearing rehash storm: the steady-live-set churn turns every
remove into a tombstone, `used` (occupied+tombstones) hits the `cap/2`
insert gate every ~N remove/insert pairs, and each rehash pays a fresh
`calloc` + zeroing + full reinsert.

Fix (both tiers in lockstep, differential-equal): **backward-shift deletion**
in `src/py_gc_index_table.c::pcc_gc_index_remove_slot` and
`py/freestanding_gc_index_table.py::pcc_gc_index_py_remove` — on remove,
close the probe-chain gap by shifting cluster entries whose home lies
cyclically outside (hole, probe]; no tombstone state is ever written, so
`used == count == live`, the insert gate only fires on real growth, and
steady-state churn performs ZERO rehashes. `find_slot` drops the
`first_deleted` branch in both tiers. The generic remove gained a
`used_cell`/`used` parameter (decrement on delete); all 8 instance wrappers
and 4 C call sites updated. Safety audit: no engine caller iterates raw
slots across removes (enumeration everywhere walks node lists), so shifting
slots cannot skip entries for any of the 9 index instances.

Post-shift longrun (same pinned workload):

```text
GC4 3 runs: 330,595 / 321,333 / 319,233  -> median 321,333 ops/s (+9% over
            post-hash; 8.0x the 08-03 baseline), zero drift, RSS 9.09MB
            (down from 9.50MB), zpage retained gap unchanged at 508,840.
GC0-3:      604,514 / 383,739 / 376,891 / 369,110 — no regressions.
```

Gates: `test_freestanding_gc_index_table.py` 5 passed (includes the C-vs-port
differential run), `test_gc_backend_generational.py` 80 passed (the
tombstone-design pin updated to pin gap-free backward-shift instead),
freestanding GC battery 197 passed (7 failures pre-existing, see below), and
the GC4 full bootstrap gate re-run at this tree state.

Attribution guard for the pre-existing reds hit while gating: GC4
`test_gc_trashcan.py` segfaults and GC3 `gc.collect()` undercounts reproduce
IDENTICALLY with the HEAD engine swapped back in (control rebuild of
`libpy_runtime_pcc_py.a`), the crash cycle contains only
trashcan/dealloc/`__del__` frames (stack-scrape via lldb), and GC1/GC3 pass
the same trashcan binary — separate investigations:
[gc4-trashcan-del-chain-dealloc-recursion-overflow.md](gc4-trashcan-del-chain-dealloc-recursion-overflow.md),
[gc3-cycle-collect-undercount-10k-cycles.md](gc3-cycle-collect-undercount-10k-cycles.md).

NOT fixed (recorded, needs owner decision): `zpage retained gap` measures
508,840 vs the 08-03-pinned 504,992 (structure: one extra 4KB zpage page +
248B live delta at the final sample). It is deterministic across every run,
identical before and after both engine slices, so it predates the engine
work — left for the PERF-P1-GC4-FREESTANDING-LONGRUN owner decision rather
than silently re-pinning the threshold.
