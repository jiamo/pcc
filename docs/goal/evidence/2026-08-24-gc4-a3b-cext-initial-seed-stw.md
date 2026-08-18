# GC4 A3b C-extension initial-seed STW evidence — 2026-08-24

## Claim

The initial three-pass refcount-root seed no longer invokes C-extension
`tp_traverse` while holding the GC graph lock or while other mutators can change
the refcount/edge snapshot.  C and strict pcc-Python reuse the extension-root
token with `pending=4`: an inactive requested cycle claims exact epoch/backend
identity under the graph lock, releases it, acquires or reuses a stopped world,
revalidates the claim, runs the full seed without the graph lock, then
reacquires and revalidates before publishing `mark_active`, clearing
`cycle_requested`, transitioning to ordinary extension-root pending state 1 and
initializing the trace cursor.

Stop failure or identity drift clears only the exact seed token and leaves the
cycle request retryable.  Backend switching rejects `pending=4`, including
same-backend reset reentry from `tp_traverse`, so collector identity cannot
change mid-snapshot.  This retains exact C-extension edge subtraction; skipping
those edges was rejected because it would keep extension-owned cycles
permanently rooted.

The source contract was genuinely RED before implementation because no
`pcc_gc_begin_mark_cycle_claim_unlocked` existed.  The final C/strict true-
pthread probe arms only the first C-extension traversal, proves the callback
observes stopped-world ownership, rejects a reentrant same-backend reset, and
lets an unregistered contender acquire the runtime's physical graph lock from
inside the callback.  Subsequent ordinary tracing is disarmed so it cannot
falsely satisfy the seed claim.

## Focused evidence

Complete seed -> ordinary -> final/CMS callback chain, C/strict pthread
behavior, source contracts, LLVM/self closure, production archive ownership,
single-finisher windows and CMS overflow/termination neighbors:

```text
gtimeout 180s zsh -o pipefail -c "gtimeout 150s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_gc_threading_substrate.py::test_initial_refcount_seed_claims_stw_before_cext_callback \
  tests/python/test_gc_threading_substrate.py::test_final_and_cms_whole_gray_cext_callbacks_split_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_cms_wb_queue_publication_is_outermost_and_lifecycle_epoch_guarded \
  tests/python/test_gc_threading_substrate.py::test_initial_seed_cext_traverse_owns_stw_without_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_incremental_trace_cext_traverse_runs_outside_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_final_cut_cext_traverse_runs_outside_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_tracing_finish_claim_lifts_stw_outside_graph_lock_in_c_and_strict_runtime \
  tests/python/test_gc_threading_substrate.py::test_tracing_finish_claim_real_pthread_windows_and_single_finisher \
  tests/python/test_freestanding_gc_common_mark_cycle.py \
  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py \
  tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_batches_gray_barrier_flushes \
  tests/python/test_gc_backend_concurrent.py::test_concurrent_backend_worker_reaches_mark_termination_without_mutator_gc_step \
  2>&1 | tee build/gc-seed-final-callback-focused.log"

28 passed in 6.46s
```

Production link-map, strict substrate owner and C explicit-collect neighbor:

```text
5 passed in 2.44s
```

Final-source task-card relocation/forwarding gate:

```text
24 passed in 12.31s
```

C syntax with `PCC_WITH_THREADS=0/1`, direct strict self/no-libpython closure
for common mark, scheduler and managed GC backend, Python syntax and
`git diff --check` pass.

The strict container-churn neighbor produced correct functional output but
reported `steps=1072` against its historical `<500` threshold.  It was not
waived or claimed green.  A controlled archive A/B used the same probe source,
current compiler/self-backend and environment with only the runtime archive
changed: the 12:48 pre-seed archive and 13:24 seed archive both printed exactly
`steps 1072` and `debt 120`.  Therefore that threshold failure is not caused by
this seed change and remains separate performance work.

The old single-finisher regression assumed the first stop request of a cycle
was the final cut.  The seed STW invalidated that test-only schedule, not its
semantic assertions.  The final regression deterministically consumes seed
plus one rooted staging object before admitting each final contender, then
retains the original color, barrier, reset-ABA, single-finisher and owned-STW
assertions in C and strict.

## Frozen identities

```text
ce38c5a7ef4a5113472fc4f51300fa03300d095b9b6175bb116770c97fd7cdbb  pcc/py_runtime/src/py_gc_backend.c
a2989a6e7e2d38e44e11671338d22bf9246ad10c0e4dccb0a740f13d558d0a10  pcc/py_runtime/py/freestanding_gc_common_mark_cycle.py
bacd3d47e5c9eed11f49322daef2bacff2c31292eb95e823338201cb22831df2  pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py
aee3581fd204c7e3301ff5d6c3ce330ea28b03356ba4f0ff1fe78f2a6f1bdd6c  pcc/py_runtime/py/py_gc_backend.py
d9659ab87c9b55787d5f1f41e0d5d85c0441fe1ec1aa3e816c1d7338a0bcb36a  tests/python/test_gc_threading_substrate.py
91b7cbb5a0f6fb5ebd49e88e9f6e57b4362246765063246607de865aa841a346  tests/python/test_freestanding_gc_common_mark_cycle.py
3dc9e074b64d34cf3fff8c32c0bc8338c9a7641c3aa5376279dd5b010e7f6b9d  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py
be6393071a6f30b761c511fb0a52ce55b5a4d20bfc040dc3342b917d1a39c3d2  build/gc-seed-final-callback-focused.log
d5b187edd75e64a9e3761493f82c19fc8b28c9ae0f1328f74786c477cba5eea7  build/gc4-relocation-mutator-quiescence.log
8779b2314a4f1a5f283317eaa1c341d8def601c78d0972d5262e700890e40898  pre-seed libpy_runtime_pcc_py.a control
c41cf6f4ac779a8f3148310d35cfb1aa32d150b5bfcfc1c7eb108f887571c6d0  seed libpy_runtime_pcc_py.a candidate
```

## Open boundary

All classified trace/mark C-extension callback holders are now split from graph
lock tenures: promotion, ordinary incremental, direct CMS, initial seed, final
cut and CMS RESCAN.  The seed remains an O(heap) stopped-world three-pass
operation; no bounded-pause or performance acceptance is claimed.

Backend-4 remap/update is the remaining classified C-extension slot callback
holder.  Unlocking its registry walk still requires collector-owned STW plus
object/source lifetime and registry revision, and each retained managed local
must remain updateable/reloaded across callback reentry.  A3c, raw access,
publication, leases, resurrection, stage performance, fixed point and five-GC
parity remain open.
