# Backend 1: the write barrier fabricated a mark cycle, so the next collect reclaimed nothing

Date: 2026-08-23

Rows: `GC-P0-BACKEND1-RESURRECTED-RECLAIM-LAG` (closed),
`GC-P1-BACKEND1-INCREMENTAL-STEP-COUNT-BOUND` (opened),
`ARCH-P0-PROVENANCE-GRANULE-MAP` (prerequisite unblocked)

Status: fixed in both mirrors with focused five-backend and C/port gates. No
stage, fixed-point or performance claim.

Investigation:
`docs/investigations/gc-backend1-resurrected-object-reclaim-lags-one-collect.md`

## What was wrong

`pcc_gc_note_slot_write_barrier` shaded a BLACK owner's WHITE referent for
backend 1 whenever it saw one, with no requirement that a marking cycle be
active, and the shading branch stores `mark_active = 1`. Ordinary mutator
stores after a completed collect therefore turned marking "on" with no epoch,
no whitening pass and no seeded roots.

The next explicit `gc.collect()` then found `mark_active != 0`, skipped
`pcc_gc_begin_mark_cycle`, had nothing gray to trace, immediately claimed the
cycle finish, and computed its candidate set from the WHITE bit — which no
whitening pass had set. The result was an explicit collect that reclaimed
**nothing at all**, not merely a one-cycle delay on the resurrected object: in
the measured probe, 30 unrelated fresh unreachable cycles also survived it.

Backend 2 gates the same branch on `mark_active`, which is exactly why
backends 0/2/3/4 passed and only backend 1 failed.

## The evidence that located it

No existing facility can observe mark-cycle state: `PCC_LOG=gc` has only
`collect_start`/`collect_stop`, the freestanding closure verifier rejects
`pcc_runtime_log_event_code` from these modules, and it also rejects a
non-exported helper function inside a freestanding module. The probe was
therefore one inlined `pcc_platform_write(1, cstr("X"), 1)` per event. Three
collects around a resurrection, with 30 fresh cycles added before the second:

```text
BTATFrKc1 0
zs 1
AtFc2-with-30-fresh 0
BTATFrKc3 31
```

`B` begin cycle, `A` continue active cycle, `T`/`t` traced / traced nothing,
`F` finish claim taken, `r` bail on no request, `K`/`n` sweep candidate
present / absent. The failing collect is `A t F` with neither `K` nor `n`: it
continued a phantom cycle, traced nothing, finished it, and never reached
`pcc_gc_collect_tracing`. All markers were removed before the fix was
measured; the working tree carries no probe.

## The change

Both mirrors, one condition each, nothing else:

* `pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py` — backend-1
  `should_shade` gains `load_i32(global_addr("pcc_gc_mark_active"), 0) != 0`.
* `pcc/py_runtime/src/py_gc_backend.c` — the non-CMS `should_shade` gains
  `pcc_gc_mark_active_load() != 0 &&`.

The following `mark_active` store is now redundant rather than harmful and was
left in place on both sides.

## Gates

New regression `test_collect_after_resurrection_still_reclaims_unrelated_garbage`
in `tests/python/test_gc_resurrection.py` gates the general defect (a collect
must not become a no-op) rather than the resurrection corner. It fails
`assert 0 >= 30` before the change.

```text
PCC_GC_BACKEND=1 ... tests/python/test_gc_resurrection.py
  7 passed in 37.69s

PCC_RUNTIME_CC=cc PCC_GC_BACKEND=1 ... tests/python/test_gc_resurrection.py
  7 passed in 10.87s          (C oracle arm)

five-backend finalizer/resurrection/weakref/trashcan gate
  backend 0   44 passed in 94.30s
  backend 1   44 passed in 91.30s
  backend 2   44 passed in 93.65s
  backend 3   44 passed in 93.83s
  backend 4   44 passed in 94.93s

tests/python/test_bootstrap_gate_baseline.py        2 passed, 2 deselected
tests/python/test_fallback_baseline.py + ir_py      40 passed in 547.79s
cc -fsyntax-only py_gc_backend.c (tripwires+threads)  clean
```

Before the fix the same five-backend gate was `1 failed, 27 passed` on
backend 1 and 43 passed on the other four.

## Found while gating, not caused by this change

`tests/python/test_gc_backend_incremental.py` fails two step-count bounds
under the pcc-Python runtime: `assert 1072 < 500` (container churn) and
`assert 1069 < 500` (pause budget). Proven pre-existing by re-running the
first one on the pre-fix arm, which produces the identical `1072`. The other
11 tests in that file pass. Tracked as
`GC-P1-BACKEND1-INCREMENTAL-STEP-COUNT-BOUND` rather than silently deselected.

## Nonclaims

No bootstrap stage, pcc1/pcc2/pcc3 fixed point, five-GC matrix, pause or
throughput claim. The fix is a correctness change on the backend-1 barrier;
its effect on incremental pacing beyond the gates above was not measured, and
the pre-existing step-count bounds above remain open.
