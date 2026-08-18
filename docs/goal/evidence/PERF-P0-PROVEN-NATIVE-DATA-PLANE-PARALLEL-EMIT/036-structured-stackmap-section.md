# 036 — packed stack maps bypass the text assembler

Date: 2026-09-04

## Owner sizing

The retained v19 `py_ast` assembly replay is 13,846,948 bytes / 740,032
lines. It contains about 474,000 numeric directive lines (`.long`, `.short`,
`.byte`, `.quad`), about 70% of all non-label lines. Its ASM-only worker is
12.24s / 150.426B instructions / 1.655GB RSS, while its production PCO worker
is 38.66s / 561.423B instructions / 5.118GB RSS. Assembly plus native-object
publication therefore owns about 26.42s, 410.997B instructions and 3.46GB on
this representative worker.

The encoded `__DATA,__pcc_stackmaps` payload is 1,510,328 bytes and carries
532 function-address relocations. Its stable fixed-width ABI makes it the
first complete high-volume directive family that can bypass decimal text
without changing instruction encoding.

## Change

`StructuredAArch64Module` is a one-per-module transport shell. Families not
yet migrated remain exact line chunks; migrated families carry final
`Section` payloads and relocations. `assemble_lines` merges those sections in
stable Mach-O segment order, rejects duplicate/untyped sections, and resolves
their defined/undefined symbols with the text-owned sections.

The AArch64 precise-stackmap producer now packs the authoritative v2 header,
function, record and location codecs directly into immutable byte chunks and
publishes one `Section`. Final safepoint order is computed in a
`CompilerIntArena` heapsort; it does not reconstruct the former per-record
Python tuple/dataclass projection. The existing textual renderer remains the
compatibility oracle. The production direct PCO worker consumes the
structured transport; ASM and validation/text-control modes remain unchanged.

## Correctness and closure gates

```text
focused assembler/codec/stackmap/direct/worker/inventory packet
103 passed, 12 environment-gated deselections, 38.48s

self_backend_precise_stackmaps.py strict standalone closure  PASS
arm64_asm_driver.py strict standalone closure                 PASS
pipeline_frontend_worker_execution.py strict closure          PASS
```

The emitter's standalone-file closure reports the pre-existing iterable-splat
diagnostic on both current source and `git archive HEAD` source. The changed
emitter is proven in context by the source-frozen pcc1 build and the real
compiled worker below; this receipt does not relabel that unrelated standalone
closure gap as fixed.

The direct-indexed regression constructs fresh text and structured routes,
then requires equal `Section` objects, undefined symbols, relocations and
encoded PCO bytes. The full real-worker object comparison below is the
representative downstream boundary.

## Source-frozen transfer

Source identity: `30bdbe305ed0624cfe57a8115ac3ee2fc0ef9bf57b598e9357cb91805f5a1cc0`.

```text
pcc1 SHA-256             9b00720bff1d915ef1f3af262d30bb660e6810d3832a3d5378a9e04a7623c316
Stage1 wall / tree CPU   158.69s / 669.66s
process-tree peak        4,781,785,088 bytes (8 GiB hard cap)
linkage / canary         libSystem only / function canary green
```

This one transfer build is not a paired Stage1 speed verdict.

## Current-pcc1 representative worker

Both arms use the same frozen v14 `worker_156.manifest`, AST sidecar, full
native-export wire and GC0/no-libpython/direct-indexed environment.

```text
metric                 v19 line control       v20 structured stackmap   reduction
wall                   38.66s                 35.94s                    7.04%
user+sys CPU           38.48s                 35.88s                    6.76%
instructions           561.422943B            548.672742B               2.27%
max RSS                5,118,443,520 B        4,085,825,536 B          20.17%
peak footprint         5,074,292,768 B        4,040,936,760 B          20.36%
process-tree peak      n/a                    4,092,821,504 B           under cap
```

Both publish the exact PCO SHA-256
`9987edea18e5fde14fd279cfdbfdfefc429eb9bddbae99122102702f4f13d3c0`.

## Verdict

`[CONFIRMED]` for the structured stackmap family: it removes a named hot text
projection, preserves the complete object boundary, improves representative
pcc1 worker CPU/wall, and materially lowers memory. It is not completion of
the structured assembler or Stage2<=Stage1 claim. The PCO worker still spends
about 23.7 seconds beyond the v19 ASM-only boundary; current-v20 caller
attribution is required before selecting the next structured family. No
Stage2 ran for this slice.
