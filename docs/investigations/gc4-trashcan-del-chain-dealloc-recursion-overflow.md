# Investigation: GC4-only stack overflow — trashcan fails to defer the `__del__`-chain dealloc cascade (test_gc_trashcan segfaults under PCC_GC_BACKEND=4)

## Status
resolved (crash) — root cause found and fixed 2026-08-07 late session: the
trashcan defer gate EXCLUDED zpage-resident objects
(`(header.flags & 0x10000) != 0` skipped `_trash_enqueue` in
`py_obj_dealloc.pcc_dealloc_with_trash`), so under PCC_GC_BACKEND=4 every
deep dealloc cascade recursed directly and overflowed the stack. The
exclusion guarded a real recycle UAF; the fix closes that UAF at the source
instead (deferred page recycles while a cascade is active) and lets zpage
objects defer like every other object. All four
`tests/python/test_gc_trashcan.py` no-overflow tests now pass under GC4
(37 passed across trashcan+effectiveness, was 4 segfaults); the RSS-plateau
effectiveness failures and the cycle-collect undercount remain as separate
pre-existing rows. See the Fix section below for the residual performance
notes (GC4 longrun 321k -> 257k ops/s, still above the 240k threshold).

## Problem Description
All four `tests/python/test_gc_trashcan.py` no-overflow tests segfault under
`PCC_GC_BACKEND=4` (returncode -11); the same binaries pass under backends
0, 1, and 3. The trashcan exists precisely to make deep dealloc cascades
iterative, so an overflow here means the defer decision never engages on
the backend-4 path. `tests/python/test_gc_effectiveness.py` RSS-plateau
failures under GC4 (peak ~1.0-1.5GB vs the 200MB post-fix expectation
documented in the test) are likely the non-crashing face of the same family.

## Repro
```bash
# program: 100k-node linked list, every node has __del__ (the
# test_trashcan_with_del_no_overflow body verbatim)
env -u LC_ALL uv run python -c "from pcc.py_frontend.pipeline import \
  compile_python; compile_python('prog.py','prog.out',ir_scaffold_mode='on')"
PCC_GC_BACKEND=4 ./prog.out   # SIGSEGV (rc 139) in ~1s
PCC_GC_BACKEND=3 ./prog.out   # rc 0
PCC_GC_BACKEND=1 ./prog.out   # rc 0
```

## Test [CONFIRMED]
Observed 2026-08-07 via the pytest gate (`PCC_GC_BACKEND=4 ... -n0
tests/python/test_gc_*.py`: 4 trashcan + 3 effectiveness + 2 performance +
1 regression failures) and via the standalone repro above.

Crash forensics (lldb): EXC_BAD_ACCESS code=2 (stack guard page) with the
deepest frame in an unrelated leaf (`pcc_gc_index_py_insert` prologue).
The default unwinder cannot walk the prologue frame; a stack scrape
(read qwords above the guard page, symbolicate text-segment addresses)
exposes the unbounded recursion cycle:

```text
py_decref -> pcc_dealloc_with_trash -> user_py_obj_dealloc__dealloc_dispatch
  -> py_instance_dealloc -> py_user_del_dispatch
  -> user_py_dunder__call_user_unary_method(_void)   (__del__)
  -> py_dealloc_tuple -> py_decref -> ...
```

No index-engine function appears in the cycle. Frequencies over the scanned
region: py_decref 255, pcc_dealloc_depth 170, pcc_dealloc_with_trash 128 —
i.e. `pcc_dealloc_with_trash` runs every iteration but never defers.

## Attribution controls (all run 2026-08-07)
- HEAD engine control: swapping `py_gc_index_table.c` +
  `freestanding_gc_index_table.py` back to HEAD content and rebuilding
  `libpy_runtime_pcc_py.a` reproduces the identical segfault → predates the
  backward-shift engine change.
- Same binary passes under GC1/GC3 (heavy index users) → backend-4-specific.
- GC4 longrun churn (6.4M ops, live set 2048): zero drift, healthy RSS →
  the failure needs the deep-chain cascade shape, not remove volume.

## Proposals
- No.1 Inspect the backend-4 branch of the defer decision
  (`pcc_trash_should_defer` / `pcc_dealloc_depth` gating in the pcc-Python
  port; historical fall-through bug in the C mirror, see
  reference_cc_only_crash_check_port_mirror_drift) — instrument the
  decision inputs at depth ~1000 under GC4 vs GC3.        [pending]
- No.2 Check whether GC4's relocation/zpage guards route dealloc through a
  path that bypasses `pcc_dealloc_with_trash`'s queueing (the cycle shows
  the wrapper present but never terminating the recursion). [pending]

## Fix (landed 2026-08-07 late session)
Root cause: `pcc_dealloc_with_trash` skipped `_trash_enqueue` for objects
with header flag 0x10000 (PY_FLAG_GC_ZPAGE_ALLOC) — a guard against a real
UAF (zpage accounting is decremented in note_object_freeing BEFORE the
deferred dealloc runs, so a same-cascade page recycle could reset the span
under a queued object). Under GC4 every instance is zpage-resident, so the
guard turned every deep cascade into unbounded recursion.

Changes (pcc-Python production port; the C oracle keeps its unreachable
exclusion with an INTENTIONAL DIVERGENCE comment since it has no zpage
allocator):
- `py_obj_dealloc.py`: zpage objects defer like everything else; new export
  `pcc_dealloc_cascade_active()`; after the top-level `_trash_drain()`,
  backend 4 runs `pcc_gc_backend4_sweep_deferred_recycles()`.
- `freestanding_gc_zpage_lifecycle.py`: `zpage_remove` defers the page
  recycle (existing flag page+104) while `pcc_dealloc_cascade_active()`;
  new `pcc_gc_backend4_sweep_deferred_recycles` walks the live page list
  after the drain and completes deferred recycles. Also DELETED the
  full node-list fallback scan in `zpage_remove` (both indexes missing
  means untracked; the scan made each object's second zpage_remove — from
  the free path after note_object_freeing already unlinked — walk every
  remaining node: O(n^2) for deep cascades).
- `freestanding_gc_forwarding_retirement.py`: the flag-104 completion via
  forwarding removal also waits for `pcc_dealloc_cascade_active() == 0`.
- `py_gc_backend.py::_backend4_zpage_list_owns_addr`: skip recycle-deferred
  pages (deferral requires count<=0 and pending<=0, so no live object can
  reside in their span).
- `runtime_abi.FREESTANDING_GC_CROSS_OBJECT_SIGNATURES`: registered
  `pcc_dealloc_cascade_active` (freestanding closure allowlist). Note:
  freestanding module functions must not carry docstrings (they emit
  managed string releases and fail PCC-PY-COMPILE-001).

Verification: 100k `__del__`-chain repro prints True on backends 0/3/4
(was SIGSEGV on 4); scaling 5k/10k/20k/40k all correct; the 1M-iteration
call-root canary still counts exactly 1,000,000 on gc0/gc4; GC0 trashcan
battery 12 passed (no cross-backend regression); GC4
trashcan+effectiveness 37 passed / 2 pre-existing RSS failures.

Performance recovery (same session, both landed):
1. **Shallow-recursion allowance** (CPython trashcan precedent, level 50):
   backend 4 defers only past depth 48; shallow cascades dealloc inline
   with zero queue traffic, restoring the cost profile the old (crashy)
   inline path had. Full deferral had cost the pinned longrun
   321,333 -> 257,452 ops/s and — far worse — pushed the GC4 bootstrap
   stage2 past its 2400s watchdog (the compiler under GC4 is one giant
   nested-dealloc workload). The recycle-UAF protection is independent of
   the threshold: page recycles defer on `pcc_dealloc_cascade_active()`
   (depth > 0) regardless of queue admission.
2. **O(1) sweep no-op**: the post-drain
   `pcc_gc_backend4_sweep_deferred_recycles` walked the whole live page
   list per top-level cascade (868/1700 samples in the longrun profile;
   116,813 ops/s). A global deferred-page counter
   (`pcc_gc_backend4_deferred_recycle_pages`, registered in
   FREESTANDING_GC_I64_GLOBALS; incremented on the 0->1 flag104
   transition, decremented by both completion paths) makes the
   no-deferral case a single load and lets the walk early-exit.

Final numbers: 100k `__del__`-chain 2.79s under GC4 (was SIGSEGV, then 58s
with full deferral); pinned GC4 longrun 308,925 ops/s, zero drift, RSS
9,093,120, gap 508,840 — back inside the pre-fix 319-330k run band.
Batteries: GC4 trashcan+effectiveness 37 passed / 2 pre-existing
RSS-plateau failures; GC0 trashcan 12 passed; index-table differential +
call-root canary 6 passed. Remaining tails (recorded in the board row):
the RSS-plateau leak family and the O(live-pages)-per-free
`_backend4_zpage_list_owns_addr` probe (~2.6s of the 2.79s teardown).

## Update 2026-08-12: source closure for the two residual mechanisms

The current production free path no longer calls the page-list ownership
probe: zpage origin is established by the header bit or the O(1) object index,
and the now-unused `_backend4_zpage_{list_,}owns_addr` helpers were removed.
The remaining RSS sink was the retained-page list itself.  `zpage_destroy`
removed pages from reuse but kept every backing span forever, even after the
forwarding retirement protocol had completed.

The source now uses an explicit two-remap quarantine in both the production
pcc-Python owner and retained C oracle.  At a remap it first physically
releases the previous retained generation, then moves the previous parked
generation into retained.  A page with any live objects, pending allocations,
or pending forwardings fails closed by staying quarantined.  Thus a retired
source span survives two complete root/referent healing epochs, while ordinary
churn can no longer accumulate spans permanently.  Focused source contracts
cover the order, invariant guards, and physical release.  This update is
implementation-only: RSS, throughput, trashcan and GC4 bootstrap gates remain
unrun, so the hypothesis is not yet CONFIRMED.

## Notes
- Two sibling symptoms recorded separately:
  [gc3-cycle-collect-undercount-10k-cycles.md](gc3-cycle-collect-undercount-10k-cycles.md)
  (GC3 `gc.collect()` undercount, also pre-existing per the same control).
- `tests/python/test_gc_codegen_write_barrier.py::test_capi_internal_owner_slots_follow_gc_slot_contract`
  fails on every backend including GC0 — separate stale source-contract
  assertion family (C-API shim commit), not this bug.
