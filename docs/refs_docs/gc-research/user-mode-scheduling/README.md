# User-mode scheduling references for GC work

This directory holds source snapshots for user-mode execution models that are
deeply coupled to precise GC root handling. These are not additional pcc GC
backends. They define a cross-backend production gate: every tracing or moving
backend must treat suspended user-mode execution contexts as roots.

## Provenance

OpenJDK was refreshed on 2026-05-15 from current `openjdk/jdk` mainline
HEAD and now has a ZGC-style pinned manifest:
`openjdk/UPSTREAM.md` and `openjdk/MANIFEST.json`.

The Go and CPython snapshots were taken on 2026-05-08 from these upstream
`HEAD` revisions:

| Runtime | Commit | Files |
|---|---:|---|
| OpenJDK | `b9778ccb475891efd6347f7645b9a53c011f70fd` | See `openjdk/MANIFEST.json` |
| Go | `16449179ece28377d5e08684a00cdbf597679f5b` | `go/proc.go`, `go/runtime2.go`, `go/stack.go` |
| CPython | `f5c75351def83602b5b23c1fba361b7de8ffabc7` | `cpython/genobject.c`, `cpython/pycore_frame.h`, `cpython/asyncio_tasks.py` |

Raw source URLs use the matching GitHub repositories:

- `https://github.com/openjdk/jdk`
- `https://github.com/golang/go`
- `https://github.com/python/cpython`

## Why this belongs with GC

Virtual threads, goroutines, generators, coroutines, and asyncio tasks all have
the same GC problem: a live program state may be suspended outside the current
C/native call stack. A production pcc collector cannot only scan
`pcc_gc_frame_enter` records for the currently running function. It must also
trace heap-owned scheduler state and every suspended frame/continuation.

For pcc, this means:

1. A compiled Python coroutine/generator frame must be a traceable heap object.
2. The frame must expose all pointer locals, temporaries that survive a yield,
   the current await/yield child, and exception/finally state to
   `pcc_gc_trace_referents`.
3. Scheduler queues must be roots: runnable, sleeping/timer, waiting IO, and
   pending wakeup queues.
4. Carrier/native thread stacks and active `pcc_gc_frame_enter` records remain
   roots for currently running code.
5. Moving or generational backends must update references inside suspended
   frames and scheduler queues, not just ordinary containers.

## Reference reading map

### OpenJDK virtual threads / continuations

Use these when designing pcc virtual-thread semantics or pinned regions:

- `openjdk/VirtualThread.java`
  - scheduler + continuation fields;
  - virtual-thread state machine;
  - mount/unmount transitions;
  - park/unpark/yield continuation paths.
- `openjdk/Continuation.java`
  - continuation parent/child relations;
  - mounted state;
  - yield/preempt/pin API boundaries.
- `openjdk/continuation.hpp`
  - VM-side continuation frame access;
  - mounted/unmounted queries;
  - native hooks that let the VM walk continuation frames.
- `openjdk/continuationFreezeThaw.cpp`
  - HotSpot freeze/thaw implementation for mounted/unmounted continuations;
  - the closest reference for pcc stack-chunk experiments.
- `openjdk/StackChunk.java`
  - heap-owned saved-stack representation exposed to the Java runtime.
- `openjdk/Poller.java`, `openjdk/NioSocketImpl.java`, `openjdk/SocketChannelImpl.java`
  - blocking I/O integration that parks virtual threads instead of blocking
    carrier threads where possible.
- `openjdk/Blocker.java`, `openjdk/CarrierThread.java`, `openjdk/LockSupport.java`
  - carrier compensation, park/unpark, and pin/blocking boundaries.

pcc takeaway: do not model a virtual thread as only a pthread wrapper. The
continuation object itself is a root container and may be mounted or unmounted.
The viable pcc direction is Loom-shaped: first build safepoints and precise
root maps, then a `PyContinuation`/stack-chunk object, then scheduler/I/O
integration with explicit pinning diagnostics.

### Go goroutines / scheduler / stacks

Use these when designing M:N scheduling, GC assists, or stack ownership:

- `go/runtime2.go`
  - `g`, `m`, and `p` structs;
  - goroutine states;
  - `_Gscan*` states that give the GC stack ownership;
  - per-goroutine `gcAssistBytes`.
- `go/proc.go`
  - scheduler loops;
  - runnable queues;
  - parking and readying goroutines;
  - GC worker interaction points.
- `go/stack.go`
  - split/growing stack mechanics;
  - stack preemption guard values;
  - stack copy/shrink constraints.

pcc takeaway: a Go-style stackful design requires stack maps, stack ownership,
and stack movement rules. That is much more invasive than pcc's first
production coroutine target, which should use heap state-machine frames.

### CPython generators / coroutines / asyncio tasks

Use these when preserving Python-visible semantics:

- `cpython/pycore_frame.h`
  - frame state constants, including suspended and executing states.
- `cpython/genobject.c`
  - generator/coroutine object behavior;
  - `send`, `throw`, close/finalization, and suspended frame ownership.
- `cpython/asyncio_tasks.py`
  - Task state machine;
  - `_fut_waiter` await chain;
  - scheduled `__step`, wakeup, cancellation, and gather/shield relations.

pcc takeaway: the first production target should be Python-compatible
stackless coroutines/tasks: explicit heap frames plus scheduler queues. This is
enough to make GC precise before considering stackful virtual threads.

## pcc implementation gate

Before any of backend #1, #2, #3, or #4 can be called production in the
presence of user-mode concurrency, it needs tests proving:

- suspended coroutine locals survive `pcc_gc_collect(0)`;
- a cycle reachable only from a suspended task is retained;
- a cycle unreachable after task completion is collected;
- backend #3 promotes/updates references in suspended frames;
- backend #4 read/relocation barriers follow and update references in suspended
  frames and scheduler queues;
- backend #2 worker/assist paths treat scheduler queues as roots under
  `PCC_WITH_THREADS=1`.

Until those gates exist, pcc's coroutine support should be documented as
single-step/no-suspension or experimental, not Go/Java-level user-mode
threading.
