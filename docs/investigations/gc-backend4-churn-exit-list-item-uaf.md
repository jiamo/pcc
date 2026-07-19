# Investigation: backend #4 intermittent exit-time SIGSEGV — stale list item pointer under instance churn

## Status
RESOLVED 2026-06-13 (user-directed overnight surgery) — the FULL
five-GC bootstrap matrix is green (gc0/gc1/gc2/gc3/gc4 each 3-stage,
byte-verified; gc4 gate 175s vs the historical ~310s), smoke 4x5 green
incl. [4-churn], churn/growshrink crash-free at every scale
(0/16, 0/10, 200k-round exit=0), and gc4 churn-20000 wall fell from
142.5s to 7.1s (port tier). THREE stacked root causes, fixed in both
tiers:

1. TRASHCAN x ZPAGE ORDERING (the original killer behind every
   trash_drain backtrace): an object's zpage accounting is decremented
   in note_object_freeing BEFORE its dealloc walk runs; the trashcan
   defers that walk, so a same-cascade death on the same page can
   recycle+memset the span first, and the deferred py_dealloc_*
   then reads its own header/fields from reset memory. FIX:
   zpage-resident objects bypass the trashcan (immediate dealloc).
2. LAZY-HEAL PROTOCOL (No.10): refcount migration could not bound
   stale-pointer lifetime. FIX: count-on-NEW accounting
   (relocate_copy transfers the outstanding count; old copies are
   immortal shells; py_incref/py_decref resolve-first on candidates),
   a remap phase (objects+frames+roots) triggered at drain-empty with
   a >=4096 forwarding-population threshold, retirement one epoch
   late (RETIRING flag) and page destruction one further epoch late
   (parked pages), forwarding nodes carrying their from-page for O(1)
   retirement accounting.
3. PAYLOAD-SPAN GLOBAL LIST (the 142.5s wall + the stage2 timeout):
   one global span list walked O(N) on EVERY object death AND every
   registration went quadratic once containers registered spans
   (95% of samples). FIX: per-owner span chains hung off the zpage
   owner node (O(own) register/remove/query; port node grew 64->72,
   chain head at +64 — +48/+56 are the page links, a collision the
   first attempt hit and instantly exposed).

Also fixed en route: C-only declaration-order break of the cc-tier
archive build (caught by the C-harness gates), and the cc tier's
O(N) index-miss fallbacks in is_known_object/zpage_remove (258s ->
118s; the cc tier keeps a residual secondary hot spot, recorded as
follow-up — the port tier, which the bootstrap uses, is at 7s).

## Update 2026-06-13 — macOS LC_UUID plus compiler-scale zpage span retention

A later same-day regression looked like a unified "GC bootstrap is red"
failure across gc0/gc3/gc4, but it split into two boundaries:

1. **Darwin loader policy changed under us.** gc0 and gc3 failed at the
   `pcc2 --help` smoke barrier with `dyld: missing LC_UUID load command`.
   The compiler had been linking Darwin executables with `-Wl,-no_uuid` for
   byte-stable bootstrap comparison. Current macOS rejects those binaries.
   Fix: keep normal Mach-O `LC_UUID` at link time and normalize UUID bytes only
   in compare-time copies. Evidence: gc0 full bootstrap `1 passed in 60.21s`;
   gc3 full bootstrap `1 passed in 83.08s`.
2. **gc4 still had a real stale-span/free failure.** A single stage2 export
   worker reproduced under `PCC_GC_BACKEND=4` and passed under backend #0.
   LLDB showed both forms of the same ownership hole: `py_instance_dealloc ->
   pcc_gc_free_object_memory` handed a zpage-span address to libc `free`, and
   malloc also tripped inside `_backend4_zpage_reset` after the allocator
   freelist was corrupted.

Two narrow protocol repairs landed in both tiers:

- Before `_backend4_zpage_remove`, `pcc_gc_note_object_freeing` restores the
  `ZPAGE_ALLOC` allocation-origin bit when the still-live zpage owner index
  proves the address belongs to a zpage. This prevents later free-time logic
  from mistaking a zpage object for malloc memory after the owner mapping has
  been removed.
- Backend #4 release/load/store paths no longer rely on a stale old-copy
  header before attempting relocation resolution. For count-on-NEW, slot heals
  rewrite the pointer without doing `incref(new) / decref(old)`.

The compiler-scale failure still required a conservative reclamation boundary:
backend #4 no longer returns zpage spans to libc during reset/destroy. ZPage
descriptors and spans are retained for backend-local reuse so stale old-copy
pointers cannot turn into malloc heap corruption. This is correctness-first and
is deliberately **not** a GC performance win; the follow-up is a real
remap/epoch proof that can safely recover memory.

Validation:

```text
env -u LC_ALL PCC_GC_BACKEND=4 bash scripts/bootstrap.sh --backend self \
  --out-dir /tmp/pcc_gc4_patch4_bootstrap
  -> pcc2/pcc3 metadata-normalized byte-identical

env -u LC_ALL uv run pytest tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0
  -> 1 passed in 140.87s

env -u LC_ALL uv run pytest tests/python/gc/test_pcc_bootstrap_full_gc0.py -q -n0
  -> 1 passed in 60.21s

env -u LC_ALL uv run pytest tests/python/gc/test_pcc_bootstrap_full_gc3.py -q -n0
  -> 1 passed in 83.08s
```

## Update 2026-07-02 — focused exit-UAF characterization gate

Concurrent-wave Worker B follow-up for the stale task-board finding added
`tests/python/test_gc_backend4_exit_list_item_uaf.py` as a focused
characterization/regression for this investigation's original failure boundary.
The test compiles only `benchmarks/python/longrun_churn.py` in strict
no-libpython self-backend mode (`backend=self`, `python-libpython=off`,
`ir-scaffold=on`) and then runs that binary three times with
`PCC_GC_BACKEND=4` and `600` rounds.

The assertion is intentionally mode-labeled: a non-zero exit after a `done,`
line fails with an explicit "backend4 exit-list-item UAF boundary" message, so
the old exit-time stale list-item/list-object shape is visible without reading
the broad 4x5 longrun smoke matrix. This wave did not run the test, did not
change backend/runtime source, and does not claim `G-P0-LONGRUN` completion.

## 2026-06-12 bisection narrative (compiler-scale regression)

The gc4 full bootstrap gate was green at 07:06 (pcc3 artifact) and is red
with the day's worktree. One-variable-at-a-time results (caches were a
trap: the gate reuses `build/bootstrap-pytest-shared-stage1/pcc1` and a
low-IR-keyed object cache — delete the shared dir to force a true
rebuild; the object cache keys on generated IR text so codegen edits DO
invalidate it):

```text
codegen leak fixes only (morning pcc1, no spans/memset):
  compiler-scale compile under gc4 -> workers SIGSEGV   [reproduces]
+ pins around py_dict_set                               [DENIED — still fails;
  worse, pins re-broke the cured churn_bare: pcc_gc_pin writes header
  flags through a possibly-already-forwarded pointer (concat's pin
  precedent pins a JUST-ALLOCATED result, which cannot be stale)]
+ spans + memset (full runtime mirrors)                 [still fails at
  compiler scale; spans DO cure churn_bare]
gc0 / gc3 full gates with the same final tree           [GREEN]
```

Mechanism picture: the dict-literal/str() ownership leak fixes are
semantically correct (CPython frees these temps; gc0-3 + default
bootstrap + pcc2/pcc3 byte-identity all green) and act as an AMPLIFIER —
they massively increase release/dealloc traffic on relocatable objects,
which the port-tier backend-4 relocation machinery cannot yet absorb.
Backing them out would re-green gc4 by reintroducing a real leak; that
tradeoff is surfaced here deliberately and NOT taken (north-star: do not
weaken semantics to localize a backend failure).

## Mirror-drift FIXED: container payload-span registration (port tier)

C containers register out-of-line payload arrays with the zpage owner
index; the pcc-Python ports (linked by DEFAULT mode) never did:

```text
C sites: py_list.c (new + grow), py_dict.c (alloc_tables + rehash),
         py_set.c (alloc_entries)
port:    NO call sites at all (the port py_gc_backend implements the
         registry; the port containers never invoked it)
```

Fixed by mirroring all five sites into `py_list.py` / `py_dict.py` /
`py_set.py` (extern `pcc_gc_backend4_zpage_register_owner_payload_span`).
Also mirrored C's calloc semantics in port `pcc_gc_alloc`'s malloc
fallback (`memset 0` — fresh objects are GC-visible before their
constructor fills the body). Result: churn_bare (no dict, no dynamic
strs) 5/6 -> 0/12 clean; full churn still crashes at 600 rounds.

## Problem Description

`tests/python/test_longrun_smoke.py::test_longrun_smoke_all_backends[4-churn]`
fails intermittently (~2/3 of runs): the churn workload completes ALL work
(prints its final `done,38400` line) and then dies with SIGSEGV during
exit-time teardown, on `PCC_GC_BACKEND=4` only (gc0-3 are 0/6 clean at the
same bound).

## Crash fingerprint (LLDB, 2026-06-12)

```text
stop reason = EXC_BAD_ACCESS reading [x19 + 0xc]   ; header flags load
x19 = 0x0000a6ee49c98760                            ; unmapped / garbage

frame #0: user_py_obj__gc_relocation_candidate + 36
frame #1: pcc_gc_load_ptr + 116
frame #2: py_dealloc_list + 120
frame #3: user_py_obj_dealloc__dealloc_dispatch + 372
frame #4: user_py_obj_dealloc__trash_drain + 72
frame #5: pcc_dealloc_with_trash + 136
```

Reading: at exit, a list is deallocated through the trashcan path;
`py_dealloc_list` loads an item slot via the read barrier; the loaded item
pointer is garbage (not tagged — it passed the tagged-int short-circuit), so
the barrier's relocation-candidate check dereferences an unmapped address.
The slot held a stale (pre-relocation or freed-and-reused) pointer.

## Repro

```bash
# Workload variant with extern sampling but no dict and no dynamic strs
# in the hot loop (see "variant matrix" below for how it was derived):
env -u LC_ALL uv run pcc --backend self --python-libpython=off \
  --ir-scaffold=on /tmp/churn_bare.py -o /tmp/churn_bare
for i in 1 2 3 4 5 6; do PCC_GC_BACKEND=4 /tmp/churn_bare 600 >/dev/null 2>&1 \
  || echo crash; done           # ~5/6 crash
# The committed benchmark reproduces identically:
#   PCC_GC_BACKEND=4 <compiled benchmarks/python/longrun_churn.py> 600
```

## Test [CONFIRMED]

Observed 2026-06-12 under
`env -u LC_ALL uv run pytest tests/python/test_longrun_smoke.py -q -n0`
(`[4-churn]` failed, returncode -11 after the final `done,` line) and under
the direct loop above (4-6 crashes out of 6 across variants).

## Update 2026-06-12 — not churn-specific

The post-fix re-measure reproduced the same exit-time SIGSEGV on the
GROWSHRINK workload too (`PCC_GC_BACKEND=4 <growshrink> 400` →
exit 139 after the final sample; churn 200k rounds → exit 139 after
`done,12800000`). So the trigger is gc4 exit teardown under
workload-scale heaps generally, not the churn shape per se. Both runs
completed all work and produced full CSV output first.

## Variant matrix (what the crash does and does not need)

All compiled strict no-libpython self-backend the same day, 6-8 runs each on
`PCC_GC_BACKEND=4`, 600 rounds:

```text
full churn benchmark (dict + tag concat + extern sampling)   6/6 crash
- dict value str -> int  ({"a": idx, "b": idx})              6/6 crash
- tag concat -> constant ("n")                               5/6 crash
- no dict literal at all                                     4/6 crash
- no dict AND no tag concat (churn_bare)                     5/6 crash
ordinary module, ring+Node+items only, no extern/str/print   0/8 clean
isolated probes (64-slot ring, single allocation kinds)      0/5 clean each
```

So: the 2026-06-12 dict-literal temp-release and raw-scaffold str() ownership
fixes are NOT required for the crash (it fires with no dict literal and no
dynamic str in the loop), and the backtrace contains no release path — the
faulting load is a list-item read during exit dealloc. The pure-Python
variant being clean while the extern-sampling variant crashes means the
trigger is layout/timing sensitive (extern module shape, sampling-time
allocation bursts), consistent with an intermittent relocation bookkeeping
hole rather than a frontend ownership bug.

One green pre-fix smoke sample existed (20/20 earlier the same day), but at
a ~2/3 crash rate a single passing sample is consistent with luck; treat the
bug as plausibly predating the day's frontend fixes and as belonging to the
backend-4 relocation rework arc.

## Proposals
- No.1 Instrument py_dealloc_list's barrier path (or run with a zpage poison/verify mode) to capture WHICH list and WHICH slot holds the stale pointer (ring list of Node* vs an items list), then audit the relocation slot-update path (store buffer / remembered set / zpage forwarding) for the missing update against docs/refs_docs/gc-research/zgc   [pending]
- No.2 Check the exit ordering: module/global teardown vs zpage release — whether the list's items were freed by zpage teardown before the list's own dealloc walks them   [pending]
- No.3 Register container payload spans in the port containers (mirror the five C sites)   [CONFIRMED partial — cures churn_bare 5/6 -> 0/12; full churn + compiler scale still red]
- No.4 Pin/unpin owned dict-literal temps across py_dict_set   [DENIED — pin writes flags through possibly-forwarded pointers; re-broke churn_bare and did not fix the bootstrap gate; removed]
- No.5 Diff the port-tier relocation step / zpage healing / forwarding lifecycle in py_gc_backend.py against py_gc_backend.c at the points the parallel rework touched (the cc-tier is clean, so the defect is port-resident or port-exercised-only); owner: backend-4 relocation/zpage rework   [DONE 2026-06-12 — relocate-copy / zpage-remove / recycle / destroy are STRUCTURALLY MIRRORED across tiers; the defect is a design invariant, not port drift: see No.6]
- No.7 Deferred-destroy IMPLEMENTED both tiers (PccGcZPage.pending_forwardings + zombie; port page grows 96->112 with offsets 96/104; install_forwarding increments via owner index gated on ZPAGE_ALLOC; forwarding_remove/remove_target decrement via addr-span lookup; zpage_remove defers recycle while count>0, zombie pages stay addr-lookupable and destroy at last decrement)   [DENIED as cure — churn-600 still 8/12. Trap probe (abort-if-destroy-with-counter>0) NEVER fired: counter-guarded destroys are clean, so the surviving stale references have NO live forwarding entry — they OUTLIVE forwarding. KEPT as hardening: closes one real destroy-while-forwarded window; gc0+gc3 full gates + 5-GC regressions green with it]
- No.8 Header-clobber residency bug FOUND+FIXED both tiers [real bug, also DENIED as cure]: relocate_copy's header memcpy/memmove overwrites `to`'s flags with `from`'s, losing `to`'s true ZPAGE_ALLOC/MINOR_ARENA bits (chained relocations then undercount pending_forwardings; reverse direction mis-frees/leaks). Fixed by capturing to-residency bits before the copy and restoring after.
- No.9 REFRAME from the surviving crash backtrace: the dying list's items are TAGGED INTS (cannot be heap pointers) — the garbage is the LIST OBJECT REFERENCE itself (trashcan queue / instance field holding a stale pre-relocation address whose old copy's page is gone; py_dealloc_list then walks junk length/items). Combined with No.7's trap evidence: the lazy-heal protocol CANNOT bound stale-reference lifetime — references can outlive both the old copy's refcount and its forwarding entry. CONCLUSION: patch-scale fixes are exhausted; backend 4 needs a real heal/remap PHASE (ZGC reference: relocated pages are freed only after the remap phase rewrites all references) before forwarding-drop and page-destroy. Route to the backend-4 rework owner with this file.
- No.6 ROOT CAUSE [CONFIRMED 2026-06-12]: lazy-heal vs page-destroy invariant violation. There is NO global heal/remap pass in either tier — slots heal lazily via the read barrier, which must READ THE OLD COPY'S HEADER (`_gc_relocation_candidate` loads flags) to decide to resolve forwarding. But `relocate_copy` calls `zpage_remove(from)` at COPY time (decrementing the old page's object_count), so once the last object evacuates, the old page recycles — and class>1 pages (or freelist overflow) hit `zpage_destroy`, which `free()`s the span; macOS returns large blocks to the OS, so every later barrier touch of an un-healed slot faults. PROBE: making port `_backend4_zpage_destroy` leak instead of free -> churn gc4 10/10 crash -> 0/10. The same structure exists in the C tier (latent; cc-mode just didn't hit the timing in 8 runs). FIX DESIGN (next slice, both tiers): per-page `pending_forwardings` counter + `zombie` flag — increment at `install_forwarding` (page found via owner index, still present at install time), decrement at `forwarding_remove` (page found via address-span lookup, e.g. `zpage_owns_addr`, so zombie pages must stay reachable by that lookup); `zpage_remove`'s recycle condition gains `pending_forwardings == 0`, pages failing it become zombies (unlinked from alloc paths, kept for addr lookup) and are destroyed when the last forwarding entry into them is removed. Page structs are tier-local layouts, so each tier adds its own field. Gates: churn/growshrink gc4 10x, smoke 4x5, gc4 + gc0 full bootstrap, five-GC matrix before claim upgrade.   [design ready — IMPLEMENT NEXT]

## No.10 Protocol audit (2026-06-12 PM, desk derivation — why patch-scale attempts keep failing)

The relocation protocol migrates REFCOUNTS lazily along with slot
heals, and that model is inconsistent in three independent places; the
combination explains every observed crash signature without needing
new code suspects:

1. MIXED RELEASE CONVENTIONS. After `relocate_copy`, the outstanding
   refcount stays on the OLD copy (`to->refcount = 1` = the forwarding
   node's own ref); each slot HEAL moves one count old->new
   (incref new / decref old). An owner whose reference is still
   accounted on OLD must therefore decref OLD on release. But the two
   release paths disagree: raw `py_decref(o)` decrements the pointer
   it is GIVEN (old — consistent), while `pcc_gc_release(o)`'s
   backend-4 branch RESOLVES first and decrements NEW (both tiers,
   py_obj.py:299-301 / py_obj.c:270-273) — for a stale-pointer release
   that is a count NEW never received. NEW can reach zero while
   reachable (premature free -> the stale LIST OBJECT reference of
   No.9), and OLD keeps a phantom count (never drains -> forwarding
   never removed -> with No.7's deferral, zombie pages accumulate,
   feeding the 74-108MB capacity retention and possibly the wall-time
   pathology below).
2. SSA-HELD OWNED REFS CANNOT BE HEALED. Heals only rewrite SLOTS the
   barrier walks. A reference created before relocation and held in a
   register/alloca (straight-line owned temp: alloc -> safepoint
   relocates it -> release) is accounted on OLD with stale bits and no
   slot to heal. Any convention that decrefs NEW for these corrupts;
   any that decrefs OLD requires OLD's dealloc to be payload-safe and
   forwarding-safe (it is neither: note_object_freeing(old) REMOVES
   the forwarding entry while other unhealed slots still need it).
3. BORROWED STALE POINTERS HAVE NO COUNT AT ALL. A borrowed load taken
   before relocation survives in a register across the safepoint; once
   OLD legitimately drains to zero (all owned refs healed/released),
   OLD is freed and the borrow's later barrier check reads freed
   memory. Refcounts CANNOT express "a borrow window is open"; only
   pinning, an epoch/handshake, or a full remap phase can.

Consequence: single-knob fixes are structurally insufficient — flipping
the release convention, pinning at one call site (No.4), or deferring
page destruction (No.7) each just moves which class fires. The
redesign requirements DERIVED from this audit:

```text
R1  A real heal/remap PHASE: old copies and forwarding entries may
    only be dropped after a pass proves no stale refs remain
    (ZGC: relocated pages free only after remap).
R2  ONE release convention, chosen to match R1's accounting (simplest:
    refcount lives on the NEW copy from install time; heals only
    rewrite bits; releases always resolve; old copies carry no count).
R3  Borrow windows need protection independent of refcounts
    (safepoint-scoped pinning or epoch-based page reclamation).
R4  addr->page and per-page forwarding accounting must be hash-indexed
    (the O(pages) walks are already pathological at churn scale).
```

## Update 2026-06-12 PM — wall-time pathology (measurement input for the redesign)

The quiet-host manual-tier re-run (fixed compiler) shows backend 4
wall-time collapsing at churn scale: churn-20000 takes 142.5s on gc4
vs 2.8-4.6s on gc0-3 (pre-fix gc4 took 10.2s at the same bound), and
growshrink-400 takes 74.7s vs 1.2-1.9s. Candidate causes, not yet
adjudicated: (a) the same-day deferred-destroy hardening performs an
O(pages) address-span walk on EVERY forwarding removal, and the leak
fixes massively increased object death (hence removal volume); (b)
relocation traffic itself. gc4 also holds 74-108MB heap capacity at
7-10MB in-use in the same runs (zombie/free page retention). The
heal/remap-phase redesign should treat per-page forwarding accounting
and addr->page lookup as first-class index needs (hash, not list walk)
— same lesson as the frame-index history
(`gc-frame-index-entry-pool-perf.md`).

## Update 2026-07-02 — read-barrier candidate check made dereference-safe (G-P0-LONGRUN)

Concurrent-wave G-P0-LONGRUN slice. The surviving-crash backtrace (No.9) and
the crash fingerprint (fault at `_gc_relocation_candidate + 36` reading
`[x19 + 0xc]` = header flags, `x19 = 0x0000a6ee49c98760`, a plausible-looking
but genuinely-unmapped address that passed the `_ptr_can_have_header` heuristic)
both point at ONE mechanical fault: the backend-4 read barrier
(`pcc_gc_load_ptr` / `pcc_gc_load_borrowed_ptr`) decided "does this slot value
need relocation resolution?" by loading the value's header flags directly
(`py_gc_relocation_candidate` -> `py_header_flags_load`). Under exit-time churn
a slot can hold a STALE reference — a freed malloc'd child, or an old copy the
object index / forwarding table never mapped — and the address heuristic cannot
tell that apart from a live object, so the header load faults.

Fix (correctness-first, both tiers; does NOT weaken relocation semantics):
route the backend-4 candidate decision through a new helper
`pcc_gc_backend4_slot_needs_resolve` (`py_gc_backend.c` +
`py_gc_backend.py` mirror, declared in `py_internal.h`). It consults the
forwarding table and object index — both pointer-VALUE hash lookups that never
dereference the pointer — FIRST, and only reads the header of a proven-mapped
(known-live) object:

```text
forwarded stale ref  -> resolve (forwarding table already heals it, no deref)
known-live object    -> safe to read the 0x800 candidate flag (mapped)
unknown & unforwarded -> dead pointer leaked into a slot: do NOT dereference,
                         return 0 (leave as-is), bounding the No.10 case-3
                         stale-borrow window without a fault
```

The resolution path (`pcc_gc_note_relocation_read` / `_note_relocation_read_unlocked`)
was already dereference-safe for unknown-but-forwarded pointers; the only
unsafe step was the fast-path candidate header deref used to gate it. The
minimal shared-file edit is in `py_obj.c` / `py_obj.py` (backend-4 branch of
both load barriers only; the generational/backend-3 branch and the store
barrier are unchanged so the tiers stay mirrored). The frontend needs no
change: slot reads lower to a `pcc_gc_load_ptr` runtime CALL
(`runtime_abi.py`, `name_lowering.py`), not an inlined candidate check.

Regression: `tests/python/test_gc_backend4_longrun_exit_uaf_regression.py`
compiles `benchmarks/python/longrun_churn.py` strict no-libpython self-backend
and runs it 8x under `PCC_GC_BACKEND=4` at 600 rounds, requiring every attempt
to reach `done,38400` and exit 0 (CPython reference: `done,38400`, exit 0). The
higher attempt count guards against the historical ~2/3 intermittent crash
masking as a lucky single pass. This wave authored the fix + regression; the
external tester runs the gate (archives wiped so the C-tier edits rebuild).
Not claiming full `G-P0-LONGRUN` closure until the gate + 4x5 smoke +
five-GC bootstrap matrix are confirmed green by the tester.

## Notes

Predecessors: `gc-backend4-list-relocation-owned-items.md` (resolved; item
ARRAY ownership on relocation copy — different slice),
`gc-backend4-dict-relocation-owned-tables.md`,
`gc-backend4-instance-relocation-owned-fields.md`.
The longrun smoke gate `[4-churn]` stays red until this closes — do not
weaken the gate to green it.
