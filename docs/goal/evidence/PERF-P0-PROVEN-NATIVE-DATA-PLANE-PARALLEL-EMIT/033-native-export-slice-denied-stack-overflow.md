# 033 — per-worker native-export slicing denied at the real pcc1 boundary

Date: 2026-09-04

## Measured owner

The current source-frozen v14 pcc1 profiled the real Stage2 small-lane
`pcc.py_frontend.py_ast` worker (`worker_156`) through frontend codegen,
direct indexed emit, assembly and `.pco` publication:

```text
wall / CPU                 41.87s / 39.36s
instructions               561.548B
peak process-tree RSS      5,272,485,888 B
output                     PCO, SHA 9987edea18e5...f13d3c0
caller samples             12,400
frontend generate          39.25%
native AArch64 emit        38.95%
GC0 mark / subtract        about 20%
_native_export_from_wire   23.05%
granule provenance         10.52%
```

Unlike the 2026-06 profile with a small persistent worker pool, the current
Stage2 uses 224 short-lived one-module workers. Each reads and materializes the
complete 12,967,451-byte native-export wire. A dependency-closure sizing pass
put the conservative median module slice at 1.82 MB, `py_ast` at 1.76 MB, and
all 224 slices at about 0.50 GB versus about 2.9 GB of repeated full-wire
input. The owner and its roughly 1.30x single-worker Amdahl ceiling are real.

## Proposal

Keep the existing v1 tuple/default/type projection, but write each module's
v1 export shard once and give every singleton worker an index containing its
transitive import closure, metadata module references, and unique derived
class targets. Unrelated modules must be absent. Full v1 remains the fallback;
tampered/missing/duplicate shards fail closed.

This deliberately did not repeat the historical `[DENIED]` list-preserving
decoder, which changed pcc2/pcc3 semantics, or the slower AST JSON sidecar
experiment.

## Focused evidence

The dependency/derived/reference closure, exact projection, tamper rejection,
deferred persistence and real shared-context tests passed. The full affected
packet reached 117 passing tests, and the complete multi-source strict closure
gave real `entry` implementations for writer, reader, shared-export wrapper and
compile caller.

A two-module pcc1 compile-to-binary canary exercised the feature in 9.80s at
197,885,952 bytes tree peak; it linked only libSystem and printed `42`.

Those results are necessary but not sufficient: the real 224-module checkpoint
is the acceptance boundary.

## Real-boundary failure

Two source-frozen pcc1 generations reached the same failure after successfully
writing all 224 module shards and all 224 indexes:

```text
version  implementation                    checkpoint   tree peak       result
v15      reread full JSON then shard       80.982s      8,199,045,120   SIGSEGV
v16      shard from live export objects    82.788s      8,331,952,128   SIGSEGV
```

Both Darwin crash reports classify the fault as `Thread stack size exceeded`:

- `pcc1-2026-09-04-173423.ips`, pid 25879;
- `pcc1-2026-09-04-175114.ips`, pid 70431.

The v15 stack was symbolized against its own pcc1 and reaches the normal
manifest/callback chain while `shell_quote_arg` allocates. The v16 design
removed the suspected second full JSON object graph entirely, but reproduced
the same stack-overflow class. Thus the whole 224-module indexed-shard boundary
is not safe under the current pcc1 call/object model; the two-module success
cannot override it.

The external 16 GiB guards contained both failures, and no compiler/test child
survived. No Stage2 was launched.

## Harness exclusions

Two diagnostics are retained but excluded from claims:

- one control accidentally used numeric `PCC_PY_FRONTEND_JOBS=3`, disabling
  the deferred auto lane and entering full codegen; the guard stopped it;
- `class_gen` was forced through in-process PCO publication even though the
  production scheduler classifies it as paired-oversized ASM. Both old and new
  pcc1 exceed 8 GiB on that non-production shape; the valid production
  assembly-only replay is 33.40s / 4.16GB.

## Verdict

`[DENIED]`. All production slice code and candidate-only tests were removed by
narrow forward patches. The restored frontend/export/worker packet passes
116 tests. The generic replay tool's checked `--exports-path` override remains
because it changes only an isolated copied manifest and is useful for future
transport A/Bs; its tool test passes.

Do not retry per-worker export JSON shards/indexes on this pcc1 coordinator by
changing only where the full wire is decoded. A future export-plane proposal
needs a different execution representation (for example a compiler-native
indexed/lazy codec with no 224-object publication/callback graph) and must
first have a full-closure pcc1 canary, not merely a two-module proof.
