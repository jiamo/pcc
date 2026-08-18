# Investigation: Backend #1 reclaims a resurrected-then-dropped object one explicit collect late

## Status
resolved

## Problem Description
Under `PCC_GC_BACKEND=1`, an object that resurrected itself in `__del__` and
later became unreachable is not reclaimed by the first explicit
`gc.collect()` after it became unreachable. The next `gc.collect()` reclaims
it. Backends 0, 2, 3 and 4 reclaim it on the first call.

Nothing leaks — this is a one-cycle latency, not a lost object. It is still a
contract deviation, because the public collect entry point documents itself as
a full-heap boundary rather than an incremental step
(`pcc/py_runtime/py/freestanding_gc_public_collection.py:253`):

> ``gc.collect()`` is the explicit full-heap boundary, not an incremental
> scheduler step.

This is a different failure from the resolved
[gc-backend1-transitive-resurrection.md](gc-backend1-transitive-resurrection.md)
(that one was an incomplete root set for `.classattr.*` globals and it
returned `-6`/aborted; this one returns a wrong reclaim count of `0` and does
not abort). It is also distinct from the referent-clearing order recorded in
[gc-transitive-resurrection-clear-order.md](gc-transitive-resurrection-clear-order.md).

Found while re-establishing the five-GC finalizer/resurrection/weakref/
trashcan gates that `ARCH-P0-PROVENANCE-GRANULE-MAP` lists as a prerequisite
before any S2 stage build, on current source (allocator sha `baf99ebe`).

## Repro

```bash
gtimeout 300s env -u LC_ALL PCC_GC_BACKEND=1 uv run pytest -q -x -n0 \
  tests/python/test_gc_resurrection.py::test_resurrection_does_not_block_other_cleanup
```

Observed:

```text
>       assert int(out[2]) >= 1
E       AssertionError: assert 0 >= 1
1 failed in 2.14s
```

The same command with `PCC_GC_BACKEND=0`, `2`, `3` or `4` passes.

Minimal program (compiled through `pcc.py_frontend.pipeline.compile_python`,
`ir_scaffold_mode="on"`, then run):

```python
import gc

zs = []

class A:
    def __init__(self):
        self.me = self

class Z(A):
    def __del__(self):
        zs.append(self)

def main() -> None:
    Z()
    print("c1", gc.collect())        # 0 on every backend (Z resurrected)
    print("zs", len(zs))             # 1
    zs.clear()
    print("c2", gc.collect())        # backend 0/2/3/4: >=1   backend 1: 0
    print("c3", gc.collect())        # backend 1: 2

if __name__ == "__main__":
    main()
```

## Test [CONFIRMED]
Observed with the pytest command above on current source: 1 failed on
backend 1, and

```text
gtimeout 600s env -u LC_ALL PCC_GC_BACKEND=$b uv run pytest -q -x -n0 \
  tests/python/test_gc_finalizer_corner.py tests/python/test_gc_g2_finalizers.py \
  tests/python/test_gc_g3_weakref.py tests/python/test_gc_resurrection.py \
  tests/python/test_gc_trashcan.py

backend 0   43 passed in 129.06s
backend 1    1 failed, 27 passed in 60.56s
backend 2   43 passed in  92.02s
backend 3   43 passed in  89.11s
backend 4   43 passed in  85.23s
```

Four probes bound the failure. All were compiled and run per backend through
the same pipeline as the test.

1. **Not a leak.** With a companion object whose own `__del__` prints, the
   third collect on backend 1 returns `2` — the same total backend 0 returns
   on its second collect.
2. **Not general explicit-collect breakage.** Six rounds of "allocate 50
   self-cycles, collect" return `50` every round on backends 0, 1 and 2.
   Backend 1 does not reclaim only every other collect.
3. **Not finalizers in general.** A `__del__` that resurrects nothing, on a
   self-cycle, is reclaimed by the first collect on backend 1 (`fin-c1 1`,
   `fin-c2 0`, and again `fin-c3 1`, `fin-c4 0`).
4. **Draining first removes the difference.** Inserting two extra
   `gc.collect()` calls between the resurrection and `zs.clear()` makes
   backend 1 behave exactly like backend 0 (`after-clear-1 1`). So the lag is
   carried in collector state left behind by the collect that ran while the
   object was still resurrected-and-reachable, not in the object's own
   finalized state alone.

Runtime `PCC_LOG=gc` output has only `collect_start`/`collect_stop` events at
this level, so it does not resolve mark-cycle state; the vocabulary observed
on this program was 410 `store_ptr`, 3 `collect_start`, 3 `collect_stop`.

## Proposals
- No.1 Empty-cycle degeneration in `pcc_gc_begin_mark_cycle` when the seeded
  root set produces zero gray objects     [DENIED]
- No.2 The backend-1 write barrier fabricates an active mark cycle outside
  any cycle     [CONFIRMED]

## No.1 Empty-cycle degeneration in `pcc_gc_begin_mark_cycle`
### Code Change
None yet. Stated so it can be refuted before anyone writes code.

The suspected shape, from source reading only:
`pcc/py_runtime/py/freestanding_gc_common_mark_cycle.py:149-154` ends
`pcc_gc_begin_mark_cycle` with

```python
    global_store_ptr("pcc_gc_trace_cursor",
                     global_load_ptr("pcc_gc_object_head"))
    if pcc_gc_gray_count_load_acquire() == 0:
        global_store_ptr("pcc_gc_trace_cursor", null())
```

A cycle that begins with a null cursor traces nothing, so the step in
`freestanding_gc_incremental_concurrent_scheduler.py:476-505` immediately
takes the finish claim and completes the cycle. The explicit-collect driver
in `pcc/py_runtime/py/py_obj.py:810-821` loops `while pcc_gc_step(1024)` and
exits as soon as a step reports zero processed objects, then asks
`pcc_gc_has_tracing_sweep()`. If that degenerate cycle never set the
candidate flag (`1024`) on the unreachable resurrected object, PASS-0/1/2 in
`freestanding_gc_tracing_sweep_collector.py:213-265` have no candidate and the
collect correctly reports `0` — while the next collect runs a real cycle and
reclaims.

This is a HYPOTHESIS. It explains probes 1 and 4, and it must additionally
explain probes 2 and 3 (fresh garbage and ordinary finalizer cycles are
reclaimed on the first collect) before it can be accepted. The obvious
competing explanation is that
`pcc_gc_tracing_recheck_reachability_after_finalizers`
(`freestanding_gc_tracing_sweep_collector.py:195-211`), which clears flag
`1024` for a resurrected object as PEP 442 requires, leaves per-object
colour/epoch state that suppresses candidacy for exactly one following cycle.

### DENIED
`pcc_gc_begin_mark_cycle` was never reached on the failing collect, so its
zero-gray shortcut cannot be the cause. Proven by instrumented markers, below.

Recorded so the next reader does not re-derive it: the whitening pass exists
and is correct. `pcc_gc_seed_roots`
(`freestanding_gc_common_mark_cycle.py:91-98`) calls
`pcc_gc_prepare_object_list_mark`, which sets the WHITE bit on every tracked
object before graying roots
(`freestanding_gc_object_root_seeding.py:30-48`). A cycle that actually
begins therefore cannot inherit stale colours.

## No.2 The backend-1 write barrier fabricates an active mark cycle
### Code Change
Require an active marking cycle before backend 1 shades a black owner's white
referent, exactly as the backend-2 arm already did. Both mirrors, same slice:

* `pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py`
  `pcc_gc_note_slot_write_barrier`: the backend-1 `should_shade` condition
  gains `load_i32(global_addr("pcc_gc_mark_active"), 0) != 0`.
* `pcc/py_runtime/src/py_gc_backend.c` `pcc_gc_note_slot_write_barrier`: the
  non-CMS `should_shade` gains `pcc_gc_mark_active_load() != 0 &&`.

Nothing else changed. The `pcc_gc_mark_active_store(1)` that follows is now
redundant rather than harmful and was left in place on both sides.

### CONFIRMED
Instrumentation, since no existing facility could see mark-cycle state: one
byte written per event from inside the freestanding closure
(`pcc_platform_write` to fd 1, because the closure verifier rejects
`pcc_runtime_log_event_code` from these modules and rejects a non-exported
helper function). Markers: `B` begin cycle, `A` continue active cycle, `T`/`t`
traced / traced nothing, `F` finish claim taken, `r` bail on no request,
`K`/`n` sweep candidate present / absent. Running the three-collect program
with 30 fresh unreachable cycles added before the second collect produced:

```text
BTATFrKc1 0
zs 1
AtFc2-with-30-fresh 0
BTATFrKc3 31
```

Read it as: the failing collect (`c2`) found a cycle already ACTIVE (`A`),
traced nothing (`t`), took the finish claim (`F`) — and never reached
`pcc_gc_collect_tracing` at all, since neither `K` nor `n` appears. It
therefore began no cycle, whitened nothing, seeded no roots, computed an empty
candidate set from the WHITE bit, and reclaimed neither the resurrected object
nor the 30 unrelated fresh cycles. The next collect (`c3`) begins a real cycle
(`B`) and reclaims all 31.

That active cycle was fabricated by the write barrier. For backend 1 the
shade condition was only "owner is BLACK", with no active-cycle requirement,
and the shading branch stores `mark_active = 1`. Ordinary mutator stores after
a completed collect — `zs.append(self)` inside the finalizer, `zs.clear()`,
and each `self.me = self` in the fresh cycles — have a black owner and a white
value, so they turned marking "on" with no epoch, no whitening pass and no
seeded roots. Backend 2 gates the same branch on `mark_active`, which is
exactly why backends 0/2/3/4 pass and only backend 1 failed.

The instrumentation was removed before the fix was measured; the working tree
carries no probe. Verified after the fix:

```text
env -u LC_ALL PCC_GC_BACKEND=1 uv run pytest -q -x -n0 tests/python/test_gc_resurrection.py
  7 passed in 37.69s

five-backend finalizer/resurrection/weakref/trashcan gate
  backend 0..4   44 passed each (94.30s / 91.30s / 93.65s / 93.83s / 94.93s)

C oracle arm
  PCC_RUNTIME_CC=cc PCC_GC_BACKEND=1 ... tests/python/test_gc_resurrection.py
  7 passed in 10.87s
```

The new regression `test_collect_after_resurrection_still_reclaims_unrelated_garbage`
fails `assert 0 >= 30` before the change and passes after it. It gates the
general defect rather than the resurrection corner: a collect must not be
turned into a complete no-op.

## Report
No.2 landed in both mirrors; No.1 is DENIED and its whitening-pass detail is
kept so it is not re-derived. The failing symptom in the title (one collect
late) was the mild face of the defect; the measured behaviour is that the
collect after any resurrection reclaimed nothing at all, including unrelated
garbage.

Found while re-running this gate: two sibling tests in
`tests/python/test_gc_backend_incremental.py` fail an unrelated step-count
bound (`assert 1072 < 500` and `assert 1069 < 500`). Proven pre-existing — the
pre-fix arm produces the identical 1072 — and tracked separately as
`GC-P1-BACKEND1-INCREMENTAL-STEP-COUNT-BOUND`.
