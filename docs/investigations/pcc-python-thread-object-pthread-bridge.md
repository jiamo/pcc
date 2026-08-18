# Investigation: connect pcc-Python Thread objects to the owned pthread kernel

## Status

active

## Problem Description

The production pcc-Python runtime has a complete opt-in pthread kernel, but
`py_threading.py` always executes `Thread.start()` synchronously. This prevents
the self-host compiler from using deterministic in-process parallel work even
when linked with an explicit `PCC_WITH_THREADS=1` pcc-Python archive.

The C runtime is the differential oracle only. The final implementation must
remain authored in pcc-Python and link no `pcc_threads.o`, libpython or LLVM.

Predecessors:

- `pcc1-threaded-explicit-gc-collect-gap.md` proves real pthread Thread/Lock
  semantics and GC0--4 with the transitional C runtime;
- `threading-lock-lost-update.md` and
  `threading-list-index-native-dispatch.md` own frontend receiver dispatch;
- `pcc1-stage2-emit-throughput-and-memory.md` No.91/No.93 owns the compiler
  performance consumer.

## Repro

The raw pcc-Python kernel already passes:

```bash
gtimeout 900s env -u LC_ALL uv run pytest \
  'tests/python/test_freestanding_runtime_no_c_closure.py::test_explicit_pcc_python_thread_kernel_starts_and_joins' \
  -vv -x -n0 --tb=short
```

The missing high-level gate will compile against
`cached_threaded_pcc_python_runtime()` and start worker 0 waiting on an Event
that only worker 1 can set. The current synchronous shim cannot return from
worker 0's `start`; the pthread bridge must complete and print the exact
result.

## Test [CONFIRMED]

The raw kernel is confirmed green. The high-level fail-first gate compiled
against the threaded pcc-Python archive and timed out after 10 seconds in the
first synchronous `Thread.start()`. After the bridge it completes a two-thread
Lock rendezvous, lets both workers call `gc.collect()`, releases a named local
Thread reference immediately after start, and prints `2 2 1`. The explicit
default pcc-Python archive remains synchronous and prints `0` then `1` around
one `start()`.

## Proposals

- No.1 Port the C Thread-object handoff/start/join/detach contract [pending]

## No.1 Port the C Thread-object handoff/start/join/detach contract

### Code Change

Use `pcc_threads_enabled`, `pcc_thread_start/join/detach`, `function_addr` and
the existing object layout. Preserve the synchronous default path. A pthread
start owns one extra Thread reference until the C-ABI callback stores its
result and releases the handoff; join clears the raw handle; deallocation
detaches an unjoined handle. Do not introduce another thread registry or GC
root graph.

### CONFIRMED at the runtime boundary; compiler performance transfer pending

The pcc-Python mirror now follows the C oracle's conditional start-handoff,
C-ABI callback, raw handle, join, is-alive and detach lifecycle. The final
source-bound focused gate proves archive ownership, raw start/join/STW and the
high-level bridge in one run: 3 passed in 113.54s. Additional focused evidence:
threaded/default high-level behavior 2/2, exception TLS 2/2, strict
`py_threading.py` and stdlib threading closure, and GC0 dual-worker explicit
collection.

The temporary-expression receiver `Thread(...).start()` still misses native
frontend dispatch and is deliberately not folded into this runtime fix; a
named local whose scope ends immediately proves the handoff contract. Likewise,
the older pcc-Python Condition/Semaphore combined entry probe is baseline-red
under the accepted No.89 archive and is not attributed here.

The runtime bridge is correct at its bounded layer. Acceptance as a compiler
optimization still depends on No.93's threaded pcc1 worker performance and
exact-output gates.

## Update — compiler transfer denied; bridge source removed

The runtime bridge passed all focused gates, but its intended compiler consumer
did not. A correctly receipt-bound `PCC_WITH_THREADS=1` pcc1 makes item311
62.5% slower with block parallelism off because atomic/safepoint overhead
dominates. Auto block emission then fails on a separate pcc1 `all(list)` gap
after exceeding 30 seconds. The performance proposal was rejected before
formal pairs.

Because No.93 pre-registered the bridge and consumer as one landing unit, the
pcc-Python Thread-object source was restored byte-for-byte to accepted No.89.
This investigation stays active: the implementation and green runtime
artifacts are valid for a future capability-owned task, but the missing bridge
is not falsely marked resolved in current production.
