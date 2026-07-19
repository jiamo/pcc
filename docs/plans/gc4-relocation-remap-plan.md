# Backend-4 relocation remap-phase plan (staged)

Executes requirements R1-R4 derived in
`docs/investigations/gc-backend4-churn-exit-list-item-uaf.md` No.10.
Read that audit first: the lazy-heal refcount-migration protocol is
inconsistent in three independent ways (mixed release conventions;
unhealable SSA-held refs; uncounted borrows), so no single-knob fix
works. One stage per slice, each with the standing gates: churn-600
gc4 x10, smoke 4x5, gc4 + gc0 full bootstrap, matrix before claim
upgrade. Both tiers mirror every stage (PY_MODULES discipline).

## Current building blocks (verified 2026-06-12, updated same evening)

- The C tier ALREADY has a value-flavored referent walker with full
  per-type coverage: `pcc_gc_trace_referents(o, visit)` (used by both
  the tracing mark and the promotion paths). What it lacked was a
  slot-ADDRESS flavor for rewriting — LANDED as
  `pcc_gc_update_referents(o, update)` (same switch, hands out
  `PyObject **`; declared in py_internal.h; gated by
  `tests/python/test_gc_update_referents.py` C-harness: slot counts
  for list/tuple/dict/set, in-place rewrite via a public accessor,
  non-container no-op).
- The PORT tier cannot take function pointers (pcc-Python limit, see
  py_dunder.c's header note), so it has SPECIALIZED walker copies
  (`_trace_referents`, `_trace_referents_for_promotion*`). The port's
  remap will be a third specialized copy `_remap_referents(o)` whose
  per-type coverage must mirror the C switch — landing WITH the stage-2
  driver so it is never inert-untested in the tree.
- Stage 1 as originally written ("extract the contract first") is
  therefore COLLAPSED into stage 2: C-side the contract existed, and
  the port cannot dedupe walkers without function pointers.
- Frame/root machinery already registers owned locals' slots
  (frame-index work), so a remap pass CAN reach spilled owned temps.
- The deferred-destroy hardening (pending_forwardings + zombie pages)
  is in place and keeps old spans mapped while forwarding exists —
  the remap phase replaces its "wait for refcount drain" with
  "rewrite and retire".

## Stage 1 — extract the slot-walk contract (no behavior change)

Factor the per-type slot enumeration out of the tracing switch into
`py_obj_visit_slots(o, visit_fn, ctx)` (C) and the port mirror; port
the tracing and promotion paths onto it. This is the
production-equality contract AGENTS.md already mandates, done first so
the remap pass is plumbing instead of a third hand-written switch.
Gate: five-GC matrix byte-identity (pure refactor claim).

## Stage 2 — remap pass + forwarding retirement (replaces lazy drain)

`pcc_gc_backend4_remap(void)`: at a safepoint, walk (a) all known
objects' slots via the Stage-1 contract, (b) registered roots, (c)
frame slot maps — rewriting every RELOCATION_CANDIDATE pointer through
the forwarding table WITHOUT refcount changes (bits only). After the
walk: assert/count remaining forwarding entries, drop the table,
unflag candidates, recycle zombie pages. Trigger: when zombie pages or
forwarding population exceed a threshold, or before any page destroy.
R2 lands here too: relocate_copy moves the FULL count to the new copy
at install (old becomes a count-free shell freed by page retirement),
and `pcc_gc_release`/`py_decref` adopt the single resolve-first
convention (the resolve is now transitional only: between relocation
and the next remap).

## Stage 3 — borrow-window protection (R3)

Choose per measurement: (a) safepoint-scoped pin (borrowed loads pin
the object until the next safepoint poll; cheap counter, no heap
growth), or (b) epoch-based page reclamation (pages retire two
safepoint epochs after remap). The exit-crash repro (churn-600 x10)
plus the c-testsuite docker bucket are the gates; if the Stage-2
deferral alone makes the repro 0/N, (b) is likely sufficient and
simpler.

## Stage 4 — index hygiene (R4)

addr->page lookup and per-page pending-forwarding accounting move to
hash indexes (reuse `pcc_gc_ptr_index` machinery). Measured by the
quiet-host manual tier: churn.gc4 wall must return from 142.5s to the
same order as gc0-3 (2.8-4.6s at the 20000-round bound).

## Non-goals

No finalizer/weakref semantic changes; no backend-0..3 behavior
changes (matrix byte-identity per stage); no claim of gc4 production
readiness before the full 5-GC matrix + smoke + manual tier are green
together.

## Stage-2 driver design detail (2026-06-12 evening, pre-implementation)

Derived to coding granularity, recorded for the implementing session
(GO/NO-GO assessed NO-GO for a 20-minute-paced loop iteration: the R2
accounting change rewrites py_incref/py_decref hot paths shared by ALL
backends, both tiers — it needs a fresh dedicated session).

R2 mechanics (count-on-NEW from install):
- relocate_copy: after install_forwarding succeeds,
  `to->refcount = old_outstanding + 1` (the +1 is the forwarding
  node's own reference, which install already increfs) and mark the
  OLD copy IMMORTAL (keep its RELOCATION_CANDIDATE flag — it now only
  drives resolution). Old shells are freed by page retirement, never
  by refcount.
- py_incref AND py_decref gain a backend-4 branch: if the header has
  RELOCATION_CANDIDATE, resolve through forwarding FIRST, then count
  on the resolved object. (pcc_gc_release's existing resolve-first
  becomes correct under this model; pcc_gc_retain inherits from
  py_incref.) Stray counts through stale pointers thus always land on
  NEW; immortality makes any unresolved leftover decref harmless.
- Slot heals (pcc_gc_load_ptr fast path) become BITS-ONLY: store the
  resolved pointer back WITHOUT the incref/decref pair.

Remap driver `pcc_gc_backend4_remap_and_retire_unlocked()`:
1. Walk pcc_gc_objects: for each ACTIVE node,
   `pcc_gc_update_referents(obj, heal_slot)`; heal_slot loads *slot,
   skips NULL/tagged, and if the value's header carries
   RELOCATION_CANDIDATE rewrites *slot to the forwarding target
   (bits only). Port tier: specialized `_remap_referents(o)` mirroring
   the C switch (no function pointers in pcc-Python).
2. Walk registered ROOTS and FRAME slot maps with the same heal —
   reuse the enumeration the gray-roots pass uses
   (pcc_gc_gray_mapped_roots / pcc_gc_visit_mapped_roots and their
   frame-list callers).
3. Retirement: for every forwarding entry — clear the old shell's
   CANDIDATE flag, do the object-index/node cleanup that
   note_object_freeing would have done for the shell (index remove,
   node unlink+free, live-bytes subtract), decref the forwarding
   node's `to` reference, free the entry; clear both forwarding
   indexes; then recycle pages whose object_count/pending_alloc/
   pending_forwardings are all zero (zombies included; the
   deferred-destroy counter naturally reaches zero through entry
   removal — or is bulk-cleared with the table).
4. Borrow-window caveat (R3): a borrowed stale pointer held across
   the remap safepoint still dangles once pages retire. Ship stage 2
   with retirement DEFERRED BY ONE STEP EPOCH (pages move to a
   retire-next-epoch list; actually destroyed at the NEXT remap),
   which empirically covers same-step borrows; full R3 remains its
   own stage.

Trigger points: (a) when an evacuation drain leaves the relocation
set EMPTY and the forwarding table non-empty; (b) explicit
pcc_gc_collect on backend 4. Both run under the graph lock at a
safepoint.

Gates for the stage (unchanged from the plan header) plus: the
churn-600 x10 exit-crash repro flipping to 0/10 is the success
criterion; watch churn wall-time vs the 142.5s pathology baseline
(remap should REPLACE per-removal O(pages) walks, not add to them).
