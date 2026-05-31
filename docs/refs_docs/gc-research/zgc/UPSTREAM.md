# OpenJDK ZGC upstream snapshot

This directory is a pinned reference pack for pcc GC backend #4.

## Upstream

| Field | Value |
|---|---|
| Repository | `https://github.com/openjdk/jdk` |
| Upstream directory | `src/hotspot/share/gc/z` |
| Tag | `jdk-27+21` |
| Commit | `4e1dd4daba5a619bbbc9720fdd509e609d6f0032` |
| Fetched | 2026-05-14 |

`MANIFEST.json` is the authoritative file list. It records the upstream path,
raw URL, SHA-256, and byte count for every copied source file.

## Freshness check

Checked against OpenJDK `jdk` `master` on 2026-05-14. At that point
`master` was 83 commits ahead of `jdk-27+21`
(`bcbf5cf730ee9a51c52310a6dfc36cd22111fe87`), but the compare contained no
changes under `src/hotspot/share/gc/z/`. For backend #4 purposes, the pinned
`jdk-27+21` reference pack is therefore still current for ZGC source files.

## Why this is the reference for backend #4

Modern OpenJDK ZGC is generational:

| JDK | Change |
|---|---|
| JDK 21 | JEP 439 introduced Generational ZGC. |
| JDK 23 | JEP 474 made generational mode the default. |
| JDK 24 | JEP 490 removed the non-generational mode. |

That means pcc backend #4 should no longer use old single-generation ZGC as
the architectural target. The target is a GenZGC-style colored relocating
collector: load barriers, forwarding/relocation, page evacuation,
remembered sets, store-buffer mechanics, young/old policy, and concurrent
worker orchestration.

## Scope

This is not a full OpenJDK mirror. It intentionally copies the files backend
#4 authors need to reread while porting:

| Area | Representative files |
|---|---|
| Heap / cycle orchestration | `zHeap.*`, `zCollectedHeap.*`, `zDirector.*`, `zDriver.*` |
| Generational policy | `zGeneration.*`, `zGenerationId.hpp`, `zHeuristics.*` |
| Barriers | `zBarrier.*`, `zBarrierSet.*`, `zBarrierSetRuntime.*` |
| Store / remembered sets | `zStoreBarrierBuffer.*`, `zRemembered*` |
| Forwarding / relocation | `zForwarding*`, `zRelocate.*`, `zRelocationSet*` |
| Page allocation | `zPage*`, `zPageAllocator.*`, `zPageTable.*` |
| Marking / roots | `zMark*`, `zRootsIterator.*`, `zWeakRootsProcessor.*`, `zReferenceProcessor.*` |
| Workers / verification | `zWorkers.*`, `zRuntimeWorkers.*`, `zVerify.*`, `zStat.*` |

## pcc status boundary

The source files here describe the upstream design. They do not imply pcc
backend #4 has implemented all of GenZGC.

Current pcc #4 status remains narrower: object-level forwarding/read-barrier
and relocation-focused gates exist, but page evacuation, fragmentation
policy, full GenZGC young/old mechanics, and complete reference-updating
coverage are still open production-hardening work.
