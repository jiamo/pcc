# Investigation: Loom-shaped virtual threads for pcc

## Status
active

## Problem Description

pcc currently has a synchronous no-suspension coroutine thunk and pthread-based
native threading. The requested target is Loom-style virtual threads: many
user-mode execution contexts multiplexed over carrier OS threads, with real
park/unpark, scheduler queues, blocking integration, and GC-visible suspended
execution state.

## OpenJDK Reference

The OpenJDK reference pack was refreshed on 2026-05-15:

- directory: `docs/refs_docs/gc-research/user-mode-scheduling/openjdk/`
- upstream: `https://github.com/openjdk/jdk`
- commit: `b9778ccb475891efd6347f7645b9a53c011f70fd`
- manifest: `docs/refs_docs/gc-research/user-mode-scheduling/openjdk/MANIFEST.json`

Relevant upstream seams:

- `VirtualThread.java`: state machine, mount/unmount, scheduler, park/unpark.
- `Continuation.java`, `ContinuationScope.java`, `StackChunk.java`: continuation
  API and heap-owned stack-chunk model.
- `continuationFreezeThaw.cpp`, `continuation*.hpp`: HotSpot freeze/thaw and
  continuation frame access.
- `Poller.java`, `NioSocketImpl.java`, `SocketChannelImpl.java`: blocking I/O
  integration.
- `CarrierThread.java`, `Blocker.java`, `LockSupport.java`: carrier compensation,
  pinning/blocking boundaries, and park support.

## Current pcc Gap

pcc is missing the JVM preconditions Loom relies on:

- bounded safepoints at loop back-edges, function entries, and blocking/alloc
  boundaries;
- PC-indexed stack maps or an equivalent precise suspended-frame root map;
- a continuation object that owns saved frames and exposes roots to GC;
- scheduler queues as traceable roots;
- fiber-aware blocking primitives and I/O poller integration;
- pinning diagnostics for native/libpython/foreign blocking regions;
- self-bootstrap evidence that the new runtime does not add `py_cpy_*`.

The existing `PyCoroutineObject` is not a continuation. It stores entry,
captures, args, result, and state flags, but `py_await()` runs it synchronously
to completion. There is no saved frame, resume PC, stack chunk, scheduler, or
I/O park path.

## Decision

Use a Loom-shaped design rather than a full CPS rewrite as the primary route:

1. Keep normal codegen where possible.
2. Add safepoints and precise root maps first.
3. Introduce a traceable `PyContinuation` / stack-chunk representation.
4. Build virtual threads as scheduler-managed continuations.
5. Treat unsupported blocking/native regions as pinned in v0, with diagnostics.

This is still a multi-phase implementation. The value of Loom is not that pcc
can copy JVM internals directly; the value is that it validates a stack-chunk
continuation architecture and avoids making CPS lowering the first dependency.

## Implementation Phases

Phase 0: reference and gap tests.

- Keep the OpenJDK reference pack current.
- Add gap tests proving current coroutine behavior is synchronous only.
- Add xfail/gap tests for missing scheduler, missing suspended-frame GC roots,
  and missing preempt/yield safepoints.

Phase 1: safepoints.

- Close `No.41`.
- Add bounded safepoint/yield coverage for loop back-edges, function entries,
  allocation-heavy paths, and blocking boundaries.

Phase 2: precise root maps.

- Extend current dynamic frame-root registration so suspended frames can expose
  all live PyObject references.
- Backend #4 must be able to rewrite forwarded references inside suspended
  frames.

Phase 3: continuation and stack chunk.

- Add a `PyContinuation` object that owns suspended frame/stack chunks.
  First slice landed 2026-05-16 with copied heap slots, `resume_pc`, and
  mount/unmount root registration.
- Support mount/unmount and resume semantics. The object/root mechanics exist;
  generated resume lowering is still open.
- Preserve exception/finally state and locals across suspension.

Phase 4: scheduler.

- Add carrier pool, ready queue, timer queue, park/unpark, and wakeup paths.
  First slice landed 2026-05-16 with a cooperative ready queue plus
  start/park/unpark/poll-ready state transitions. Phase 5 added the first
  timer and poll wakeup substrate; full carrier scheduling policy remains open.
- Scheduler queues are GC roots.

Phase 5: blocking integration.

- Add fiber-aware sleep, locks/conds, and socket/file I/O poller paths.
  First slice landed 2026-05-16 with fiber-aware sleep, fd poll wait entries,
  and timer/I/O poller APIs; broad lock/cond/socket/file integration remains
  open.
- Pin unsupported native/libpython/foreign blocking regions and expose metrics.

Phase 6: comparison matrix.

- Compare existing coroutine thunk, pcc virtual thread, and OS thread.
- Measure throughput, latency, RSS, GC pause, pinning rate, bootstrap impact,
  and backend #4 relocation behavior.

## Required Gates

- `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  must stay green after every phase.
- No new `py_cpy_*` fallback in the strict self-host closure.
- Backend #4 tests must cover suspended-frame relocation and scheduler queue
  relocation before virtual threads can be production.
- Threaded GC safepoint blocker must be closed before virtual threads can be
  default-enabled.

## Current Status

Reference pack exists. Phase 0 gap coverage started in
`tests/python/test_virtual_threads_gap.py`:

- one passing baseline test records that current coroutine/`asyncio.sleep`
  behavior is synchronous no-suspension;
- initially four strict xfail tests marked the missing continuation object,
  scheduler API, suspended-frame GC hooks, and fiber-aware blocking/poller API;
  after Phase 2/3, the remaining strict xfails are scheduler API and
  fiber-aware blocking/poller API.

Implementation has started at the shared safepoint layer. The first No.41
slice makes `pcc_gc_alloc()` poll `pcc_thread_safepoint()` without running full
GC steps on non-threaded allocations; the non-threaded `pcc_thread_safepoint()`
implementation is the existing no-op. This is necessary but not sufficient for
virtual threads: pure compute loops still need codegen loop-backedge/function-entry
safepoints before real continuation scheduling can make progress guarantees.

## Update: Phase 1 safepoint lowering

The next No.41 slice adds generated-code safepoints:

- every `while` back-edge now flows through a latch block that polls
  `pcc_thread_safepoint()`;
- `for` lowering polls at step/latch paths, including range, native iterator,
  object iterator, async iterator, CPython iterator, and comprehension loops;
- typed-int low-IR while back-edges and function entries poll the same runtime
  hook;
- user functions, class methods, generator wrapper/resume functions, module
  top-init, and program main poll at entry.

This is still only Phase 1. It gives the future scheduler and STW collector
bounded cooperative yield points in generated Python code, but it does not
create a continuation object, stack chunk, root map, carrier scheduler, poller,
or pinning diagnostics. Those remain Phase 2-5.

Validation for the current roadmap/gap state:

```text
tests/python/test_virtual_threads_gap.py
1 passed, 4 xfailed in 0.04s
```

The xfails are intentional strict gaps for Phase 2-5. No virtual-thread
production capability is claimed until continuation objects and suspended-frame
GC scanning exist.

## Update: Phase 2 Root Hook Substrate

The first Phase 2 slice now exists as runtime substrate, not a virtual-thread
runtime:

- `pcc_gc_register_continuation_root(frame_map, slots)` and
  `pcc_gc_unregister_continuation_root(slots)` register a suspended-frame-like
  root map with a contiguous `PyObject **` slot array. The frame-map v0 format
  remains the existing `int32 slot_count` shape.
- `pcc_gc_trace_continuation_roots()` exposes the registered continuation roots
  to tracing/observability paths.
- `pcc_gc_rewrite_continuation_roots()` resolves backend #4 forwarding entries
  inside registered suspended roots.
- `pcc_gc_continuation_root_slot_count()` is folded into
  `pcc_gc_coroutine_root_score()`.
- Backend #0 keeps generated-code frame-root registration on a default fast
  no-op path for self-bootstrap performance, but explicit
  `pcc_gc_set_backend(0)` enables the same frame-root observability gate used by
  the backend matrix tests. Continuation suspended-root hooks are independent
  of that active-frame fast path.
- The pcc-Python runtime mirror and runtime ABI table expose the same surface.

Validation:

```text
tests/python/test_virtual_threads_gap.py
2 passed, 3 xfailed in 0.18s

tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py
5 passed, 3 xfailed in 13.15s

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime_pcc_py.a
passed

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.30s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 74.28s
```

Running the broader coroutine/root gate exposed and fixed an adjacent
pcc-Python object-index lifetime bug; see
`gc-pcc-py-object-index-freeing-reuse.md`.

At the end of the Phase 2 slice, the remaining strict xfails still covered the
missing `PyContinuation` / stack-chunk object, virtual-thread scheduler, and
fiber-aware blocking/poller API. No production virtual-thread capability was
claimed.

## Update 2026-05-16: Phase 3 minimal continuation object

Phase 3 now has a concrete first slice, still below production virtual-thread
runtime:

- `PyContinuationObject` owns a `PyContinuationStackChunk` with copied heap
  slots, a frame-map v0 slot count, a `resume_pc`, and mounted/unmounted state.
- `py_continuation_new(frame_map, slots, resume_pc)` copies the current frame
  slots into the heap stack chunk and registers the chunk as suspended roots.
- `py_continuation_mount(cont, slots_out)` unregisters the suspended-root
  entry and can copy saved slots back into active root slots.
- `py_continuation_unmount(cont, slots_in, resume_pc)` copies active slots back
  into the stack chunk and re-registers the suspended-root entry.
- Backend #4 relocation root rewriting updates stack-chunk slots through
  `pcc_gc_rewrite_continuation_roots()`; relocating a continuation retargets
  its remembered slots and continuation-root node to the copied stack chunk.
- The C runtime, pcc-Python runtime mirror, and `runtime_abi.py` expose the
  same API surface.

Validation:

```text
tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py
7 passed, 2 xfailed in 12.54s

tests/python/test_gc_coroutine_scheduler_roots_production.py::test_continuation_object_mount_unmount_scans_and_rewrites_slots
1 passed in 4.41s

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime.a
passed

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime_pcc_py.a
passed
```

At the end of the Phase 3 slice, the remaining strict xfails covered the
missing virtual-thread scheduler API and fiber-aware blocking/poller API. That
still was not production virtual threads: there was no carrier scheduler,
park/unpark, timer queue, blocking I/O integration, pinning diagnostics, or
coroutine-vs-virtual-thread comparison gate yet.

## Update 2026-05-16: Phase 4 cooperative scheduler API

Phase 4 now has a first cooperative scheduler slice:

- `PY_TYPE_VIRTUAL_THREAD` objects own a continuation slot, result slot, state,
  queued bit, and pin counter.
- `py_virtual_thread_start()` / `py_virtual_thread_unpark()` enqueue ready
  virtual threads onto `pcc_vthread_ready_queue`.
- `py_virtual_thread_park()` only marks state `PARKED`; it does not block the
  carrier OS thread.
- `py_virtual_thread_poll_ready()` lets a carrier/cooperative loop pull one
  ready virtual thread and mark it `RUNNING`.
- `py_virtual_thread_complete()` stores a result and marks the virtual thread
  `DONE`.
- The ready queue stores entries as GC roots. Cycle-GC, backend #3 promotion,
  and backend #4 tracing/relocation know `PY_TYPE_VIRTUAL_THREAD`; queued
  virtual threads reject backend #4 relocation because the queue entry owns the
  current address.

Validation:

```text
tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py
9 passed, 1 xfailed in 16.74s

tests/python/test_gc_coroutine_scheduler_roots_production.py::test_virtual_thread_scheduler_ready_park_unpark_is_cooperative
1 passed in 4.36s

tests/python/test_gc_coroutine_roots.py tests/python/test_gc_root_precision.py
7 passed in 75.32s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.55s

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime.a
passed

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime_pcc_py.a
passed
```

The only remaining strict xfail is Phase 5 fiber-aware blocking/poller API.
Production virtual threads are still not claimed: timer queue, blocking I/O
integration, pinning diagnostics, generated resume lowering, and comparison
workloads are still open.

## Update 2026-05-16: Phase 5 timer/poller/pinning substrate

Phase 5 now has the first fiber-aware blocking/poller substrate, and no strict
virtual-thread xfail remains.

- `py_virtual_thread_sleep(vt, delay_ms)` parks a virtual thread on a runtime
  timer queue; `py_virtual_thread_poll_timers()` moves expired timers to the
  ready queue and `py_virtual_thread_timer_count()` exposes pending timers.
- `py_virtual_thread_block_on_fd(vt, fd, events, timeout_ms)` parks a virtual
  thread on a poll-backed fd wait; `py_virtual_thread_poll_io(timeout_ms)`
  drains ready or expired waits and `py_virtual_thread_io_wait_count()` exposes
  pending waits.
- `py_virtual_thread_pin_enter()` / `py_virtual_thread_pin_leave()` plus
  pin-count and event-count accessors provide the first diagnostics for native
  or blocking regions that pin the carrier.
- The C runtime, public header, pcc-Python ABI declarations, and symbol gap
  tests expose the same API surface.

Validation:

```text
tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py
11 passed in 22.15s

tests/python/test_virtual_threads_gap.py --runxfail
5 passed

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime.a
passed

env -u LC_ALL uv run make -C pcc/py_runtime libpy_runtime_pcc_py.a
passed
```

This closes the strict xfail / gap-test phase. Production virtual threads are
still not claimed: generated suspend/resume lowering, broad lock/cond/socket
and file integration, carrier scheduling policy, and the Phase 6
coroutine-vs-virtual-thread-vs-OS-thread comparison gate remain open.

## Update 2026-05-16: Phase 6 comparison report gate

Phase 6 now has a repeatable comparison-report gate, but this is still a
measurement gate rather than production virtual-thread capability.

- `pcc/virtual_thread_comparison.py` defines the report schema and parser for a
  runtime probe that compares three workload rows: current coroutine thunk,
  pcc virtual-thread substrate, and OS thread creation/join.
- `scripts/virtual_thread_comparison.py` can run a real temporary
  `PCC_WITH_THREADS=1` runtime probe, parse an existing probe output, or emit
  deterministic dry-run data for CI/schema checks.
- The report normalizes wall time, per-op latency, throughput, RSS delta,
  measured GC collect pause, and pinning rate. It can optionally merge a
  `PCC_BOOTSTRAP_PROFILE_DIR` summary so bootstrap impact appears in the same
  artifact.
- The verdict field explicitly keeps `production_virtual_threads=false` and
  lists the remaining blockers: generated suspend/resume lowering, broad
  lock/cond/socket/file integration, and carrier scheduling policy beyond
  cooperative polling.

Validation:

```text
tests/python/test_virtual_thread_comparison_report.py
3 passed in 0.23s

scripts/virtual_thread_comparison.py --dry-run --format text --iterations 10
passed

scripts/virtual_thread_comparison.py --iterations 10 --format json
passed

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 65.20s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 56.92s
```

This closes the Phase 6 report/gate substrate. It does not close No.42: pcc
still lacks generated continuation suspension/resume, a full carrier scheduler,
and broad fiber-aware lock/cond/socket/file lowering.

## Update 2026-05-17: minimal carrier run loop

The scheduler substrate now has a minimal carrier-facing run loop instead of
only manual `poll_ready()` / `complete()` calls:

- `py_virtual_thread_run_once()` polls timer and IO wakeups, dequeues one ready
  virtual thread, invokes the continuation `resume_pc` as a no-arg resume stub,
  and marks the thread done if the resume stub leaves it in `RUNNING` state.
- `py_virtual_thread_run_until_idle(max_steps)` repeats that bounded step until
  no ready work remains or the caller's step limit is hit.
- The public header and `runtime_abi.py` expose both functions, and the
  comparison probe now uses the carrier run loop for the `pcc_virtual_thread`
  workload row.

Validation:

```text
tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py tests/python/test_virtual_thread_comparison_report.py
15 passed in 25.17s

tests/python/test_virtual_threads_gap.py --runxfail
5 passed in 0.03s

scripts/virtual_thread_comparison.py --iterations 10 --format json
passed; comparison_gate_complete=true

tests/python/test_gc_coroutine_roots.py
6 passed in 108.83s

bash scripts/run_coroutine_scheduler_roots_gate.sh
13 passed in 134.25s
```

This closes the "no carrier run-loop entrypoint" gap. It still does not close
No.42 production virtual threads: generated suspend/resume lowering, a typed
resume-call protocol with saved locals/temporaries, a real carrier pool, and
broad lock/cond/socket/file lowering remain open.

## Update 2026-05-18: bounded carrier-pool drain

The scheduler substrate now has a first real carrier-pool API, not only a
single-thread cooperative carrier loop:

- `py_virtual_thread_run_carrier_pool(carrier_count, max_steps)` starts up to a
  bounded number of `PCC_WITH_THREADS=1` carrier OS threads.
- Carriers share a bounded step budget, repeatedly call
  `py_virtual_thread_run_once()`, and join before the API returns.
- `py_virtual_thread_carrier_count()` is atomically readable while carriers are
  active; the focused regression observes more than one carrier from inside a
  resume stub.
- The comparison probe now batches virtual threads and drains them through
  `py_virtual_thread_run_carrier_pool(2, iterations)`.

Validation:

```text
tests/python/test_gc_coroutine_scheduler_roots_production.py::test_virtual_thread_bounded_carrier_pool_drains_ready_queue
1 passed in 4.75s

tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py tests/python/test_virtual_thread_comparison_report.py
16 passed in 33.35s

scripts/virtual_thread_comparison.py --iterations 10 --format json
passed; comparison_gate_complete=true

bash scripts/run_coroutine_scheduler_roots_gate.sh
14 passed in 145.25s

make -C pcc/py_runtime libpy_runtime.a
passed

make -C pcc/py_runtime libpy_runtime_pcc_py.a
passed

make -B -C pcc/py_runtime PCC_WITH_THREADS=1 libpy_runtime.a
passed

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 59.94s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 69.15s
```

This closes the "no real carrier-pool substrate" part of the prior blocker.
No.42 is still not production: pcc still lacks generated suspend/resume
lowering, a typed resume-call protocol carrying saved locals and temporaries,
persistent/work-stealing carrier-pool policy, and broad lock/cond/socket/file
blocking lowering.

## Update 2026-05-18: generated typed resume and persistent carriers

The direct-function virtual-thread slice now has generated lowering and a typed
resume ABI. This is still a stackless/direct-call subset, not arbitrary Loom
stack freezing:

- New compiler-recognized module `pcc.virtual_thread` lowers
  `spawn(fn, *args)`, `run`, `run_until_idle`, `carrier_pool_start`,
  `carrier_pool_stop`, `result`, `state`, `sleep`, and `block_on_fd` without
  libpython fallback.
- For a direct user function target, codegen emits an internal
  `__vthread_resume_N(PyObject *vthread, PyObject *continuation)` thunk.
  `spawn()` boxes the call arguments into continuation slots using the target
  formal annotations; resume restores them through `py_continuation_get_slot()`,
  calls the original user function, boxes the return value, and completes the
  virtual thread.
- `PyContinuationObject` now stores `resume_abi`.
  `py_continuation_new_typed()` marks the continuation as
  `PCC_CONTINUATION_RESUME_ABI_VTHREAD`; `py_virtual_thread_run_once()` dispatches
  typed continuations as `int64_t (*)(PyObject *, PyObject *)` and keeps the
  legacy no-arg resume ABI for old probes.
- `py_virtual_thread_current()` exposes the currently mounted virtual thread to
  runtime blocking helpers. `py_virtual_thread_result()` reads the stored
  completion result.
- `py_virtual_thread_carrier_pool_start()` / `py_virtual_thread_carrier_pool_stop()`
  provide a persistent carrier-pool lifecycle. Current policy is global-queue
  work-sharing over the existing ready/timer/IO queues, not per-carrier deque
  work stealing.
- Runtime `threading` lock/rlock/event/condition/semaphore/thread-join and file
  open/read/write/close boundaries now pin/unpin the current virtual thread and
  increment pin telemetry. This records and protects carrier-pinning regions; it
  is not yet true fiber-parked lock/cond/socket/file lowering.

Validation:

```text
tests/python/test_gc_coroutine_scheduler_roots_production.py::test_virtual_thread_typed_resume_and_persistent_pool_pin_blocking
1 passed in 4.82s

tests/python/test_virtual_thread_frontend.py
3 passed in 1.74s

tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py tests/python/test_virtual_thread_comparison_report.py tests/python/test_virtual_thread_frontend.py
20 passed in 37.58s

make -B -C pcc/py_runtime libpy_runtime.a
passed

make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
passed

tests/python/test_bootstrap_gate_baseline.py tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed, 4 skipped in 58.49s
```

This closes the concrete blockers for the supported direct-function slice:
generated typed resume lowering, continuation saved slots for arguments,
persistent carrier lifecycle, and blocking-region pinning instrumentation.
`production_virtual_threads` remains false for the full Loom target because pcc
still lacks arbitrary stackful/frame-state suspension, surviving temporary and
exception/finally preservation across suspension points, per-carrier deque work
stealing / carrier compensation, and true fiber-parked lock/cond/socket/file
lowering.

## Update 2026-05-18: production-scoped stackless virtual threads

The previous direct-function slice has been extended into pcc's production
scope for explicit suspension-point virtual threads. The implementation still
intentionally does not claim JVM/Loom native-stack copying; it uses the Python
frontend's generated generator state machine as the stackless frame model.

Changes:

- `pcc.virtual_thread.spawn()` now recognizes targets containing
  `pcc.virtual_thread.yield_now()`, `sleep_current()`, or
  `block_current_on_fd()` and routes them through generator/state-machine
  lowering.
- Runtime added `py_virtual_thread_resume_generator(vthread, continuation)`.
  Continuation slot 0 owns the generated generator object; carrier resume calls
  `py_gen_next()`, requeues on yield, stays parked if the suspension primitive
  parked the vthread, and completes from `StopIteration.value`.
- `yield_now()`, `sleep_current()`, and `block_current_on_fd()` are generated
  suspension points. The latter two bind the current virtual thread to the
  timer/fd poller before yielding.
- Carrier pools now allocate carrier-local ready queues. Carriers drain their
  own queue, steal from peers, then fall back to the global queue. The policy is
  observable via `py_virtual_thread_carrier_steal_count()`.
- `Lock.acquire()`, `Event.wait()`, `Condition.wait()`, and
  `Semaphore.acquire()` lower to virtual-thread-aware runtime calls when emitted
  inside the generated state machine. Contended operations enqueue the current
  virtual thread as a waiter, park it, and yield the carrier; release/set/notify
  unparks waiters.
- File operations still pin because the current native file fast path is
  stdio-based. Socket-module-specific lowering is not present; fd-level polling
  is available through `block_current_on_fd()`.

Validation:

```text
tests/python/test_virtual_thread_frontend.py
6 passed in 2.97s

tests/python/test_virtual_thread_frontend.py tests/python/test_gc_coroutine_scheduler_roots_production.py tests/python/test_virtual_threads_gap.py tests/python/test_virtual_thread_comparison_report.py
23 passed in 38.16s
```

The comparison verdict now sets `production_virtual_threads=true` for this
scoped pcc capability and records the remaining limitations separately: no
Loom-style arbitrary native-stack copying, and no socket/file module-specific
async wrappers beyond fd poller/file pin diagnostics.
