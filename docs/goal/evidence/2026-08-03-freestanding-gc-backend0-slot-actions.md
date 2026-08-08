# Freestanding backend-0 slot actions and finalizer-safe table reentry

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Slice fingerprints include:

```text
freestanding_gc_backend0_slots.py  9be5dc585f9901f4d0bf10a84e89aab99c8a36f5807f943929e27b204ec784de
freestanding_gc_tracking.py        2a1d93314225c8c820e4bc41ed21565e80a9b69efc4b0194bc46a1e45ce2987c
py_obj_gc.py                       a7b242856fcc1f64e44bf0477c7772664221b7ca9eb5e4044a348e38b4d3b917
py_obj_gc.c                        b8caeeb81e18267a8a0ab37cdbbe3b4fad7a5fd7dc7d4b2b5eeb3e4b61e34617
pcc_threads.c                      f567d7cbb6f0e4032b08b8339bbf072e057864bbde04d7b8290e604d2079e3f2
test backend0 slots               ab01ea76d367600fb3b1dd2e34e6f36232135be08d09df79daa24cefeb4f9904
test tracking                     606650e6897fddb55c21b4221f098e06784d6c8bb00f5ce235714ba22f3963b5
```

## Claim boundary

The backend-0 raw slot actions now have one strict freestanding pcc-Python
production owner:

```text
pcc_gc_backend0_is_unreachable
pcc_gc_backend0_subtract_slot
pcc_gc_backend0_visit_subtract
pcc_gc_backend0_mark_reachable
pcc_gc_backend0_mark_slot
pcc_gc_backend0_clear_slot
pcc_gc_backend0_clear_container_metadata
pcc_gc_backend0_clear_referents
```

`py_obj_gc.py` retains managed list-producing inspection APIs and collector
orchestration, but calls the strict subtract/mark/clear ABI. Both layers consume
the one `pcc_gc_visit_object_slots` layout contract; no object geometry was
copied into the new module. The strict object's complete undefined closure is
exactly `pcc_gc_load_ptr`, `pcc_gc_visit_object_slots`, `py_decref`, and
`py_gc_index_find` under both LLVM and self emission.

This slice does not claim that the backend-0 collector state machine or the
other four collectors are fully strict/freestanding.

## Red evidence and stacked failure

The new owner/closure tests first failed in three places because the strict
module did not exist. After the ownership migration, the required finalizer
gate exposed a separate pre-existing backend-0 bug in both the retained C
oracle and production pcc-Python archive:

```text
test_del_on_cycle_runs_after_collect       timed out after 60s
test_resurrection_is_transitive            timed out after 60s
```

Runtime logging stopped during `__del__` immediately after creation of a
tracked bound-method temporary. `/usr/bin/sample` showed the main thread
spinning in `pcc_gc_default_table_lock`: collection held the tracked-object
table lock while user finalizer code re-entered `py_gc_track`.

A five-second regression reproduced the same timeout against both archives.
The first attempted guard used global `py_gc_collecting`; the raw-pthread
contention gate then aborted because foreign pthreads also bypassed the lock.
That falsified the global-owner assumption before it could land as the final
protocol.

## Final synchronization protocol

The C-level threading kernel now exposes a native-thread identity token backed
by a C11 TLS byte. It stays unique across live raw pthreads even when the
archive was built with `PCC_WITH_THREADS=0`; this is deliberately distinct
from the single-thread-mode synthetic `pcc_current_thread_id() == 1` contract.

The table lock publishes that token in an atomic i64 owner slot. Only the
actual owner thread may re-enter track/untrack without acquiring the byte lock.
All other native threads still contend normally. If owner-thread untrack occurs
while collection holds candidate `PyGcNode *` values, the node is removed from
the index/list and its object pointer is cleared, but node storage is queued on
a raw deferred-free list until collection ends. Candidate loops skip cleared
nodes, preventing both deadlock and use-after-free/ABA. No tracking event is
dropped.

The retained C oracle implements the same transition. This C change is an
oracle/kernel correctness repair, not a claim that production GC ownership
moved back to C.

## Focused proof

```text
48 passed in 7.40s
  strict state + tracking + backend-0 slot actions + shared referent rules

17 passed in 8.15s
  threading substrate

2 passed in 55.91s
  raw-pthread collector contention + finalizer owner-thread reentry

3 passed in 49.80s
  cycle finalizer + transitive resurrection + weakref callback

3 passed in 49.86s
  backend-0 production contracts: cycle finalizer, reentrant gc.collect,
  finalizer resurrection
```

The raw-pthread harness explicitly checks four worker TLS tokens are non-null
and pairwise distinct, then proves both archives finish at
`tracked:1024,1024` and `untracked:0,0`. The finalizer regression breaks its
own self-cycle inside `__del__`, proves tracked count returns to baseline and
the finalizer runs exactly once, and gives each executable only five seconds.

The production archive symbol table uniquely attributes all eight slot action
symbols to `freestanding_gc_backend0_slots.o`; tracking/lock/deferred-drain
symbols are uniquely owned by `freestanding_gc_tracking.o`, never
`py_obj_gc.o`.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/libc-gc-backend0-slots-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/libc-gc-backend0-slots-stage1 --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=140939 \
  output=build/libc-gc-backend0-slots-stage1/pcc1
```

That fresh no-libpython/self pcc1 compiled the real slot-action, tracking, and
state strict modules with `--ir-scaffold=on`, `--backend self`,
`--python-libpython off`, and `--python-library`. Clang accepted all three IR
outputs. `nm` confirmed the eight slot definitions/four exact imports, the
tracking owner/deferred ABI, and the 132-symbol raw state object including the
atomic owner token and deferred-node head.

## Remaining task boundary

Migrate the managed collector state machines for backend 0 and tracing/
incremental/concurrent/generational/relocating backends without duplicating the
slot graph. Complete the all-backend weakref/finalizer/resurrection,
suspended-frame/scheduler/C-extension-root, relocation, synchronization, and
production link-map proofs. Run the five-GC fixed-point matrix and long-running
RSS/fragmentation/pause/throughput measurements once, only after those source
migrations stabilize.
