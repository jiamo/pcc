# OpenJDK Loom / virtual-thread upstream snapshot

This directory is a pinned reference pack for pcc user-mode scheduling,
virtual-thread, continuation, and suspended-stack GC work.

## Upstream

| Field | Value |
|---|---|
| Repository | `https://github.com/openjdk/jdk` |
| Branch | `master` / mainline HEAD |
| Commit | `b9778ccb475891efd6347f7645b9a53c011f70fd` |
| Fetched | 2026-05-15 |

`MANIFEST.json` is the authoritative file list. It records the upstream path,
raw URL, SHA-256, and byte count for every copied source file.

## Why this is the reference

OpenJDK Project Loom is the closest production reference for stackful virtual
threads built on continuations, carrier threads, pinning, blocking integration,
and GC-visible stack chunks. pcc should copy the engineering shape, not the
JVM assumptions: pcc still needs its own safepoints, stack maps, continuation
objects, suspended-frame tracing, and backend #4 relocation updates.

## Scope

This is not a full OpenJDK mirror. It intentionally copies the files needed to
read the design while porting:

| Area | Representative files |
|---|---|
| Java virtual-thread state machine | `VirtualThread.java`, `BaseVirtualThread.java`, `ThreadBuilders.java`, `Thread.java` |
| Continuation API and stack chunks | `Continuation.java`, `ContinuationScope.java`, `ContinuationSupport.java`, `StackChunk.java` |
| Carrier/blocking/thread container integration | `CarrierThread.java`, `CarrierThreadLocal.java`, `Blocker.java`, `ThreadContainer.java`, `ThreadFlock.java`, `LockSupport.java` |
| I/O park/unpark integration | `Poller.java`, `PollerProvider.java`, `NioSocketImpl.java`, `SocketChannelImpl.java` |
| HotSpot freeze/thaw machinery | `continuation*.cpp`, `continuation*.hpp`, `continuation*.inline.hpp` |

## pcc status boundary

These files do not mean pcc already supports Loom-style virtual threads. They
are the upstream reference for No.42: pcc must still build the prerequisites
before production virtual threads are credible:

- codegen safepoints with bounded response time;
- PC-indexed stack maps or an equivalent precise suspended-frame root map;
- a traceable `PyContinuation` / stack-chunk object model;
- scheduler queues as GC roots;
- backend #4 relocation rewrite for suspended frames and scheduler queues;
- pinning diagnostics for foreign C/libpython/native-thread regions;
- self-bootstrap gates proving no new `py_cpy_*` fallback.
