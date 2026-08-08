# GC reference implementations

Source code snapshots of the five real GC algorithms that pcc's runtime
backends mirror.  Kept in tree (rather than in `/tmp/`, where this content
lived ephemerally for several months) so that:

1. agents can grep / read these files directly when fixing pcc's GC port,
2. URL drift / upstream rewrites do not break investigations,
3. each reference is paired with the pcc backend ID it informs.

Backend mapping (see `docs/tasksV2.md > Current completion map > Backend #N`
and `docs/investigations/gc-backend-selection-matrix.md`):

| pcc backend | Algorithm                  | Reference subdir | Status in pcc |
|-------------|----------------------------|------------------|---------------|
| **#0**      | refcount + STW cycle       | `python/`        | production / default |
| **#1**      | incremental tricolor mark-sweep | `lua/`     | production-test-green (color bits, frame/module/class roots, write barrier, bounded step, sweep, allocation debt, `gcpause` / `gcstepmul` env tuning, max-pause telemetry, explicit-collect tracing sweep with live roots, finalizer/resurrection fixes, and object-registry performance gates green) |
| **#2**      | concurrent mark-sweep      | `go-greentea/`   | correctness-green threaded prototype (shares #1 tracing core; has pthread worker startup, bounded queue, active-cycle gray barrier, thread-local buffered gray-ticket flushes, allocation-work tracing, mutator assist, stop/join/restart lifecycle, worker-side STW mark termination, multi-mutator TSan stress, and sweep/allocation TSan proof; still no full Go-style work-buffer/drain model or concurrent span/object sweep) |
| **#3**      | generational young/old     | `ocaml/`         | production-facing focused gates green (young/old flags, remembered-set promotion, C-runtime and pcc-Python runtime-high minor bump arenas, constructor-preserved young/minor flags, arena-aware deallocation, arena telemetry, C-runtime and pcc-Python runtime-high `PCC_WITH_THREADS=1` thread-local current minor blocks with owner ids, C/pcc-Python refill-time promotion coverage, scalar remembered-child copy-oldification with lazy read-barrier slot update, owned-slot eager rewrite for list/tuple/dict/set/instance-style probes plus function/iterator/generator/coroutine/task/exception/weakref/thread/class metadata slots, registered native frame-root and scheduler-root rewrite, coroutine scheduler-root matrix, and inactive-source cleanup after copied minor forwarding; cross-domain remembered-set sharing and broader pcc-Python threaded object-index/object-list synchronization remain open) |
| **#4**      | colored relocating / modern GenZGC | `zgc/` | latest-reference pack from OpenJDK `jdk-27+21`; `zgc/UPSTREAM.md` records the 2026-05-14 freshness check against current OpenJDK `master`; pcc implementation is production-facing for forwarding/read-barrier/object relocation, main containers and many reference-bearing runtime objects, scheduler-root healing, first default-young / bounded-aging generation slice, young/old population telemetry, old-to-young store-buffer telemetry with C-runtime cross-thread mutator-local medium-buffer flushing, bounded-batch drain signals, owner+slot remembered-set side-table signals, slot-page bitmap remembered-set telemetry, small/medium/large page-class live telemetry, synthetic ZPage capacity/used/fragmentation/ratio/policy-score telemetry, small/medium evacuation selector threshold telemetry, first large-page evacuation-policy slice, and evacuation-backlog signals; still lacks full GenZGC young/old policy, true ZPage allocation/evacuation, real fragmentation policy, ZPage-integrated remembered-set bitmap, native-handle Thread/File/thread-sync relocation protocols, pcc-Python threaded mirror flushing, and complete reference-updating coverage |

User-mode scheduling is a cross-backend GC concern rather than a sixth
backend. See `user-mode-scheduling/` for OpenJDK virtual-thread, Go goroutine,
and CPython coroutine/task references. The current stackless/cooperative
coroutine scheduler root matrix is green across backend 0..4 and a minimal
carrier run loop exists, but production virtual threads still require generated
suspend/resume lowering, a real carrier pool, and complete blocking
integration.

Precise native safepoints are another cross-backend concern. See
`llvm-statepoint/` for the pinned LLVM 20.1.8 `gc.statepoint` / stack-map
reference, its source-attributed relocation and liveness notes, and the gap
table against pcc's current frame-root and load/store healing contracts. LLVM
is treated there as a design oracle, not as pcc's runtime or backend owner.

## What's in each subdir

### `python/` — backend #0 reference

CPython 3.13 cycle collector and free-threaded refcounting:

| File | What it shows |
|---|---|
| `gcmodule.c` | The CPython generational + tracing cycle collector. ``gc_collect_main``, ``visit_decref``, ``move_unreachable``, ``handle_legacy_finalizers`` etc. — the literal algorithm pcc backend #0 reproduces. |
| `gc_free_threading.c` | PEP 703 / no-GIL variant of the same collector. Reference for backend #0 → backend #7 free-threaded path (roadmap §4 GC-7). |
| `CPYTHON_STRING_INTERNING.md` | CPython's docstring on `_PyUnicode_Intern{Mortal,Immortal,Static}` and the singleton state machine. Relevant when the pcc compiler-side hot path interns identifiers. |

### `lua/` — backend #1 reference

Lua 5.4's single-threaded incremental tricolor collector:

| File | What it shows |
|---|---|
| `lgc.c`     | The full mark/atomic/sweep state machine, including the *step debt* / *pacer* mechanics that pcc backend #1 currently lacks. |
| `lobject.h` | `GCObject` header + color/age/finalized bits — the layout pcc's tricolor `flags` mirror. |
| `lstate.h`  | Per-state GC fields (`GCdebt`, `gcpause`, `gcstepmul`, `gray`, `grayagain`) — the pacer state pcc still has to add. |

### `go-greentea/` — backend #2 reference

Go runtime concurrent mark-sweep (the "greentea" / new GC iteration):

| File | What it shows |
|---|---|
| `mgc.go`        | (kept in `refs_docs/go_mgc.go.html` at top level; not duplicated here) |
| `mgcmark.go`    | Concurrent mark workers, mark queue, work-stealing. The shape pcc backend #2 still needs. |
| `mgcsweep.go`   | Concurrent sweep, span cache. |
| `mgcscavenge.go`| Background returning-memory-to-OS path. |
| `mwbbuf.go`     | The write barrier batched buffer — the pattern pcc's `pcc_gc_store_ptr` should grow into for #2. |

### `ocaml/` — backend #3 reference

OCaml 5 generational major + minor:

| File | What it shows |
|---|---|
| `gc.h`        | Public GC interface (alloc, store, root registration). |
| `major_gc.h`  | Major heap data structures, mark stack interface. |
| `minor_gc.c`  | Domain-local minor heap allocator + remembered-set promotion. The bump-pointer young heap pcc backend #3 still needs. |

### `zgc/` — backend #4 reference

OpenJDK ZGC is now a modern generational colored relocating collector.
The snapshot in `zgc/` is no longer the old five-file JDK 21-era research
slice; it is a pinned reference pack from OpenJDK `jdk-27+21`
(`4e1dd4daba5a619bbbc9720fdd509e609d6f0032`), fetched on 2026-05-14.
See `zgc/MANIFEST.json` for exact upstream paths, raw URLs, byte counts, and
SHA-256 hashes, and `zgc/UPSTREAM.md` for the source contract.

JEP status matters for pcc #4:

| JDK | ZGC status |
|---|---|
| JDK 21 | JEP 439: Generational ZGC introduced. |
| JDK 23 | JEP 474: generational mode became the default. |
| JDK 24 | JEP 490: non-generational mode was removed. |

The pcc #4 target should therefore be evaluated against modern GenZGC, not
against the removed single-generation mode.

Reference freshness rule: `zgc/UPSTREAM.md` and `zgc/MANIFEST.json` record
the exact source snapshot. As of 2026-05-14 the implementation work is
tracked against the JDK 27 EA / `jdk-27+21` source snapshot. Do not treat
"modern GenZGC" in this document as a permanent latest-version claim; before
declaring backend #4 current again, refresh the snapshot metadata and compare
the local `z*` source set against the current OpenJDK JDK mainline.

| File group | Representative files | What it shows |
|---|---|---|
| Heap / collection entry | `zCollectedHeap.*`, `zHeap.*`, `zDriver.*`, `zDirector.*` | Heap API, cycle scheduling, collection orchestration. |
| Generations | `zGeneration.*`, `zGenerationId.hpp` | Young/old generation split that pcc #4 does not yet implement. |
| Barriers | `zBarrier.*`, `zBarrierSet.*`, `zBarrierSetRuntime.*` | Load-barrier and runtime-barrier shape for colored relocating references. |
| Remembered sets / stores | `zRemembered*`, `zStoreBarrierBuffer.*` | Cross-generation remembered-set and store-buffer mechanics needed for GenZGC. |
| Forwarding / relocation | `zForwarding*`, `zRelocate.*`, `zRelocationSet*` | Forwarding table, relocation set, and relocation selector design. |
| Pages / allocation | `zPage*`, `zPageAllocator.*`, `zObjectAllocator.*`, `zThreadLocalAllocBuffer.*` | Page-based allocation, TLAB path, age metadata, page tables. |
| Marking / roots | `zMark*`, `zRootsIterator.*`, `zWeakRootsProcessor.*`, `zReferenceProcessor.*`, `zResurrection.*` | Concurrent marking, root iteration, weak/reference processing, resurrection semantics. |
| Workers / verification | `zWorkers.*`, `zRuntimeWorkers.*`, `zVerify.*`, `zStat.*` | Worker substrate, verification, and telemetry hooks. |

### `user-mode-scheduling/` — coroutine / virtual-thread GC roots

Reference material for the execution model that all tracing/moving backends
must eventually support:

| Runtime | Files | What it shows |
|---|---|---|
| OpenJDK Loom | `openjdk/VirtualThread.java`, `openjdk/Continuation.java`, `openjdk/continuation.hpp` | Virtual-thread scheduler state, continuation mount/unmount, pinning, and VM-side continuation frame walking. |
| Go runtime | `go/proc.go`, `go/runtime2.go`, `go/stack.go` | Goroutine scheduler state, G/M/P layout, stack ownership during GC scans, GC assist fields, stack growth/preemption. |
| CPython | `cpython/genobject.c`, `cpython/pycore_frame.h`, `cpython/asyncio_tasks.py` | Python generator/coroutine object semantics, suspended frame states, asyncio Task await-chain scheduler roots. |

## Provenance

- Snapshots were taken from upstream repositories at the URLs recorded in
  `pcc_multi_year_roadmap.md > §14 Source links for AI agents`; ZGC also has
  an exact pinned manifest in `zgc/MANIFEST.json`.
- They are not a full mirror — only the `*.c` / `*.cpp` / `*.h` / `*.hpp` /
  `*.go` files that pcc backend authors actually re-read while porting.
- If a reference looks too old, re-fetch it from the URL in the roadmap and
  replace the file inline.  Do not delete a file while keeping the README
  entry.

## Cross-link to xfails

The pcc test suite encodes the missing pieces of these algorithms as
``@pytest.mark.xfail`` markers.  See `tests/test_gc_g1_cycle_collector.py`,
`tests/test_gc_resurrection.py`, `tests/test_gc_finalizer_corner.py` and
`tests/test_gc_api.py`; tags ``Phase G1`` / ``Phase G2`` / ``Phase G3`` /
``Phase G5`` correspond to roadmap §4 ``GC-2`` / ``GC-3`` / ``GC-4`` / GC API.
When one of those `xfail` becomes `xpassed` (`X` in pytest output), the
matching reference algorithm here is either no longer needed or has been
ported to production; remove the marker in the same PR that lands the port.
