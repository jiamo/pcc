# GC4 A3a tracing final-cut STW lift

Status: **FOCUSED GREEN for the shared tracing final-cut lock-order and
single-finisher contract only.**
`GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE` remains `IN_PROGRESS`.

## Supported claim

The common tracing final cut used by GC1/GC2 no longer requests a stopped
world while holding the GC object-graph lock.  The C differential oracle and
the strict freestanding pcc-Python production objects now use the same
two-phase protocol:

1. A tracing step runs under the graph lock.  When tracing appears complete,
   at most one caller claims the cycle with the captured pair
   `(cycle_epoch, selected_backend)`, then releases the graph lock.
2. The claimant either reuses a stopped world already owned by the current
   thread (including the GC2 CMS worker's outer stop) or calls
   `pcc_stop_the_world` with no graph lock held.
3. After stop succeeds, it reacquires the graph lock and revalidates the exact
   claim pair, current cycle epoch, selected backend, and active-cycle state.
   A reset, same-backend setter call, backend switch, stale claimant, or second
   finisher therefore cannot cut or reset a successor cycle.
4. The pure final-cut owner rescans current roots, drains all gray work, marks
   white candidates, and commits cursor/gray/active state while both STW and
   the graph lock are held.  The final rescan/drain covers roots and gray work
   published before the stop; the commit deliberately preserves a new
   `cycle_requested` instead of erasing the request for the following epoch.
5. The graph lock is released before a stop acquired by this helper is
   resumed.  A caller-owned stopped world is left owned by that caller.

The cycle epoch is monotonic and never reused: a negative value or
`INT64_MAX` triggers an unconditional process abort instead of wrapping.
Backend setter/reset advances the
epoch and commits selected backend plus tracing active/request/cursor/gray
state under one graph lock.  It retains an outstanding old claim until the
old claimant clears its captured token; token-match clear checks both epoch
and backend before writing either claim field.  Stop failure also reacquires
the graph lock and clears only that captured pair, without running a cut,
resuming a world it did not stop, or mutating active/cursor/gray state.

The strict raw ABI has one owner for each cross-object operation:
`pcc_gc_finish_tracing_cycle(epoch, backend)`,
`pcc_gc_complete_claimed_tracing_cycle(epoch, backend)`, and
`pcc_gc_tracing_finish_claim_clear_unlocked(epoch, backend)`.  The C oracle
keeps its equivalent completion helper private to its monolithic translation
unit.  No old no-argument finish ABI remains in the production or approved
neighbor sources.

This is a prerequisite lock-order slice for the Backend 4 quiescence task.  It
executes shared tracing behavior under GC1 and the GC2 CMS neighbor; it does
not execute physical Backend 4 relocation.

## Freeze #5 source and test identity

| Path | SHA-256 |
|---|---|
| `pcc/py_runtime/src/py_gc_backend.c` | `67296940fe9ffc7240646767007949f5c86ecf5b14fb576e88314ef663665f74` |
| `pcc/py_runtime/py/freestanding_gc_state.py` | `c48b3f1470d7909c8b51dae95bd64fa95b63e3ce862d150a1d405838f21c50f7` |
| `pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py` | `12488092435b7d5a02886c169095bb3a9e70590159c9050b3b87b9c402c92b07` |
| `pcc/py_runtime/py/freestanding_gc_common_mark_cycle.py` | `3e4f7b92b1237cf3b2479be171b5ce18a4a1e34b22ff8083a5e5ca282fa1b113` |
| `pcc/py_runtime/py/py_gc_backend.py` | `0c03b034c3d74168a7ebc873800fe16ffddacbd00449a467974f0df6d7db77cb` |
| `pcc/py_frontend/codegen/runtime_abi.py` | `fa21e252ee901b0649de40314b3e01c6fb0c440b60d5981d718dcd8a1550abe3` |
| `tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py` | `602fac4999bfc796fd342c8390fd12a43237b38be64fdf6deb61469ff8aa581b` |
| `tests/python/test_freestanding_gc_common_mark_cycle.py` | `4e1474bf5f914ce963371c3f18152d36b1b92fbc0555b23e03016950c649c788` |
| `tests/python/test_gc_threading_substrate.py` | `23175dfba44bc0e171892138af0af5ba2bbb2eb67b39db28ca64a760e7343631` |
| `tests/python/test_freestanding_gc_public_collection.py` | `d41ec9b06ec9ceb2fc9f47af98609974791464effb0d5b8b380b9a08357bde97` |

These are dirty-worktree content identities, not a clean commit or release
manifest.  Freeze #5 supersedes the rejected intermediate freezes; only the
threading test changed between Freeze #4 and Freeze #5, to make the two token
guards and early return all precede both claim-field stores.

## RED to green record

The fail-first and compiled closure sequence exposed these concrete gaps.  All
were below 60 seconds, so no durable tee log exists and none is claimed:

| Boundary | RED result | Correction carried by Freeze #5 |
|---|---|---|
| First exact static contract | Failed in `0.16s`: tracing epoch global absent | Added C/strict epoch, claim pair, commit counter, ABI, and two-phase owner |
| Strict scheduler LLVM closure | Failed in `0.18s`: scheduler called a private common helper | Exported one strict raw completion owner and declared the scheduler extern/runtime ABI |
| Strict raw closure | Failed in `0.19s`: claim-clear extern was noncanonical | Made common-mark the unique raw clear owner and scheduler a consumer |
| GC2 public CMS neighbor | Failed to link in `1.23s`: harness lacked the new platform-abort/write-side stubs and still modeled the old finish ABI | Updated the approved harness stubs/closure to the captured epoch/backend ABI |
| C real-pthread window | Exited `14` in `7.81s`: the next requested epoch was not guaranteed to finish in one budgeted step | Kept the production budget contract and bounded the test to four real steps |

Adversarial review also rejected several tests that could have been falsely
green before the final run:

- a synthetic barrier was replaced by a real `pcc_gc_store_ptr` from an aged
  black owner to a separate aged white child, with a telemetry delta;
- a third aged white self-cycle is registered only after stop publication, so
  final root rescan and insertion-barrier preservation have separate causes;
- the first-cut assertions require both objects to be BLACK and neither GRAY,
  WHITE, nor a sweep candidate; merely checking `!candidate` was insufficient;
- same-backend reset samples the old claim before and after reset and requires
  the old pair to remain intact until its captured claimant clears it;
- the two-finisher window publishes a real stop, holds two bounded no-park
  regions, and requires the second step to return without changing the first
  token or commit count;
- the CMS harness joins its worker before reading plain terminal state; and
- Freeze #5 replaced pairwise claim-clear source checks with
  `max(epoch_guard, backend_guard, early_return) <
  min(epoch_store, backend_store)` in both C and strict sources.

The dynamic windows use acquire/release phase variables and no-park depth.
`sched_yield` appears only inside state-conditioned wait loops; there is no
fixed sleep used to manufacture the contested ordering.  The CMS neighbor's
bounded one-millisecond poll waits for a concrete atomic drain counter and is
followed by join before terminal reads.

## Final focused gates

For the sub-60-second runs, the retained execution record contains the exact
node selection and canonical pytest options
`env -u LC_ALL uv run pytest -vv -x -n0 --tb=short`; the original outer
watchdog shell text was not retained.  Those commands did not use tee, so this
evidence records results without inventing log artifacts.

### Source, ABI, and ordering contract

The production-frozen six-node selection passed **6 passed in 0.11s**:

- `tests/python/test_gc_threading_substrate.py::test_tracing_finish_claim_lifts_stw_outside_graph_lock_in_c_and_strict_runtime`
- `tests/python/test_freestanding_gc_common_mark_cycle.py::test_common_mark_cycle_has_one_strict_source_owner`
- `tests/python/test_freestanding_gc_common_mark_cycle.py::test_common_mark_cycle_preserves_root_and_termination_order`
- `tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py::test_incremental_concurrent_scheduler_has_one_strict_source_owner`
- `tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py::test_incremental_concurrent_scheduler_preserves_bounded_policy_order`
- `tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py::test_tracing_finisher_claim_stops_only_after_graph_unlock`

The only Freeze #5 test-source delta then reran the first node above and passed
**1 passed in 0.16s** at the final threading-test hash.

### Strict raw-object closures

Each exact node passed independently:

| Node | Result |
|---|---|
| `tests/python/test_freestanding_gc_common_mark_cycle.py::test_common_mark_cycle_object_has_exact_raw_closure[llvm]` | `1 passed in 0.88s` |
| `tests/python/test_freestanding_gc_common_mark_cycle.py::test_common_mark_cycle_object_has_exact_raw_closure[self]` | `1 passed in 0.64s` |
| `tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py::test_incremental_concurrent_scheduler_has_exact_strict_object_closure[llvm]` | `1 passed in 0.86s` |
| `tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py::test_incremental_concurrent_scheduler_has_exact_strict_object_closure[self]` | `1 passed in 0.67s` |

These prove the exact raw globals/functions and LLVM/self object closure for
the two strict modules.  They are not whole-program LLVM/self parity.

### GC2 CMS and C real-pthread neighbors

| Node | Result |
|---|---|
| `tests/python/test_freestanding_gc_public_collection.py::test_pcc_python_cms_worker_starts_drains_and_joins` | `1 passed in 1.14s` |
| `tests/python/test_gc_threading_substrate.py::test_tracing_finish_claim_real_pthread_windows_and_single_finisher[c]` | `1 passed in 0.26s` on the final warm content-addressed C archive |

The CMS node proves the outer-stopped-world worker can complete the captured
claim without a nested stop and balances stop/resume before joined terminal
state is read.  The C pthread node covers late root plus real barrier, reset,
two finishers, preserved next request, and caller-owned STW.

### Strict pcc-Python real-pthread gate

The retained inner selection was:

```bash
gtimeout 300s env -u LC_ALL uv run pytest -vv -x -n0 --tb=short 'tests/python/test_gc_threading_substrate.py::test_tracing_finish_claim_real_pthread_windows_and_single_finisher[pcc_python]'
```

It was captured through a pipefail/tee wrapper whose exact outer watchdog text
was not retained.  Observed result: **1 passed in 138.31s**, with a final
pytest summary.  The durable log is
`build/a3a-tracing-finish-strict-freeze5.log`, SHA-256
`eb8f12ebf06207c0461e592d63ab6f95f383115c7792ef40912b5874f8deff43`.

### Final production-owner gate

```bash
gtimeout 120s zsh -o pipefail -c 'gtimeout 90s env -u LC_ALL PCC_RUNTIME_ARCHIVE=/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/55e1986f8b1790ebfa880a4f-threaded-pcc-py/libpy_runtime_pcc_py.a uv run pytest -vv -x -n0 --tb=short tests/python/test_freestanding_gc_common_mark_cycle.py::test_production_archive_has_one_common_mark_cycle_owner tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py::test_production_archive_has_one_incremental_concurrent_scheduler_owner 2>&1 | tee build/a3a-production-owner-freeze5.log'
```

Observed result: **2 passed in 0.83s**, with a final pytest summary.  The log is
`build/a3a-production-owner-freeze5.log`, SHA-256
`dab06bf5dcd07fff3ff09d82233e3421867aca9dae86323521f011dea265e8de`.

## Content-addressed runtime receipts

The final dynamic and owner gates consumed these existing cache artifacts:

| Artifact | SHA-256 |
|---|---|
| Strict cache marker `/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/55e1986f8b1790ebfa880a4f-threaded-pcc-py/.pcc-threaded-pcc-py-complete` | `d728d794f7ed1f11770a6425053474418d5c27efbf621e6b8618dca00b94e233` |
| Strict archive `.../55e1986f8b1790ebfa880a4f-threaded-pcc-py/libpy_runtime_pcc_py.a` | `92eb6c01d9739371d520ee01066ecd666fdabcb37fc24ea36a80ea0e356ec2a9` |
| Strict provenance `.../libpy_runtime_pcc_py.a.provenance.json` | `2661ff6c98d02ca846d379e812aee38115c1e56ad6f7405acdbd5a339604eaec` |
| Strict C-API inventory `.../libpy_runtime_pcc_py.a.capi_syms` | `71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda` |
| Strict target receipt `.../libpy_runtime_pcc_py.a.target` | `1226c4ac2cb8c821a9c1bbf10da42027bdb24700e9426f6961a879705ef51fe1` |
| C cache marker `/Users/jiamo/.cache/pcc/test-artifacts/runtime-builds/900b6b4bd81788f644ba8bb5-c-threaded/.pcc-c-runtime-complete` | `af660f2923f38121698f79df8765d4176b66b947a04a82db271a8514cd05aa60` |
| C archive `.../900b6b4bd81788f644ba8bb5-c-threaded/libpy_runtime.a` | `f96ae473e64a9e40a36ca3f80c454d12387c047eb3f750b2a8e88a7387f1b2e8` |
| C C-API inventory `.../libpy_runtime.a.capi_syms` | `71ab7e714faa2f754fd353fc6d7f50cf95267d32f4388895b95d30ddd01dffda` |

The strict marker is schema `pcc.runtime-build-cache.v4`; its provenance is
schema `pcc.runtime-archive-provenance.v2`, target
`arm64-apple-darwin25.5.0`, 186 archive members, 444 C-API symbols, and policy
`pcc-production-no-handwritten-c.v1`.  These receipts bind the focused archive
inputs; they do not turn the dirty worktree into a clean publication manifest.

## Independent review

Two independent read-only reviews converged on Freeze #5:

- the runtime/design adversarial review reported **ZERO findings** for the
  single-finisher, stop-failure/already-STW, lock/STW ordering, window
  preservation, reset/backend revalidation, commit/resume order, C/strict ABI,
  and CMS-owner contracts; and
- the test-sufficiency audit reported **ZERO findings** for the final static,
  closure, real-pthread, CMS, cache/owner, and false-green controls.

Neither reviewer ran tests, builds, or profiles.  Their verdicts cover this
exact A3a freeze only; execution results above are the implementation runner's
and final owner runner's evidence.

## Explicit nonclaims and next boundary

This evidence does **not** prove:

- graph-lock acquire/hold/release is integrated with A1 no-park depth;
- a graph-lock holder is yet a bounded leaf free of decref/finalizer, runtime
  log or blocking I/O, allocator/free, callbacks, or CAS-wait safepoint/sleep;
- complete list/dict/set or other owner-derived raw transactions are guarded;
- callback-split updateable roots, managed thread argument/result handoffs,
  constructor publication, C-API raw-view lifetimes, or nested buffer leases;
- one collector-owned Backend 4 copy/drain/page-drain/idle-remap/target-dies
  phase, forwarded-source payload retirement, or any physical Backend 4
  relocation under concurrent mutators;
- a CPython differential, llvmlite-PY path, pcc1 execution, GC0..4 parity,
  stage1/stage2 timing or performance, pcc2/pcc3 equality, the five-GC matrix,
  or a self-hosted fixed point.

The next A3b boundary is **graph-lock bounded-region preparation**, not direct
no-park integration.  It must first eliminate or defer every reentrant or
blocking operation inside graph-lock holder regions: decref/finalizer work,
runtime logging or blocking I/O, allocator/free, callbacks, and CAS-waiter
safepoint/sleep.  Root-store deferred decref/log is a priority candidate but
still awaits the final read-only route verdict.  Only after A3b makes those
regions bounded leaves may A3c connect the outermost graph-lock acquire/release
to no-park (recursive locking only increments depth; outer unlock occurs before
the no-park exit can service a pending stop).

No broad suite, bootstrap stage, CPython comparison, physical relocation test,
performance measurement, or five-GC matrix was run for A3a.
