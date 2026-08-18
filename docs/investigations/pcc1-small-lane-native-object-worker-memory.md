# Investigation: the Stage2 small lane's in-process native-object path is quadratic under pcc1

## Status

active — fixes 1-3 landed and replay-verified; the remaining 3.27 GiB / 19.5 s
against host's 0.21 GiB / 3.7 s is attributed to codegen ownership leaks
(see `pcc-codegen-ownership-leaks-str-iadd-and-call-result.md`) and to
retained frontend inputs.

## Problem Description

The authorized capped Stage2 v3 (`build/inline-edge-stage2-capped-v3`) hit the
8 GiB breaker at 628 s in the small lane: worker_27
(`pcc.py_frontend.codegen.exception_lowering`, AST 1.92 MB) grew
1.29 -> 6.82 GiB in six seconds.  The four other lanes had completed under
6.3 GiB tree with zero suspensions, so the burst-admission defect
(PERF-P0-STAGE-RESOURCE-ENVELOPE-PARITY evidence 002) is fixed and this is a
different owner.  The small lane is the only lane with
`PCC_DIRECT_INDEXED_NATIVE_OBJECT=1`: the worker assembles its own `.s`
(`arm64_asm_driver.assemble_file`) and encodes a `.pco`
(`NativeObject.from_sections`, `encode_native_object`) in-process.

## Repro

Replay the recorded worker with the exact Stage2 environment (a launcher that
`execve`s pcc1 with the receipt's environment; `env KEY=VAL` word-splitting
fails on this machine's PATH):

```text
manifest  build/inline-edge-stage2-capped-v3/stage2/pcc2.pcc-codegen-plan.state.89262/manifests/worker_27.manifest
          (lines 1-2 rewritten to a scratch result/pco dir)
env       build/inline-edge-stage2-capped-v3/stage2-process.result.json["environment"]
          + PCC_PY_FRONTEND_JOBS=1 PCC_DIRECT_INDEXED_NATIVE_OBJECT={0,1}
guard     scripts/run_process_tree_sample.py --max-tree-rss-bytes 17179869184 --timeout 300
```

## Test [CONFIRMED]

```text
pcc1 v4  NATIVE_OBJECT=0   10.7s   1.29 GiB   (.s)
pcc1 v4  NATIVE_OBJECT=1   77.2s  13.64 GiB   (.pco)   <- the small-lane shape
host     NATIVE_OBJECT=1    3.7s   0.21 GiB
```

pcc1's `.pco` equals host `assemble_file(pcc1 .s)` byte for byte, so the
assembler is correct; the 20x/65x gap is pcc1 execution.

## Proposals

- No.1 chunks + one join instead of bytearray growth `[CONFIRMED]`
- No.2 `bytes.join` runtime primitive `[CONFIRMED]`
- No.3 struct.unpack_from zero-copy for bytes `[CONFIRMED]`
- No.4 release codegen ownership leaks (str +=, len(call)) `[pending, own file]`

## No.1 chunks + one join instead of bytearray growth

### Code Change

`pcc_profile.py` during the blow-up: `_py_bytes_concat` 66.8%, `_memset` 21.1%.
pcc's bytearray `+=`/`extend`/`append` allocate a replacement buffer
(PY-P0-BYTEARRAY-INPLACE-IDENTITY-MUTATION), so per-instruction and
per-relocation appends were O(n^2).  `arm64_encode.assemble_text`,
`arm64_asm_driver._SectionBuffer` and `native_object.encode_native_object` now
collect `list[bytes]` chunks and join once.

### CONFIRMED

Host: 31 assembler/object tests green; module 151 `.pco` byte-identical.
pcc1 (Stage1 v6 rebuilt): 77.2 s / 13.64 GiB -> 28.6 s / 3.28 GiB, output
byte-identical.

## No.2 `bytes.join` runtime primitive

### Code Change

Stage1 v5's canary failed: `codegen[stage1_function_smoke]: AttributeError:
join` — the runtime had no `bytes.join` (pcc/py_stdlib zlib/lzma/bz2/hashlib
already used the idiom, so those paths were latent failures under pcc1).
Added `py_bytes_join` (port + C mirror, header, ABI table in a rebalanced
chunk) and a typed frontend branch for bytes/bytearray receivers; DynType
`.join` stays on the str path (same-name overlap).

### CONFIRMED

`tests/python/test_native_bytes_join.py` port + cc + GC0..4 equal CPython;
Stage1 v6 canary prints 42.

## No.3 struct.unpack_from zero-copy for bytes

### Code Change

After No.1, `_py_bytes_new` was 34.9% self time under
`struct.Struct.unpack_from` (46% inclusive) from the stack-map validator,
which runs three times per object (from_sections, `__post_init__` round trip,
encode).  `pcc/py_stdlib/struct.py::_unpack_fields` copied the whole buffer
each call; an immutable bytes buffer is now read in place.

### CONFIRMED

82 struct tests green; pcc1 (Stage1 v7): 28.6 s -> 19.5 s, peak unchanged at
3.27 GiB, output byte-identical.  The peak is therefore not transient
copies: MallocStackLogging live-heap attribution at the plateau is
`assemble_file` 40%, `emit_aarch64_darwin_indexed_module` 17%,
`_read_native_exports_wire` 17%, frontend codegen 9%.

## No.4 release codegen ownership leaks `[pending]`

Bisected with single-construct programs (see the sibling investigation):
`cur += ch` leaks every previous string (299 MB for 20k chars; `cur = cur +
ch` is 3 MB), and `len(f())` with an exact-list result leaks the owned result
(116 MB per 300k calls; `x = f(); len(x)` is 3 MB).  The assembler's
`_split_operands` does `cur += ch` per character of every instruction line.

## Update 2026-09-03 — capped Stage2 v4 completes under the cap; small-lane outliers named

With No.1-3 plus the ownership fixes (source v18), the authorized capped
Stage2 v4 COMPLETED: 1350 s, peak tree 7.28 GiB < 8 GiB, runnable
libSystem-only pcc2 whose function canary prints 42
(PERF-P0-STAGE-RESOURCE-ENVELOPE-PARITY evidence 004).  The small lane ran
all 193 modules at width <= 4 with floor admission (1712 denied polls, 3
suspensions), median worker peak 1.20 GiB, p90 2.79 GiB.

The native-object path still has outliers the AST-size floor cannot see:
`pcc.py_frontend.py_ast` (AST 0.27 MB) peaked at 4.88 GiB in 46 s and
`pcc.backend.arm64_encode` (1.99 MB) at 4.70 GiB; `ir_scaffold_lowering`
4.18 GiB.  `py_ast` is 66 dataclasses whose generated methods make its
emitted `.s`/object far larger than its AST, so these are the next replay
targets (same recipe as module 151).  The receipt tool bug that lost the
formal stage2-record (`frontend_jobs` missing from the runner Namespace) is
fixed with a test.

## Update 2026-09-04 — where pcc1's time goes (v8 worker + coordinator), two experiments denied

Human decision: 1350 s is not acceptable; the contract is Stage2 <= Stage1 on
the same resources (Stage1 v8 = 164 s), a modestly larger shared memory
envelope is acceptable.  Attribution to decide where a 5x per-operation gain
can come from:

Self time by code category (`scripts/pcc_flamegraph.py cpu`, v8 pcc1):

```text
                          worker module_151 (whole life)   coordinator single-thread phases
gc/allocator exports              56.3%                          38.1%
runtime ports (_user_py_*)        21.3%                          19.7%
runtime C-ABI (_py_*)             12.5%                           9.0%
compiler code (_user_pcc_*)        3.5%                           1.4%
memset/memmove                     1.6%                           3.2%
libSystem                          1.2%                          24.7%  (mmap 16.9, read 5.0)
```

pcc1's own compiler logic is 1-4% of the samples; ~98% is the runtime it
calls per operation.  Per-phase host-vs-pcc1 wall (Stage1 v8 vs Stage2 v4
coordinator): collect_multi_source_relative_closure 1.0 s vs 11.0 s,
expand_native_extension_module_object_ports 0.4 vs 7.1, expand_recursive_stdlib
3.2 vs 16.8, order_module_inits 0.5 vs 3.3, export_parallel 9.5 (10 workers) vs
89.4 (2 workers).  The tiny worker retires 31 B instructions at IPC 3.2 for a
2653-record module: the cost is instruction count, not stalls.

Layer shares (inclusive):

```text
                         worker A7   coordinator
barrier/root layer         13.4%        9.1%    load_ptr root reloads 9.5/1.9, store_root, pin/unpin, frame roots
provenance checks           8.0%       12.3%    granule_is_object_start 3.2/7.3, pointer_is_managed, find_slot
refcount protocol           5.1%       10.7%    incref/decref prepare + decref_finish + py_decref
type checks                 2.1%        4.4%    py_capi_type_runtime.is_type_object
GC0 cycle collector         ~8% (v8 worker: mark_reachable 4.3 + visit_subtract 3.8)
```

### Experiment: GC0-static barrier layer `[DENIED as designed]`

A knob that turned every frontend-emitted `pcc_gc_load_ptr` into a plain
load (and skipped pin/unpin/write-barrier notifications) miscompiled even a
pure integer loop (`total += i` printed 0).  Cause: every frontend
`pcc_gc_load_ptr` call has `owner == null` -- they are ROOT-SLOT RELOADS
(1135 of 1135 in module 151), the protocol by which generated code re-reads
an alloca that runtime calls (`pcc_gc_store_root`, frame enter) write
through an escaped pointer; as a plain load the self backend's mem2reg
forwards the alloca's initial `store null` across those calls.  The runtime
already has the flag fast path (`pcc_gc_read_barrier_enabled` is 0 on
backends 0-2), so the layer's cost is the call itself.  A correct design is a
backend-visible "volatile/non-forwardable load" for root reloads (and a
sound escape rule in mem2reg); the knob was forward-removed.  Upper bound of
the layer: ~13% (worker) / ~9% (coordinator).

### Experiment: GC0 cycle-collector frequency `[DENIED]`

`PCC_GC_DEBT_THRESHOLD=4294967296` versus default on the same v8 pcc1:
module 151 252.76 B -> 252.58 B instructions, 20.24 -> 20.02 s; cli_bootstrap
892.25 B -> 892.22 B, 72.3 -> 71.9 s; outputs identical.  The collector work
in the profile is the explicit frontend-release `gc.collect()` that buys the
24 GB -> 7 GB memory drop, not debt-triggered collections.  Not a lever.

### Quantified candidates (each needs its own single-variable A/B)

1. Export lane width from MEASURED per-kind peaks: the two compiled export
   workers sum to ~0.8 GiB while admission budgets 3 GiB each; host runs 10.
   Coordinator export 89 s -> ~20-25 s (about -65 s of 1350).  Harness.
2. Allocator slab acquisition: `pcc_allocator_refill_small` does one
   `page_alloc(65536)` mmap per slab; 16.9% of coordinator samples are the
   mmap syscall.  Reserve larger arenas and carve 64 KiB slabs.  Runtime.
3. Provenance checks at statically-known-object sites (13.5% / 12.3%): the
   codegen already skips pin/release for never-GC values; add the "known
   managed object" direction so release/pin/store_root skip the granule map.
4. Root reload as a non-forwardable inline load (6-10%): backend + codegen.
5. `is_type_object` fast path (3-4%): isinstance lowering.
6. Track 1: native-object triple validation (~40% of the worker's object
   phase), exports wire per worker (1.1 s x 224), 12 GiB shared envelope.

## Update 2026-09-04 — native-export JSON slicing `[DENIED; removed]`

The current real `py_ast` PCO worker confirms that repeated export projection
grew into a first-order owner: 41.87s / 561.548B instructions / 5.27GB, with
`_native_export_from_wire` at 23.05% of 12,400 caller samples. The full wire is
12.97MB per each of 224 fresh workers; conservative dependency sizing gives a
1.82MB median slice and 1.76MB for `py_ast`.

The candidate preserved the existing v1 tuple/default/type decoder and wrote
one module shard plus dependency/derived/reference indexes. Focused gates were
117 passed, strict multi-source closure was real, and a two-module pcc1 binary
compiled/ran at 198MB with output 42. The real boundary denied it: two frozen
pcc1 implementations both wrote 224/224 shards and indexes, then crashed with
Darwin `Thread stack size exceeded` at 80.98/82.79s and 8.20/8.33GB tree peak.
The second form had already eliminated the first form's extra full-JSON decode,
so moving that decode again is not a new proposal.

All production slice code and candidate-only tests were forward-removed; the
restored packet is 116 passed. No Stage2 ran. Full evidence, crash-report names
and excluded harness arms are in
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/033-native-export-slice-denied-stack-overflow.md`.

## Update 2026-09-04 — explicit frontend-cycle teardown `[DENIED; removed]`

The next candidate attacked the roughly 20% GC0 mark/subtract share without
changing collector semantics. Once the direct module was frozen it explicitly
broke Function/Block and CodeGen/ClassLowering cycles, cleared frontend/export
containers, dropped caller locals, and only then collected. The second
direct-module collection remained.

Focused tests and strict worker closure were green; v17 pcc1 was libSystem-only
and its function canary printed 42. On the identical `py_ast` manifest the PCO
was byte-identical, and wall moved 41.87s -> 38.59s, but user CPU moved only
38.17s -> 37.29s, instructions 561.548B -> 561.388B (-0.028%), and RSS/
footprint were unchanged. The explicit field clearing replaced rather than
removed collector work. `[DENIED]`; production code was forward-restored and
no Stage2 ran. Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/034-pre-emit-cycle-teardown-denied.md`.

## Update 2026-09-04 — emitter line transport `[CONFIRMED structural; speed weak]`

The first vertical transport step removes the complete-module assembly string
between indexed emit and the PCO assembler. The emitter returns its existing
line chunks, the directive driver consumes them, and the text encoder consumes
physical text lines; string APIs remain compatibility oracles. The first v18
build exposed multi-line global/data chunks and failed before pcc1 publication.
A generic per-chunk newline split closed the whole directive failure class.

The v19 pcc1 is libSystem-only and canary-green. On the identical real
`py_ast` manifest, PCO bytes remain `9987edea...`; wall is 41.87s -> 38.66s,
CPU 39.36s -> 38.48s, RSS 5.253GB -> 5.118GB, while instructions are nearly
neutral (561.548B -> 561.423B). Retained as output-exact representation/memory
progress, not claimed as the Stage2 speed solution. Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/035-line-transport-structural-retention.md`.

## Update 2026-09-04 — packed stackmap section `[CONFIRMED]`

The v19 ASM-only replay bounds assembly plus NativeObject publication at
26.42s / 410.997B instructions / 3.46GB on `py_ast`. Numeric data directives
are about 474k lines (70% of non-label lines), and the fixed-width v2 stackmap
is the first complete family migrated into `StructuredAArch64Module`.

The producer now packs final ABI bytes plus function relocations directly;
final record ordering stays in a `CompilerIntArena` rather than recreating
per-record Python tuples. The textual renderer remains an exact oracle. A
source-frozen libSystem-only v20 pcc1 builds and runs; on the identical real
worker its PCO is byte-identical while wall is 38.66->35.94s, CPU
38.48->35.88s, instructions 561.423B->548.673B and RSS 5.118->4.086GB.
`[CONFIRMED]`; the remaining roughly 23.7s above ASM-only is still active and
must be re-profiled before choosing the next structured family. Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/036-structured-stackmap-section.md`.

## Update 2026-09-04 — direct Section-to-packed PCO `[CONFIRMED]`

A full v20 profile assigned 19.00% inclusive to native-object validation,
17.53% to `NativeObject.from_sections`, 15.41% to assembly and 4.91% to final
encoding. The worker now validates source Sections once, encodes canonical
packed records directly, then runs the complete packed-byte validator; it no
longer constructs a second NativeSymbol/NativeSection/NativeRelocation graph
or converts it back to Sections twice.

Two pcc1-only transfer failures were closed before retry: both native-object
readers now preserve ASCII diagnostics through the supported UTF-8 codec, and
all packed special sections cursor-walk relocation records instead of
materializing a generator as a tuple. The v23 pcc1 is libSystem-only and its
function smoke passes. On the identical timed `py_ast` replay, exact PCO bytes
are preserved while wall falls 35.53->28.76s, CPU 35.47->28.63s,
instructions 548.264B->389.073B and RSS 4.086->3.977GB. `[CONFIRMED]`.
Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/037-direct-section-to-packed-pco.md`.

## Update 2026-09-04 — AArch64 text-entry scalar arena `[CONFIRMED structural]`

`assemble_text_lines` no longer builds one `(kind, payload)` Python tuple per
physical instruction/data chunk. A `CompilerIntArena` carries kind/index
scalars while strings and inline bytes remain explicit cold side tables. The
v24 pcc1 is libSystem-only and canary-green; exact `py_ast` PCO bytes remain
unchanged. Wall moves 28.76->27.86s and CPU 28.63->27.82s, but instructions
and RSS are flat. Retained as required representation deletion, not a material
speed claim. The next slice must move actual high-frequency opcode/operand
families into the arena rather than refining the entry container. Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/038-arm64-text-entry-arena.md`.

## Update 2026-09-04 — structured unscaled load/store `[CONFIRMED]`

The structured transport now carries final scalar words for all emitter-owned
`ldur/stur/ldurb/sturb` shapes. The frozen `py_ast` object has 72,970 such
instructions and 131,857 instruction strings still on the oracle path. The
assembler preserves positions/labels but no longer parses operands for the
migrated records. v25 pcc1 is libSystem-only/canary-green and publishes the
exact PCO. Against v24, wall is 27.86->27.54s, CPU 27.82->27.37s,
instructions 389.051B->375.069B and RSS 3.977->3.441GB. `[CONFIRMED]` for the
family and material memory reduction; source helpers still create these
strings before final conversion, so the full data plane remains open.
Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/039-structured-unscaled-load-store.md`.

## Update 2026-09-04 — structured move family `[CONFIRMED]`

`mov/movz/movk` add 38,319 scalar-word records to the 72,970 unscaled
load/store records, leaving 93,538 fallback instruction strings in the real
`py_ast` worker. v26 pcc1 is libSystem-only/canary-green and its PCO is exact.
Against v25, wall moves 27.54->27.32s, CPU 27.37->27.19s, instructions
375.069B->369.166B and RSS 3.441->3.242GB. `[CONFIRMED]` for deterministic
representation/instruction/memory improvement; standalone speed is weak. The
next family must add symbol/relocation IDs for 47,015 `bl` records.
Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/040-structured-move-family.md`.

## Update 2026-09-04 — structured calls and current-source Stage2 `[CONFIRMED; task open]`

The relocation-bearing transport now carries `(line_index, word,
relocation_kind_id, symbol_id)` and covers 47,015 direct `bl` instructions in
the frozen `py_ast` worker. Recursive/local targets resolve exactly; cross-atom
and external targets publish exact BRANCH26 relocations. Together with the
unscaled and move families, 158,304 instructions are structured and 46,523
remain on the text path. The 180-test focused packet, strict closures and exact
PCO differential pass. Host/native cost-model routing avoids making host
CPython pay the native instruction-tail conversion.

The source-current v28 Stage2 then completed under the 8 GiB process-tree cap:
1005.626s end-to-end, 2301.856s timed-tree CPU, 7.620GB peak tree RSS, runnable
libSystem-only pcc2. This is about 25.5% lower wall than the old safe v18
transfer, but remains 5.875x the v28 Stage1 (171.16s). The stage task is not
complete and no fixed point/GC1-4 claim is made. Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/041-structured-call-and-stage2-transfer.md`.

## Update 2026-09-04 — owned JSON export projection `[DENIED; removed]`

Reusing `json.loads` dictionaries while restoring tuple-valued ABI fields
improved the smallest pcc1 worker, but did not improve the representative
`py_ast` worker CPU and reduced its RSS only 3.5%. Both unconditional and
native-only generations also produced slower Stage1 transfers (190.99s and
184.01s versus 171.16s). The candidate was forward-removed and the source
identity exactly restored to v28. A future export-plane slice must avoid
decoding unrelated modules rather than merely reusing the decoded containers.
Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/042-owned-export-projection-denied.md`.

## Update 2026-09-05 — indexed native exports `[CONFIRMED; wall still memory-bound]`

One indexed export file now carries per-module payloads, exact AST-derived
dependency rows, a deduplicated unique-class preload plane and a lazy compact
contextual L1CodeGen host schema. It does not recreate the denied 224-shard +
224-index callback graph. A tiny native worker moves 1.58s/632MB/13.557B to
0.20s/128MB/2.107B; `py_ast` moves 27.72s/3.145GB to
25.46s/2.413GB with exact PCOs.

The first full run exposed and closed two whole failure classes before retry:
all 224 dependency rows had been erased by an unreliable pcc1 string-set, and
contextual mixins needed the merged host method ABI without materializing all
host implementation modules. The final P table has 963 edges across 186
non-empty modules; both failing workers are green. The guarded Stage2 completes
at 960.951s / 2046.211s tree CPU / 7.638GB peak, runnable and libSystem-only.
Versus v28 this is -4.4% wall and -11.1% CPU. The CPU/12 lower bound is now
170.5s, below Stage1, but old memory floors still cause 1,053 small-lane
admission denials. Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/043-indexed-native-export-plane.md`.

The next slice is a packed direct-module handoff across a process-exit boundary.
Re-fitting an AST-only floor was rejected because safe coverage of every
outlier retains a 388s small-lane estimate; process lifetime, not a looser
average floor, owns the remaining gap.

## Update 2026-09-05 — packed process boundary `[CONFIRMED structural; task open]`

A versioned direct-module sidecar now carries the pre-stackprep indexed kernel
as list/dict cold JSON plus raw i64 arenas.  The frontend process exits before
a fresh pcc1 decodes and emits; established large lanes remain ASM and the
small lane remains PCO.  Tiny, py_ast, pipeline, runtime_abi, class_gen and
cli_bootstrap outputs are exact.  Medium/heavy/worst split time is essentially
neutral while maximum per-worker RSS falls 22-48%.

The source-frozen v48 GC0 Stage2 completes at 911.658 s / 2104.575 tree-CPU
seconds / 7.675 GB, runnable and libSystem-only.  This is only 5.1% wall better
than v41 and CPU is 2.85% worse: five sequential lane pairs and the old
combined-process frontend floors leave the machine under-filled.  The full
226-module sample now measures 629.279 frontend worker-seconds and 1081.721
emit worker-seconds; charged frontend floors are 4.9x observed peaks.  The
next proposal unifies the frontend/ASM/PCO phases and uses phase-specific
front floors plus exact sidecar-byte emit floors.  Full evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/044-indexed-process-boundary-stage2.md`.

## Update 2026-09-05 — unified indexed phases `[CONFIRMED; task open]`

The v49 runner removes the five sequential lane-pair barriers and executes one
frontend, one ASM and one PCO phase.  The guarded source-frozen Stage2 is
745.327 s /2082.828 tree-CPU seconds /7.696 GB, exact, runnable and
libSystem-only.  This cuts v48 wall18.2% with1.0% less CPU.  Phase elapsed
times are139.405/150.374/240.778 s; the offline scheduler predicted their
530 s total exactly.  Full v49 peaks show the deliberately conservative PCO
base charges288.43 GiB for98.32 GiB observed, so the next finite slice tightens
the generic sidecar-byte formula while preserving5%+100 MB per-item coverage.
Evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/045-unified-indexed-phase-scheduler.md`.

## Update 2026-09-05 — exact admission and link width `[CONFIRMED; task open]`

Full-population sidecar floors plus deterministic first-fit reduce source-
frozen v50 Stage2 to619.056 s under8 GiB.  Decoupling the measured host linker
class from pcc1 codegen (`PCC_MACHO_LINK_JOBS=8`) reduces the frozen final link
89.278 ->61.350 s at4.842 GB with exact output; v51 transfers it to a complete
595.457-second Stage2,7.679 GB peak, runnable/libSystem-only pcc2.  Stage1 is
163.05 s, so the ratio remains3.65x.

An explicit common16 GiB envelope was implemented without changing the8 GiB
default, but its first preflight correctly refused under14.7/16 GiB swap use
and46.9 GiB reclaimable RAM; no run or claim exists.  Same-pcc1 ASM/PCO
differentials now select the next owner: production `py_ast` spends13.86 s and
1.16 GB after ASM publication, with46,523 remaining text instructions outside
the packed instruction plane.  Full evidence:
`docs/goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/046-exact-admission-and-link-width.md`.

## Update 2026-09-05 — producer words and native buffer closure `[pending]`

The subsequent qualified v69 producer transport removes instruction strings
for 20,822,694 of 21,264,800 instructions in the retained 227-sidecar population.
Another 442,106 still pass through text encoding despite zero final assembler
fallback. On py_ast, v65 -> v69 keeps exact PCO while instructions fall 3.83%
and sampled RSS 12.03%; cli ASM instructions rise 1.11% while RSS falls 2.64%.
This is not a general Stage2 speed acceptance. Last qualified v69 Stage1 is
167.80s; the last complete Stage2 remains v58 544.963s on different source.
Full results and the split inventory's explicit timeout:
[evidence 058](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/058-direct-instruction-producer-transport.md).

The next slice closes the full instruction buffer, not another opcode helper.
`PackedAArch64TextBuilder` shares one directive/layout parser and one final
encoding owner between text and native paths. The driver now streams chunks,
removing physical_lines/text_lines and their record-index remaps. Its first
text error is deferred until driver validation completes, preserving the
historical diagnostic priority without restoring instruction lists.

The 204-test focused packet and full contextual closure pass. The attempted
standalone imported-source pcc1 canary was a harness error: both host and pcc1
omitted the backend source closure and generated an unavailable stub. It did
not exercise the new builder and is not attributed to it. The corrected test
uses a source-manifest-checked native indexed-worker replay. Frozen v70 is
building under an external 8GiB tree-RSS breaker; performance and full closure
remain pending. Upstream helper/module lists, producer text, normal ASM
serialization and verifier projections are still named open boundaries.
[Plan](../design/pcc-native-aarch64-emission-buffer.md),
[evidence 059](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/059-native-text-buffer-core.md).

## Update 2026-09-05 — final-layout native branch fixups `[pending]`

The receipt-selected v70 indexed-worker canary passed (ASM and PCO equality,
1 test in 0.28s). Readback of its Stage1 receipt is 200.58s / 757.65 tree CPU
seconds versus v69's 167.80s / 682.24; these are not adjacent paired arms and
do not establish cause, but they prohibit a no-regression claim. The retained
v70 py_ast PCO replay completed at 17.56s / 1,375,371,264B sampled tree peak.

The next prerequisite of the full buffer migration is to retain unresolved
branch/call targets as native fixups and resolve them at the canonical builder's
final PCs. Currently the emitter must compute a separate label map before it
can hand words to that builder. The minimized forward-branch/inline-data/
recursive-and-cross-atom-call test failed with missing `append_branch`, before
production edits (`test_native_text_builder_resolves_forward_fixups_from_final_layout`,
1 failed in 0.10s). This is a missing transport capability, not a claim that
existing emitted code is incorrect.

### Code Change

The existing native entry/relocation arena now carries branch/call fixup kinds
and reuses the text encoder's branch-range and atom rules at final layout. Both
direct and text-encoded producer records use this driver boundary, so the
emitter's independent label-map pass and PC accounting are removed. No second
assembler or per-instruction object table was added. Helper/module lists and
the remaining ASM/verifier families stay open until actually removed.

### CONFIRMED — bounded native prerequisite, not full buffer closure

198 focused tests and the separately logged 227-module contextual gate pass.
v71 Stage1 completes at 190.48s / 751.50 tree CPU seconds, libSystem-only;
its function canary prints 42. Native text-buffer plus HFA/cold-landing
ASM/PCO worker checks pass. The retained py_ast PCO and cli ASM are exact
against v70: CPU 16.87→16.68s and 30.21→29.89s, instructions essentially flat,
memory within 1%. This supports structural migration, not a material speedup.
The historical Stage1 regression remains un-attributed and open.
Full source identities, watchdogs, failed invocation accounting and metrics:
[evidence 060](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/060-native-final-layout-fixups.md).

## Update 2026-09-05 — incremental module builder `[pending]`

The next vertical prerequisite moves the existing directive parser and final
section normalization into a single module-scoped append/finalize owner. This
allows the emitter to hand instructions directly to the native text builder,
rather than retaining the module line list for a later `assemble_lines` call.
The existing complete-line API becomes a wrapper over the same implementation;
there must not be a second parser. The new incremental full-driver differential
failed before production edits (missing `AArch64ModuleBuilder`, 1 failed in
0.09s). First prove exact sections, relocations, diagnostic priority and arena
cleanup, then connect the producer. Full helper-list closure remains open.

### Code Change

Factored the existing parser/finalizer into `AArch64ModuleBuilder`, registered
the single per-module phase shell in the record inventory, and exposed the text
builder's final label offsets and size. The complete-line driver remains an
adapter over the same implementation. No backend rule or optimization changes.

### CONFIRMED — incremental driver prerequisite

210 focused tests and the 227-module contextual gate pass. v72 is
libSystem-only/canary42, Stage1 168.96s / 697.35 tree CPU seconds, sampled peak
4.804GB. All three native text/HFA/cold-landing worker checks pass. Adjacent
v71/v72 py_ast PCO outputs are byte-identical, CPU 16.14→16.11s, instructions
+0.17% and RSS essentially flat. This is structural qualification, not a speed
claim or producer-list closure. Full artifacts and the unavailable LLVM-MC
oracle are recorded in
[evidence 061](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/061-incremental-module-builder.md).

### Follow-up — public append allocation-failure cleanup

Before connecting the producer, fault injection found that a `MemoryError`
from `append_encoded` left the module's arenas open when the public append
interface was used directly (1 failed in 0.08s). The complete-line wrapper had
its own finally and masked this case. The public method now closes on
non-EncodeError exceptions while keeping historical EncodeError deferral.
The driver/structured packet passes (138 tests). This small follow-up is newer
than the v72 native receipt and must transfer with the producer migration.

## Update 2026-09-05 — module producer bridge `[pending]`

The new three-function regression observed the missing finalized transport
before implementation (1 failed in 0.12s). Unoptimized structured PCO emission
now consumes each function into the incremental builder, keeping no module
instruction list, no line-coordinate arena and no separate stackmap/unwind
label scan. The captured instruction arena is cleared after each function and
its maximum retained size is tested against the full module instruction count.

### Code Change

One `_NativeAArch64Emission` phase shell owns the builder and a reusable packed
scratch instruction; its borrowed capture arena remains module-global until
the established emission-scope finally closes it. Existing opcode/branch/
directive/stackmap and memory-barrier rules are reused. Production worker
publication consumes finalized sections directly. The original line API stays
an exact oracle, and the structured inventory separately counts residual
encoder lines on the native path. Both new concrete classes are registered.

212 focused tests pass, including exact sections, HFA/cold paths, precise stack
maps, source inventory and capture-size assertions. Native qualification is
pending. Function/helper lists, their placeholders, compact-unwind data
directives, residual producer text, normal ASM publication and verifier/CFG/
def-use projections remain open. This is not complete instruction-buffer
closure or a speed claim.

### Contextual ABI audit before native build

The first contextual gate passed its original assertions, but generated IR
inspection found `py_obj_getattr("get4_unchecked")` in the new native sink:
`direct_records` was initialized through a borrowed-arena-returning function
without an explicit field type. This is a distinct source shape from the
historical inherited-field/index defect in
`pcc1-exact-compiler-arena-field-method-abi.md` (read in full before editing).
No generic frontend or inheritance repair was guessed here.

The native-emitter contextual gate now forbids that dynamic method projection
and unavailable stubs. Its assertion failed against the retained pre-annotation
IR. Declaring the borrowed fields as `CompilerIntArena` and `list[str]` makes
both captured and scratch `get4_unchecked` calls direct four-i64 aggregate ABI
calls. The updated gate passes in 46.52s and the 212-test packet passes again.
Artifacts: `build/native-module-producer-context-typed`. No expensive compiler
build was run with the dynamic aggregate-return call.

### CONFIRMED — module-level native producer

v73 completes Stage1 at 165.83s / 674.49 tree CPU seconds, libSystem-only;
function output is 42 and all three native worker canaries pass. Real py_ast
PCO remains exact with -2.03% instructions and -2.10% process maximum RSS.
The CLI ASM is exact and effectively flat in CPU/instructions/memory.
[Evidence 062](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/062-native-module-producer.md)
records source identities, gates and both adjacent comparisons. Module-level
instruction slots are gone; function/block/helper lists, their placeholders,
residual text and the verifier family remain explicit next work.

## Update 2026-09-05 — native function and block streaming `[pending]`

The expanded three-function regression failed before implementation: the
native module still called `_emit_function` without a sink and retained both
function and block output lists (1 failed in 0.16s). Both enclosing emitters
now append directly to the existing native scope and return empty list adapters
on that path. The seven block append/extend sites and three packed stack-map
append sites were enumerated by enclosing function before their narrow rewrite.
ASM calls keep their existing list path.

Memory-barrier depth/terminal checks were factored from the existing memory
pair pass and shared with the streaming scope, avoiding a second marker rule.
Native and legacy unbalanced-marker diagnostics match for all three tested
shapes. The original nested-emission test still exercises both modes; its
interceptor now forwards the new optional sink argument.

242 focused tests pass, including exact native function/block empty-output
assertions, capture bounds, GC reloads, HFA/cold paths and target passes.
Helper return lists, placeholder scalar ordering, compact-unwind data lists,
remaining producer text, ASM publication and verifier projections stay open.
Native qualification is pending; no speed or full closure claim is made.
