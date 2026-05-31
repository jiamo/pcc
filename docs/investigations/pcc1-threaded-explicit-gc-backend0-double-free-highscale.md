# Investigation: backend 0 double-free / refcount underflow under high-scale threaded explicit gc.collect

## Status
active

Backend 0 (reference/default) + universal STW-hang: **RESOLVED & verified**
(official gate `..._all_backends[0]` 1 passed in 11.21s; self-bootstrap
stage1->2->3 1 passed in 53.86s; host-pcc repro 25/25). Backends 1,2,3
(tracing collectors) remain RED on the same gate — tracking as follow-up
slices below; backend 4 was already green.

Predecessor (frozen, `resolved`):
[`pcc1-threaded-explicit-gc-collect-gap.md`](pcc1-threaded-explicit-gc-collect-gap.md)
closed the same *shape* at a lower scale (2 threads, sparse collect). This file
tracks the regression that resurfaced after the committed pcc1 hard gate
`test_pcc1_c_runtime_threads_and_explicit_gc_collect_all_backends` was bumped to
the harder **4 workers x 200 iters, main spams 100 gc.collect()** shape. The
predecessor is `resolved` and must not be edited; this is the successor.

Related (different failure surfaces, not superseded):
- [`threaded-gc-safepoint-production-blocker.md`](threaded-gc-safepoint-production-blocker.md)

## Problem Description
`tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threads_and_explicit_gc_collect_all_backends[0]`
fails. Backend 0 is the **reference / default / rollback** GC backend, so a red
backend-0 gate is the most serious class per `AGENTS.md` ("Do not let backend X
regress backend #0").

The 2026-05-28 `docs/current-goal-state.md` append characterized it as failing
for backends 0,1,2,3 (4 passes) and deferred it as "multi-iter scope, requires
TSan-equivalent analysis". This investigation confirms backend 0 is red and
records the concrete failure fingerprint.

## Repro
Committed gate (red):

```bash
env -u LC_ALL -u LC_CTYPE uv run pytest \
  "tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threads_and_explicit_gc_collect_all_backends[0]" \
  -q -n0
# 1 failed in ~6s; binary exit -6 (SIGABRT)
```

Standalone, deterministic-fast repro (host pcc, same runtime, faster iteration —
the bug is in the C runtime + generated refcount calls, not pcc1 codegen, so
host pcc reproduces it):

```bash
# source = the committed _EXPLICIT_THREADED_GC_SOURCE (4 workers x 200 iters,
# each builds chunk=[i,i+1,i+2], gc.collect() on i%7==0, append under lock;
# main spams 100 gc.collect()). Expected stdout: "800".
env -u LC_ALL PCC_RUNTIME_CC=cc PCC_RUNTIME_HIGH=c PCC_WITH_THREADS=1 PCC_GC_BACKEND=0 \
  uv run pcc --backend self --python-libpython off --ir-scaffold on \
  /tmp/thrgc/thr_gc.py -o /tmp/thrgc/thr_gc.out
for n in $(seq 1 12); do env PCC_GC_BACKEND=0 timeout 30s /tmp/thrgc/thr_gc.out; echo "rc=$?"; done
# ~10/12 rc=134 (SIGABRT), ~2/12 rc=124 (timeout). Empty stdout on abort.
```

Two abort signatures (same root cause, nondeterministic which fires):

```text
Assertion failed: (pcc_refcount_load(&h->refcount) > 0 && "py_decref: refcount underflow"),
  function py_decref, file py_obj.c, line 524.
```
```text
malloc: *** error for object 0x...0c0: pointer being freed was not allocated
```

## Test [CONFIRMED]
`test_pcc1_c_runtime_threads_and_explicit_gc_collect_all_backends[0]` observed
RED (1 failed, exit -6) under the command above on 2026-05-29.

## Findings

### lldb backtrace at the abort (all threads)
```text
thread #1 (main):  pcc_stop_the_world  <- py_gc_collect <- pcc_gc_collect <- user_..._main
                   (WAITING for STW; collector has NOT started yet)
thread #2,#3:      pcc_thread_safepoint <- user_..._worker          (parked for STW)
thread #4 (CRASH): find_zone_and_free <- py_decref+432 <- user_..._worker+1276   (double-free abort)
thread #5:         pcc_thread_safepoint <- py_gc_untrack <- py_decref+352 <- user_..._worker+1276
```
Both worker threads are in `py_decref` at the **same call site**
`user_..._worker+1276`. Disassembly shows `+1272 = bl pcc_gc_release`, i.e. the
frame-slot reassignment release of the previous `chunk` list. `pcc_gc_release(o)`
is literally `py_decref(o)` (`pcc/py_runtime/src/py_obj.c:252`).

When thread #4 aborts, the main thread is still **waiting** in
`pcc_stop_the_world` (collector not yet running), so the *second* free is not a
live collector race at that instant — the object was already freed in a prior
window and a frame slot still pointed at it.

### Object identity
`value1=5` in the runtime event log = `PY_TYPE_LIST`. The only per-iteration heap
object is the thread-local `chunk` list. The appended value `chunk[1]-chunk[0]`
is always `1`, a **tagged int** (immortal; `py_decref` early-returns on
`PY_IS_TAGGED_INT`, and "Tagged ints are inherently immortal",
`py_int_ops.c:254`), so the churn is purely `chunk` lists.

### Ruled out
- **Torn refcounts**: `pcc_refcount_incref/decref/load` use `__atomic_*`
  (`pcc_threads.c:173-204`), default strategy `ATOMIC`. Individual refcount ops
  are correct. The underflow is a *logical* extra decref.
- **Appended-value refcount**: tagged int, no heap object, no decref.

### Mechanism (class identified; exact interleaving still to prove with ASan)
This is the same class as the predecessor's
"backend0 abort root-caused to missing frame roots" update: the backend-0
CPython-style collector (`py_gc_collect`, `py_obj_gc.c:506`) frees an object that
a worker's frame slot still references, then the worker's reassignment decrefs
the now-freed pointer -> underflow / double-free. The 2026-05-14 fix added
`pcc_gc_visit_runtime_roots()` (`py_gc_backend.c:6599`) to mark frame/continuation/
scheduler roots during `py_gc_recompute_reachability()` (`py_obj_gc.c:345`), but
at the 4x200 scale a window remains.

Candidate windows (to discriminate with ASan):
1. **Frame-root visibility gap**: a worker's `chunk` slot is not in the visited
   `pcc_gc_frames` set (registration/unregistration race or frame_map gap) at the
   instant a collector recomputes reachability, so `chunk` (gc_refs computed as 0
   external) is freed while still slot-referenced.
2. **Construction seam**: `chunk` held only in a register between `py_list_new`
   and the store into the rooted slot; if rc is momentarily 0/under-counted and
   not pinned, a collector at an append safepoint frees it.
3. **Missing incref** on some chunk path that single-thread runs mask but
   high-frequency gc.collect exposes.

Note: a pure decref-before-clear window on the *old* value appears to be handled
under STW (if still tracked it is marked reachable via the frame-root visit; if
already untracked, `py_gc_find_node` returns NULL and the visit is a no-op), so
the simple Py_CLEAR-ordering theory alone does not explain it. Confirm with ASan
before patching shared GC/runtime code (AGENTS.md §9).

## Build-staleness gotcha (must read before any runtime-edit experiment)
`pcc/py_frontend/pipeline.py::_runtime_archive_stale` does **not** check
`pcc/py_runtime/src/*.c` mtimes — only headers, the Makefile, and the
`.target` stamp. So editing a runtime `.c` file and re-running `uv run pcc`
**silently reuses the stale archive**: the edit is never compiled in. Symptom:
instrumentation prints nothing / behavior unchanged. Always force a rebuild
before judging a runtime-edit experiment:

```bash
rm -f pcc/py_runtime/libpy_runtime*.a pcc/py_runtime/libpy_runtime*.a.target
rm -rf pcc/py_runtime/build pcc/py_runtime/build_pcc
# then recompile; the pipeline now runs `make -B` and picks up the .c edit
```
(Or `touch pcc/py_runtime/Makefile`, which the stale-check does honor.)

This corrupted the first leak/trace experiment runs in this investigation; all
runtime-edit results before a forced rebuild are void.

## Diagnostic instrumentation in place (env-gated, default off)
In `pcc/py_runtime/src/py_obj_gc.c::py_gc_collect`:
- `PCC_GC_DEBUG_LEAK_UNREACHABLE=1` -> compute reachability but free nothing.
- `PCC_GC_DEBUG_FREE_TRACE=1` -> per freed object, log
  `obj/tag/gc_refs/rc/in_root` to stderr (flushed). `in_root` = is the object
  still pointed at by a registered runtime root (`pcc_gc_visit_runtime_roots`).
Requires `#include <stdio.h>` (added). These are debug-only; remove or keep as
infra once root cause is fixed.

## ROOT CAUSE (CONFIRMED)
The backend-0 cycle collector collects an object whose **raw refcount is already
0**. Such an object is owned by an **in-flight `py_decref`**: a worker thread is
parked at a safepoint between `rc -> 0` (`py_obj.c` `pcc_refcount_decref`) and
`py_gc_untrack(o)`, so the object is still in `py_gc_head` with refcount 0. A
*different* thread's `gc.collect()` then runs under STW, sees the object as
unreachable (`gc_refs = refcount = 0`, not in any frame slot), and frees it.
When the parked worker resumes, it finishes its own free of the same block ->
double-free (`malloc: pointer being freed was not allocated`) or, on the next
`py_decref`, the `refcount underflow` assert.

Evidence (fresh binary, see Build-staleness gotcha):
- `PCC_GC_DEBUG_LEAK_UNREACHABLE=1` (collector frees nothing): 12 runs ->
  ok=9, timeout=3, **abort=0**. Baseline same binary: ok=0, timeout=2, **abort=10**.
  => the collector's free path is the abort source.
- `PCC_GC_DEBUG_FREE_TRACE=1` captured the freed object:
  `[GC_FREE] obj=0x... tag=5(LIST) gc_refs=0 rc=0 in_root=0` — raw refcount 0,
  not frame-rooted: a chunk list mid-`py_decref`.
- Refcount ops are atomic; the collector runs under STW + table lock. The race
  is purely the rc==0 tracked window, not a torn counter.

This refines the earlier "missing frame roots" framing: the object genuinely has
no live root (rc 0, in_root 0); the bug is that the cycle collector treats an
rc==0 tracked object as collectable instead of leaving it to the refcount path.

## Proposals
- No.1 ASan alloc/free1/free2 stacks  [SUPERSEDED — leak-gate + free-trace already pinned it]
- No.2 free-trace: is the collector freeing in_root=1 / gc_refs>0 objects?  [CONFIRMED — frees rc=0/gc_refs=0/in_root=0 objects]
- No.3 cycle collector must skip raw-refcount<=0 objects  [TESTING]

## No.3 cycle collector skips raw-refcount<=0 objects
### Code Change
`pcc/py_runtime/src/py_obj_gc.c` `py_gc_collect`: when building the unreachable
set, require `pcc_refcount_load(&py_header(n->obj)->refcount) > 0`. Mirror in
`pcc/py_runtime/py/py_obj_gc.py` `py_gc_collect` (`load_i64(obj, 0) > 0`).
Rationale: genuine cycle garbage always has refcount > 0 (references internal to
the cycle); a tracked object at refcount 0 is owned by the refcount path. The
guard is a no-op single-threaded (no tracked rc==0 object exists at collection
time there), so it cannot regress non-threaded backend 0.
### CONFIRMED (double-free)
After this fix: standalone repro 20 runs -> ok=13, timeout=7, **abort=0**
(was ok=0, to=2, abort=10). Double-free eliminated. The 7 timeouts are a
separate, pre-existing STW-hang (Proposal No.4).

## Second bug: STW hang (deadlock) — exposed once the double-free was fixed
Backtrace of a hung run (lldb attach):
```text
T1 main:  pcc_stop_the_world  <- py_gc_collect  (waiting for all threads to park)
T3,T5:    pcc_thread_safepoint                    (parked OK)
T2,T4:    pcc_cond_wait <- py_threading_lock_acquire   (NOT at a safepoint)
T3:       pcc_thread_safepoint <- pcc_mutex_lock <- py_threading_lock_release
          (lock owner trying to release, but parked for STW)
```
Deadlock: main's STW waits for T2/T4 to park; T2/T4 block in an **unbounded**
`pcc_cond_wait` inside `Lock.acquire` (no safepoint) waiting for the lock; the
releaser (T3) is parked at a safepoint and cannot release. The 2026-05-14 work
made `pcc_mutex_lock` safepoint-aware (trylock+safepoint) but left the
`Lock.acquire` / `Event.wait` condition waits unbounded.

## Proposals (cont.)
- No.4 safepoint-aware bounded condition wait for Lock.acquire / Event.wait  [TESTING]

## No.4 safepoint-aware bounded condition wait
### Code Change
- `pcc/py_runtime/src/pcc_threads.c`: add `pcc_cond_timedwait_ms(cond, mutex,
  ms)` (pthread_cond_timedwait; returns 0 signaled / 1 timed-out / -1 error),
  plus the non-threads stub; declare in `py_internal.h`.
- `pcc/py_runtime/src/py_threading.c` `py_threading_lock_acquire` and
  `py_threading_event_wait`: replace the unbounded `pcc_cond_wait` loop with
  `pcc_cond_timedwait_ms(..., 5)` + `pcc_thread_safepoint()` each iteration, so
  a thread blocked on a contended Lock/Event still parks during STW.
Note: the pcc-Python mirror `py_runtime/py/py_threading.py` `Lock.acquire`
already uses `pcc_mutex_lock` directly (safepoint-aware), so the Lock path there
is not deadlock-prone; its Condition/Event cond-waits are a separate follow-up.

**CRITICAL detail (first attempt deadlocked):** the safepoint must be taken with
o->mutex RELEASED. `pcc_cond_timedwait_ms` re-acquires the mutex on return, so a
naive `timedwait; safepoint;` parks the thread holding o->mutex, which blocks
every other waiter's cond re-acquire (`pthread_cond_timedwait`'s internal
`__psynch_mutexwait`, not a safepoint) and the lock releaser -> STW re-deadlocks.
Correct sequence per iteration: `timedwait; unlock; safepoint; relock;`.
### CONFIRMED (backend 0)
After the corrected hang fix: standalone host-pcc repro **25/25 ok, 0 timeout,
0 abort** (was 0 ok / 2 to / 10 abort before any fix; 13/7/0 after only the
double-free fix). Backend 0 fully green on the repro.
### CONFIRMED (backend 0 + hang, official gates)
- `tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_threads_and_explicit_gc_collect_all_backends[0]`
  -> 1 passed in 11.21s (was failing exit -6).
- `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  -> 1 passed in 53.86s (mandatory; py_obj_gc.py mirror + threading changes do
  not regress self-host).
- host-pcc repro 25/25 ok backend 0; all-backends repro (12 runs each):
  backend 0 ok=12, backend 4 ok=12, **0 timeouts on every backend** (hang fix
  is universal).

## Follow-up: tracing backends 1, 2, 3 still RED (separate slices)
All-backends repro after the fix (12 runs each):
```text
backend 0: ok=12 timeout=0 abort=0   (FIXED)
backend 1: ok=6  timeout=0 abort=6
backend 2: ok=7  timeout=0 abort=5
backend 3: ok=5  timeout=0 abort=7
backend 4: ok=12 timeout=0 abort=0   (already green)
```
Hang gone everywhere (No.4 universal). Remaining aborts are in the **tracing
collector** (`pcc_gc_collect_tracing` -> `pcc_gc_sweep_unreachable` ->
`pcc_gc_finalize_unreachable`), which backends 1/2/3 use instead of backend 0's
`py_gc_collect`. Signatures:
- backend 1, 2: `py_list_append` assert `l->h.type_tag == PY_TYPE_LIST`
  (a list freed/corrupted while still appended to).
- backend 3: `py_decref: refcount underflow` (same as backend 0 was).

Hypothesis: the tracing sweep frees objects the refcount path is concurrently
freeing (white + rc==0 in-flight) and/or whose root the final cut missed. The
backend-0 analogue (skip in-flight rc==0 objects) likely fixes backend 3; 1/2's
"live list freed" may be a root/sweep-candidate staleness gap. Per AGENTS.md
("one backend per PR"), handle these as focused follow-up slices with their own
verification (the tracing sweep is shared across 1/2/3, so a single principled
in-flight guard there must be re-validated on backends 0/4 and the bootstrap).

### Experiment: tracing-sweep in-flight guard (rc<=0) — partial, REVERTED
Tried the backend-0 analogue in `pcc_gc_sweep_unreachable` (C + py mirror): skip
sweep candidates with `pcc_refcount_load(&h->refcount) <= 0`. All-backends repro
(15 runs each): backend 0 ok=15, backend 4 ok=15 (no regression), **backend 3
ok=10/15 (improved from ~5/12)**, backends 1/2 ~unchanged (ok=4, ok=5; still
~70% abort). So the in-flight guard is correct and helps backend 3 but does NOT
make any tracing-backend gate green; backends 1/2 are dominated by the distinct
"live list freed" bug (root/sweep-candidate gap). Reverted to keep this slice's
deliverable = backend 0 + universal hang only (clean, gate-green, bootstrap-
verified). The tracing slices will re-derive this guard PLUS the backend-1/2
root fix and validate the full GC suite.

## Report (backend 0 + universal hang)
Landed in this slice (verified): No.3 (backend-0 cycle collector skips raw
rc<=0 in-flight objects, C + py mirror) and No.4 (safepoint-aware bounded
condition wait for Lock.acquire / Event.wait via `pcc_cond_timedwait_ms`, with
unlock-before-safepoint). Gates: `..._all_backends[0]` 1 passed (11.21s);
`test_full_three_stage_bootstrap_self` 1 passed (53.86s); host-pcc repro 25/25;
focused GC subset (`test_gc_g1_cycle_collector`, `test_gc_semantics`,
`test_gc_regression_bugs`, `test_gc_root_precision`, `test_gc_threading_substrate`)
36 passed (25.85s) — no GC regression from the cycle-collector guard.
DENIED/deferred: tracing-sweep guard alone (incomplete; see experiment above).
Open follow-up: backends 1, 2, 3 on the same committed gate (tracing collector;
one investigation + slice per backend).

## No.1 ASan alloc/free1/free2 stacks
### Code Change
None yet — diagnostic step. Build the runtime archive with
`-fsanitize=address -g -fno-omit-frame-pointer` and link the repro binary with
ASan so the heap-use-after-free / double-free reports the allocation site, the
first free (collector `py_gc_dealloc_unreachable` vs owner `py_decref`), and the
second free.
### pending
Next probe to run.

## Next Steps
1. Get ASan stacks (Proposal No.1) to choose between the candidate windows.
2. Patch the smallest correct seam in `pcc/py_runtime/src/py_obj_gc.c` /
   `py_gc_backend.c` (+ pcc-Python mirror `py_obj_gc.py`/`py_gc_backend.py`),
   not generated codegen, unless ASan shows a codegen rooting gap.
3. Re-run the standalone repro 100+ times, then the committed gate for backend 0,
   then all backends, then the full self bootstrap.
