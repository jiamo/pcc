# Investigation: GC backend selection matrix

## Status

resolved

## Problem Description

`goal.md` No.40 requires a final five-backend GC comparison, a default-backend
decision, rollback guidance, and consistent documentation. The risk is choosing
a non-default backend because it has impressive focused gates while it still
lacks the boring evidence that backend #0 has as the long-running reference
path.

## Context

pcc has five runtime-selected GC backend slots:

- #0: refcount + stop-the-world cycle collector, CPython-shaped reference path.
- #1: Lua-style incremental tricolor mark-sweep.
- #2: Go-style concurrent mark-sweep direction.
- #3: OCaml-style generational young/old direction.
- #4: modern GenZGC-style colored relocating direction.

The current implementation has moved well past smoke tests. Backends #1-#4 now
have executable correctness and stress slices, pcc1 selection coverage, and
runtime mirror coverage in the areas that have been implemented. They are still
not equal algorithmic ports of Lua, Go, OCaml, or ZGC.

## Evidence

Fresh gates from the No.40 closure pass:

- `tests/python/test_pcc1_gc_backend_matrix.py`: `20 passed in 12.86s`.
- `tests/python/test_gc_backend_concurrent.py` under `PCC_GC_BACKEND=2`:
  `6 passed in 25.34s`.
- `tests/python/test_gc_concurrent_collection.py` under `PCC_GC_BACKEND=2`:
  `11 passed in 84.52s`.
- `tests/python/test_gc_threading_substrate.py` under `PCC_GC_BACKEND=2`:
  `16 passed in 10.46s`.
- Backend #3 class metadata slot rewrite, C runtime and pcc-Python mirror:
  `3 passed in 38.97s`.
- `tests/python/test_gc_backend4_production.py` under `PCC_GC_BACKEND=4`:
  `110 passed in 474.71s`.
- `tests/python/test_gc_backend_under_env.py -k 'gc_backend4_production and llvm'`:
  `1 passed, 127 deselected in 458.97s`.

Recorded gates from the same 2026-05-17 closure batch:

- coroutine/scheduler roots across backend 0..4:
  `tests/python/test_gc_coroutine_roots.py`,
  `tests/python/test_gc_coroutine_scheduler_roots_production.py`, and
  `tests/python/test_virtual_threads_gap.py`: `17 passed in 135.26s`.
- self-backend full bootstrap:
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`:
  `1 passed in 69.89s`.
- LLVM C API parity:
  `tests/c/test_llvm_capi_ir_parity.py`: `17 passed in 0.22s`.

The full `test_gc_backend_under_env.py` wrapper was not used as a single
all-or-nothing result because it takes tens of minutes and previously had a
target timeout shorter than backend #4's production file runtime. The wrapper
was fixed to use a 600s timeout for that known-slow target, and the slow target
was verified through the wrapper.

## Ranking

1. **Backend #0 remains the default.** It is the reference path, has the
   broadest real bootstrap and language coverage, and has the least policy
   uncertainty. It is not the best long-term pause-time design, but it is the
   safest default for users and for bootstrap.
2. **Backend #1 is the best near-term non-default candidate.** It has the
   simplest non-refcount algorithmic surface and good correctness gates. It
   should compete for default only after pacer/debt/finalizer/resurrection
   audits show clear benefit without destabilizing #0.
3. **Backend #3 is the best medium-term throughput candidate.** Minor arenas,
   copy-oldification, owned-slot rewrite, frame/scheduler roots, and class
   metadata slot rewrite are now covered in both C and pcc-Python runtime
   paths. It still needs broader pcc-Python threaded object-graph and
   cross-domain remembered-set proof before default consideration.
4. **Backend #2 is a threaded correctness candidate, not the default.** The
   current CMS worker, queue, mutator assist, lifecycle, and TSan slices are
   valuable. It still lacks a full Go-style work-buffer/drain model and
   concurrent span/object sweep policy, so it is not a general default.
5. **Backend #4 is the long-term low-pause candidate.** It has the widest
   advanced surface: forwarding, read barriers, container relocation,
   scheduler-root healing, store-buffer telemetry, page-class telemetry, and
   first large-page policy slices. It remains too complex and incomplete for a
   default because true page evacuation, full GenZGC young/old policy,
   fragmentation policy, native-handle protocols, and pcc-Python threaded mirror
   flushing are still open.

## Default Decision

Keep backend #0 as the default.

This is an explicit No.40 decision, not a deferral. The matrix currently says
the safe production default is #0, while #1 and #3 are the realistic future
default challengers. #2 and #4 should continue as selectable advanced backends
with focused production-facing gates, not as default candidates yet.

## Rollback Strategy

No default change is made, so rollback is the existing path:

- unset `PCC_GC_BACKEND`, or set `PCC_GC_BACKEND=0`;
- keep backend #0 bootstrap and fallback gates green whenever another backend
  changes shared runtime code;
- if a future winner changes the default, CI must run both the new default and
  `PCC_GC_BACKEND=0` reference gates until the new default has at least one
  release cycle of evidence.

## Failure Modes Found During Closure

- backend #3 pcc1 crashed in `IRBuilder.call` when `_opname_of()` allocated a
  short `"call"` slice that lived in long-lived metadata. Returning stable opcode
  literals fixed the backend #3 pcc1 matrix failure.
- backend #2 lifecycle and threading probes had timing or link assumptions that
  predated virtual-thread dependencies in `pcc_threads.c`; the tests now link the
  threaded runtime archive and wait through safepoints.
- backend #3 class metadata promotion missed borrowed `methods[i].func` and
  `del_method` slots; C runtime and pcc-Python mirror now promote those borrowed
  metadata slots without adding them to backend #4's generic trace surface.
- backend #4 wrapper evidence was blocked by a too-short 240s subprocess timeout;
  backend #4 production currently needs roughly 7-8 minutes on this machine.

## Backend Backlog

- **#0:** auto-pacing for cycle collection; deeper weakref/finalizer/resurrection
  policy parity; keep it as the non-regression reference for every shared edit.
- **#1:** finish pacer/debt tuning, long-chain release performance, finalizer and
  resurrection stress, and broader real-program pause/RSS measurement.
- **#2:** replace the conservative queue with a fuller Go-style work-buffer and
  drain model; decide whether sweep stays STW or grows into concurrent
  span/object sweeping; keep TSan and lifecycle gates mandatory.
- **#3:** broaden pcc-Python threaded object-index/object-list synchronization,
  cross-domain remembered-set sharing, and workload-level throughput/RSS data.
- **#4:** implement true ZPage allocation/evacuation, complete young/old GenZGC
  policy, fragmentation-driven relocation, native-handle protocols, and
  pcc-Python threaded mirror flushing.

## Outcome

No.40 is closed for default selection: backend #0 remains default, with a
documented full ordering and rollback policy. The algorithm-specific backlogs
above remain active backend-improvement work, not blockers to the default
decision.
