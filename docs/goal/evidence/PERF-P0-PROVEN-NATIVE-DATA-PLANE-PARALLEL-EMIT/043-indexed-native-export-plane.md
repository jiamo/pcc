# 043 — indexed native-export plane cuts Stage2 CPU; wall remains memory-bound

Date: 2026-09-05

## Closed representation

The final codegen export wire is now one indexed file, not 224 JSON shards.
Each native worker materializes only its transitive AST import/metadata
closure.  The export workers publish import edges from their already-owned
lifted ASTs; the coordinator does not reparse 224 source files.  Relative
imports and the ON-mode `pcc.llvm_capi.compat -> pcc.llvm_capi.ir` provider
mapping are preserved.

The repeated global unique-class scan is frozen once into 351 deduplicated
type descriptors, a 607-key common map, and sparse per-root deltas.  Across the
real 224-module compiler, all 224 reconstructed maps equal the old per-worker
algorithm; only 57 roots differ from the common map, totalling 420 removals and
143 replacements.  The preload index is about 310KB rather than 135,655
retained key rows.

Ninety-five contextual L1CodeGen mixin modules receive a separate 924KB host
schema containing only merged `L1CodeGen`, `ClassLowering`, and `ClassInfo`
exports.  It is decoded lazily only for those roots and does not pull the 132
host implementation modules into their ordinary dependency closure.

The writer/reader uses line payloads rather than numeric spans into one giant
Unicode string.  A rejected intermediate implementation spent 99.9% of a
10-second profile in UTF-8 codepoint-to-byte offset conversion and made a tiny
worker take 17.4s; one `splitlines()` removed that accidental quadratic path.

## Failure-class closure

The first full Stage2 attempt correctly stopped at two omitted-context
failures (`method_call_expression_lowering` and `pipeline`).  The emitted P
table was then found to contain zero edges for all 224 modules even though raw
export-worker rows were correct.  Every string-set introduced by the indexed
writer/reader/preload path was replaced with the repository's established
list/dict membership projection; integer-ID seen sets remain.

The two-module pcc1 canary now preserves
`pkg.entry_relative -> pkg.dep` from raw worker row through final wire.  On the
real compiler the final P table contains 963 edges across 186 non-empty
modules.  The two formerly failing workers pass.  For the method-call module,
41/44 ordinary global/function assembly blocks—including `_emit_method_call`
itself—match the full-export control byte for byte; only class metadata and
module init/fini differ because unrelated extern classes are no longer
initialized/released.  `pipeline` assembly is wholly exact.

Focused results:

```text
frontend/export/type/context/worker packet        137 passed
ownership-transfer sibling packet                   4 passed
cross-module semantic nodes                         14 passed
relevant strict closures                            PASS, real bodies
224-root unique-class reconstruction                 exact
```

## Representative same-compiler A/B

```text
worker                         full wire                 indexed wire
tiny pcc.py_frontend           1.58s / 632MB / 13.557B   0.20s / 128MB / 2.107B
py_ast                         27.72s / 3.145GB /368.15B 25.46s / 2.413GB /356.31B
method-call contextual         22.91s / 2.878GB /279.33B 20.76s / 2.447GB /266.35B
```

The tiny and `py_ast` PCOs are byte-identical between full/indexed arms.
Method-call's ordinary function bodies are exact as classified above.

## Source-current Stage1 and Stage2

Source-frozen v41:

```text
source manifest       385bf0bad92ead5121640e32657efe6c3f54ccb0c3f33cc10eeda9aaaf5148ac
pcc1 SHA-256          d27e0e3ceb8109c461b67167bba57f794f866dd05b7a6df277d94a9b47523359
Stage1 wall / CPU     179.15s / 717.42s
Stage1 tree peak      4,698,734,592 B
```

The guarded GC0 Stage2 completed and published a runnable, libSystem-only
pcc2:

```text
receipt               build/indexed-export-stage2-v41/stage2-record.json
end-to-end wall       960.951s
timed-tree CPU        2046.211s (1826.099 user + 220.112 sys)
peak tree RSS         7,637,516,288 B
pcc2 SHA-256          71e8b862c81de01c9c0d7cabab334369b7f9b18e9cb92469357e9940591b0035
linkage               libSystem only; no libpython or LLVM
```

Against the prior v28 transfer (`1005.626s / 2301.856s CPU`), wall improves
4.4% while tree CPU improves **11.1%**.  `2046.211 / 12 = 170.5s`, now below
the v41 Stage1 wall, so remaining wall is primarily insufficient safe
parallelism, not a fivefold compute deficit.

The 193-worker small lane now peaks at 660MB median, 1.57GB p90 and 3.19GB
max, but the old AST floor generated 1,053 admission denials.  A static linear
floor fit was rejected: covering every outlier with 5% + 0.1GiB margin still
predicted a 388s small lane, versus about 208s using the measured exact peaks.
Do not encode a module-name whitelist or an unsafe average model.

## Verdict and next boundary

`[CONFIRMED]` for the indexed export/type plane, bounded memory, material tree
CPU/RSS reduction and source-current Stage2 correctness.  This is not task
completion: Stage2/Stage1 wall remains 5.36x, the global fixed point has not
run, and GC1-4 remain pending.

The next architectural owner is process lifetime.  Frontend objects are
released before emit but their allocator slabs remain resident in the same
worker.  Freeze a packed `ParsedModule`/`IndexedFunctionSeed` sidecar, exit the
frontend process, then let a fresh emit worker consume it.  This must preserve
the direct indexed zero-fallback contract and exact emitted object semantics;
only after measuring its new per-item peaks should lane widths be increased.
