# Stage2 medium-lane critical-item ranking

Date: 2026-08-26  
Task: `PERF-P0-STAGE2-COLD-CACHE-REGRESSION`  
Claim level: diagnostic current-source GC0/self/no-libpython native emit.  This
is not another full Stage2 timing or a new source optimization.

## Exact input reconstruction

The accepted No.62 frontend cache entry was verified against bundle SHA-256
`b62d4ce9606aa8492cd9588b21ead8a995e2fd5b67804010ff868eb7a2e2eaae`.
Its length-prefixed 212 modules were split with the production
`split_self_backend_ir_module_for_object_shards` function and the production
2,000,000-byte / 1,000,000-byte thresholds.

The reconstructed manifest contains exactly the counts from the cold Stage2
profile:

```text
modules       212
objects       464
oversized       7
medium        152
small         305
```

Manifest:
`build/stage2-current-object-inputs-no62-v1/manifest.json`.

## Per-item ranking tool

`scripts/pcc_emit_rank.py` runs every selected input in a fresh compiler
process, holds `build/.pcc-performance.lock`, applies a process-group timeout,
records Darwin wall/user/sys/instructions/cycles/RSS/footprint counters,
validates the native-worker result and assembly SHA-256, and persists the
manifest after every completion.  It fails rather than overwriting an output
directory.  Focused tool gate: `tests/python/test_pcc_emit_rank_tool.py`, one
passed in 0.29 s.

## Production-shaped medium replay

Inputs were the 152 exact current medium objects.  Compiler was the same
ordinary-bootstrap pcc1 used by the accepted cold Stage2:
`31d6ac3bc4f9b217ac9a15336c99e7f0e1f6f1e7d54e7159ed14becad13a0393`.
Each item used one fresh native worker and the replay used eight concurrent
workers, matching the medium-lane cap.  All 152 returned zero with assembly
receipts; elapsed wall was 254.28 s.

The ten slowest completions were:

| rank | module | bytes | wall |
|---:|---|---:|---:|
| 1 | `pcc.py_frontend.codegen.string_method_lowering` | 1,973,250 | 24.25 s |
| 2 | `pcc.backend.self_backend_parse` | 1,798,704 | 22.66 s |
| 3 | `pcc.backend.self_backend_verify` | 1,881,875 | 22.42 s |
| 4 | `pcc.py_frontend.codegen.assignment_statement_lowering` | 1,741,427 | 21.90 s |
| 5 | `pcc.py_frontend.codegen.cpy_call_lowering` | 1,949,350 | 21.71 s |
| 6 | `pcc.backend.precise_stackmap` | 1,850,409 | 21.49 s |
| 7 | `pcc.py_frontend.codegen.comprehension_lowering` | 1,865,765 | 21.16 s |
| 8 | `pcc.py_frontend.codegen.exception_lowering` | 1,892,625 | 20.67 s |
| 9 | `pcc.py_frontend.codegen.format_lowering` | 1,866,307 | 20.43 s |
| 10 | `pcc.py_frontend.codegen.name_lowering` | 1,871,655 | 20.35 s |

Full manifest:
`build/stage2-medium-rank-no62-v1/manifest.json`.

The result corrects the earlier proxy error.  IR byte size alone does not rank
cost: before the full replay, the 1.80 MB parser object and 1.88 MB verifier
object both exceeded the isolated largest-file time.  Under eight-way load the
largest object did become the critical completion, but at 24.25 s versus about
15.5 s in isolated A/Bs, showing that its production cost includes resource
competition as well as its local algorithm.

## Current critical-item profile

The No.62 ordinary pcc1 call graph on the exact rank-1 item attributes the
post-prepare samples as follows:

```text
function emit                42.42%
precise stack-map plans      33.26%
stack-map render              8.42%
adjacent-memory target pass   5.08%
global emit                   5.00%
regalloc                      4.77%
```

The largest self leaf is `pcc_gc_granule_is_object_start` at 18.89%, but its
callers span instruction emit, stack-map analysis, target passes, hashing and
other compiler operations.  No.62 removed the one exact repeated analysis:
regalloc fell from 8.78% in the No.60 capture to 4.77% here.

The remaining local shapes have recorded negative evidence in
`pcc1-stage2-emit-throughput-and-memory.md`: precise-stackmap cursor/views,
managed-index-only, safepoint representation, parse regex/interning, structured
sidecar production, text lifecycle, call rendering, and worker concurrency.
None identifies a new whole local lifecycle above the existing acceptance
floor.  Retrying one would be optimization without evidence.

## Routing conclusion

The safe/medium lane remains expensive, but the remaining common tax is the
distributed runtime provenance path rather than another untried emit-local
scan.  Its existing structural owner is
`ARCH-P1-GRANULE-SPAN-LOOKUP-RADIX`, which replaces the remaining hash probe
with a shift-and-load lookup while preserving publication, transactional slab
registration, moving-GC retirement and five-backend provenance semantics.

`PERF-P0-STAGE2-COLD-CACHE-REGRESSION` should wait on that row before paying
for another cold Stage2.  This does not claim that the radix row will by itself
reach 600 s; it is the next evidence-backed prerequisite, while emit-local
micro-edits are exhausted at the current floor.
