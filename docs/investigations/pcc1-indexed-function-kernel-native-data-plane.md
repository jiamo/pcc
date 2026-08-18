# Investigation: migrate pcc1's compiler hot path to an Indexed Function Kernel

## Status

active

## Problem Description

The current compiler has native control flow but executes its internal data
through generic Python object projection.  No.75's source-correct cold chain
records Stage2 977.866 seconds: IR-to-assembly native emit is 583.303 seconds,
and the final owned assembler/linker is 118.087 seconds.  Even deleting the
linker cannot approach the 107.8-271.7-second Stage1 range.

Controlled per-operation evidence explains the flat profile: pcc1 is faster
than CPython for scalar arithmetic and calls, but 2.5-4.6x slower for list,
dict, attribute, and dynamic-string operations.  The compiler workload is
dominated by `TypeDesc`, instruction views, strings, tuples, lists, dicts,
sets, slots, and safepoint records.  Each generic heap operation pays type,
provenance, granule/radix, root/pin, barrier, refcount, allocator, and GC-index
costs.

Fresh item311 profiles split the emit window roughly between stack-map planning
(35.4%) and AArch64 register allocation (33.8%).  Their common leaves are
`pcc_gc_granule_is_object_start` (16.0%) and `pcc_gc_load_ptr` (7.6%), showing
that the owner is the shared execution representation rather than one planner
helper.  No.78 cached a duplicated 59,984-instruction frame-protocol scan but
reduced instructions only 1.52%, worsened cycles/CPU, and was removed.

The migration must implement the value-model north star, not create a compiler
semantic exemption: semantic types remain unchanged; identity-free immutable
compiler records may take value/arena projection; object projection is lazy at
diagnostic/escape seams; pointer-bearing payloads remain visible to the unified
five-GC root/update schema; Dyn and unsupported shapes keep a correct slow path.

Predecessors:

- `pcc1-stage2-emit-throughput-and-memory.md` Updates No.71-No.78;
- `pcc1-native-vs-cpython-per-operation-cost.md`;
- `granule-span-lookup-radix.md`;
- `docs/goal/evidence/2026-08-27-mold-packed-linker-transfer.md`.

## Repro

Primary frozen input:

```text
build/stage2-current-object-inputs-no62-v1/item_311.ll
module: pcc.py_frontend.codegen.call_expression_lowering
IR bytes: 5,108,635
blocks: 9,474
instructions: 59,984
safepoint records: 20,004
pre-migration assembly SHA-256:
ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251
```

Use `scripts/pcc_emit_rank.py --item-index 311` for receipt-bound worker
measurements.  The complete-stage command remains deferred until the vertical
slice is source-stable and passes its worker gate.

## Test [CONFIRMED]

The performance failure and representation mechanism are confirmed by the
current source-correct Stage2 profile, controlled per-operation benchmark,
early/late caller flamegraphs, and the denied No.78 substitution.  The new
kernel's focused gate must prove:

1. stable block/value/type/opcode IDs and deterministic construction;
2. supported hot passes consume indexed storage without normal-path diagnostic
   projection materialization;
3. diagnostic and unsupported projections preserve exact existing messages;
4. pointer-bearing value payloads preserve GC0..4 root/update semantics;
5. item311 assembly remains byte-identical.

## Proposals

- No.1 End-to-end Indexed Function Kernel tracer bullet [selected by user]
- No.2 Pass-local wrappers or one-view removal [DENIED BY PREDECESSOR]
- No.3 Global managed-pointer/refcount bypass [DENIED BY PREDECESSOR]

## No.1 End-to-end Indexed Function Kernel tracer bullet

### Code Change

Create one deep Module whose interface owns a function's stable IDs, packed
instruction/type/value tables, block ranges, and shared indexed analysis.
Parser/prepare constructs it once.  Stackprep, liveness, precise stack maps,
AArch64 register allocation and emission consume it directly.  Diagnostic
projection is lazy; unsupported instruction/type shapes take the existing
semantics-preserving object path.

The first accepted slice must cross every named pass.  Landing only a wrapper,
one cursor, one pass-local cache, or metadata without replacing hot object
projection fails the deletion test and is not progress.

### pending

Item311 must improve wall and instructions by at least 25%, lower RSS/physical
footprint materially, and preserve exact assembly.  After focused self/no-
libpython and GC gates, one source-frozen Stage2/Stage3 transfer must preserve
the pcc2/pcc3 fixed point.  Parallel function/module emission starts only after
the kernel plan and output ordering are frozen and per-worker memory falls.

## No.2 Pass-local wrappers or one-view removal

### Code Change

Wrap `ParsedFunction`/`CompactParsedInstrArena`, or remove one generator/view,
without changing the end-to-end representation.

### DENIED BY PREDECESSOR

The prior private cursor removed seven generator/view projections, kept
byte-identical output, and improved real oversized shards only about 1.04x.
No.78's duplicated protocol-scan removal saved only 1.52% instructions.  Do not
repackage either as the native data plane.

## No.3 Global managed-pointer/refcount bypass

### Code Change

Treat all compiler values as managed-by-construction and call unchecked
incref/decref/barrier helpers globally.

### DENIED BY PREDECESSOR

The managed-refcount ABI dereferenced raw C pointers stored through Dyn and
corrupted allocator state.  Static provenance must be attached to exact
kernel/value projections, with the existing generic slow path for Dyn/escape;
there is no global bypass.

## Update 2026-08-27 — first vertical host/self-closure slice

The initial deep slice now constructs one stable kernel and carries its IDs and
shared def/use/last-use tables through verifier, stackprep, precise stack maps,
AArch64 register allocation/target planning, and AArch64 emission. The normal
item311 host path constructs zero `CompactParsedInstrView` objects (down from
119,968, exactly two per instruction before the verifier migration) and emits
the exact historical assembly SHA-256
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`.

The self-backend subsystem gate is 512 passed. Twelve changed-module closure
IR artifacts were each passed through real self-backend assembly emission, not
only `--emit-llvm`, and all succeeded. The first source-frozen pcc0 -> pcc1
attempt found one real smaller-subset defect: a diagnostic-only list
comprehension in `legacy_used_values` lowered its integer index through a
pointer helper and the self IR verifier rejected it. The replacement is an
explicit integer loop; stackprep also stopped publishing both legacy
string/object analysis projections on its normal path. Kernel and AArch64
materializer module self-emits are green after the fix.

No pcc1 performance verdict exists yet. An unrelated long GC3 bootstrap was
already consuming the shared performance machine, so the failed source-frozen
Stage1 attempt is correctness evidence only and will not be timed or reused as
a performance arm. The next valid action after the machine is idle is one
fresh candidate build followed by the pre-registered item311 gate.

## Update 2026-08-27 — pcc1 verdict and value-arena denial

The source-frozen pcc1 candidate (`21c615ff...`, compiler `1df30556...`) and
fresh control (`dd808447...`) emitted byte-identical item311 assembly. The
candidate reduced footprint from 6.874 GB to 4.999 GB, but only improved wall
44.60 -> 41.32 seconds (1.079x), CPU 44.05 -> 41.03 seconds (1.074x), and
instructions 636.39 -> 599.88 billion (1.061x). It therefore misses the 1.25x
wall **and** instruction gate. No Stage2 transfer is authorized.

A direct dynamic scalar arena then tested the missing value-model mechanism:
host list oracle, pcc-compiled `malloc/realloc`, raw i64 load/store, integer-only
stored provenance, and explicit close. Closure/self-emission and 515 focused
tests were green, but the source-frozen worker was worse than the indexed-list
candidate: 43.88 seconds, 607.97 billion instructions, 4.981 GB footprint. The
1.35% instruction regression bought only 0.36% additional memory. This design
is `[DENIED]` for kernel scalar tables; they were restored to the v2 projection.

Fresh v2 caller attribution puts `build_function_stack_map_plan` at 35.21%,
`_emit_function` at 51.52%, call emission at 20.80%, materialization at 8.53%,
and register allocation at only 1.61%. The migration successfully removed
regalloc as an owner; further pass/regalloc work would now be off-mainline.
Early v3 attribution bounds scalar-arena get/open overhead around 1%, which is
enough to explain the regression but far too small to meet the total target.

The next value-model proposal must batch a whole immutable record/span per
operation: packed safepoint/location/reload and slot/type/operand records with
object/string side tables only for diagnostics. A per-scalar arena object
method, even backed by raw memory, is not an accepted native projection.

## Update 2026-08-27 — batch-record value projection passes the worker gate

The corrected value-model slice writes and reads whole two/three/four-i64
records per arena operation. AArch64 safepoint scalars, location spans, root
locations, and managed reloads no longer persist as one Python dataclass graph
per record; labels and diagnostic spellings remain in traced side tables.
Cross-module valueclass metadata/return inference was fixed generically so the
arena provider and stackmap consumer share the same aggregate ABI. The real
merged pair passes the self verifier/emitter, and host item311 materializes
zero instruction views and zero safepoint diagnostic records.

Source-frozen pcc1 `2606324a...` passed the registered item311 gate. Bracketing
control walls were 61.12 and 52.76 seconds; candidate was 33.33 seconds. Using
the faster control gives 1.583x wall, 1.509x CPU, and 1.594x instructions;
footprint falls 27.4%, and assembly remains `ff943e10...`. Even the earlier
44.60-second clean control gives 1.338x wall. Proposal No.1 is `[CONFIRMED]` at
the worker claim level.

Stage2, Stage3, fixed point, and five-GC equality remain unclaimed. The next
boundary is correctness transfer, not another worker optimization.

## Update 2026-08-27 — final-source GC0 transfer and fixed-point artifact

The fallback transfer first exposed a probe-model error, fixed by giving
self-backend Modules closed-world sibling exports without an `L1CodeGen` mixin
host. The complete fallback/IR ratchet passes 42/42 without raising the
baseline. The first pcc1 -> pcc2 attempt then found a distinct ownership bug:
an object IfExpr phi borrowed an owned local that was released at loop step,
leaving a dangling `ir.Constant` and emitting `or ptr null`. That generic
frontend defect is resolved separately in
`pcc1-owned-ifexpr-local-transfer.md`; the rebuilt pcc1 passes its canary under
GC0/3/4 and self-emits the exact former failure Module.

Final source `bd27a19d...` / pcc1 `8e94030a...` re-passes item311 against fresh
bracketing controls. Candidate is 30.00s versus controls 44.68/43.15s; using
the faster control gives 1.438x wall, 1.439x CPU, 1.563x instructions and
39.1% lower footprint with exact `ff943e10...` assembly.

GC0 cache-off Stage2 completes formally in 772.453s. Stage3 twice exhausted
underestimated 720s/960s wrapper budgets after all 485 assembly receipts were
complete and during/after the owned linker. The second linker published a
175MB `pcc3.tmp` whose raw SHA is exactly equal to pcc2
(`8f5884dc...`). That preserved artifact links only libSystem, passes `--help`,
and itself compiles/runs a no-libpython self-backend function smoke (`42`). It
was copied byte-for-byte to the missing final pcc3 path and raw `cmp` passes.

This proves the GC0 fixed-point artifact, but there is deliberately no claimed
Stage3 wall time or successful wrapper result line. The final cache-enabled
five-GC matrix must produce ordinary completion receipts; its cold/warm times
are correctness evidence, not comparable performance.

## Update 2026-08-27 — whole-Stage2 owner profile selects packed instructions

The accepted final source now has one complete process-tree Stage2 profile.
The same-source unsampled Stage1/Stage2 wall is 260.56/772.453 seconds, so
Stage2 remains 2.965x slower. The sampled diagnostic Stage2 completed with
1.018 trillion instructions, 11.613 GB peak aggregate process-tree RSS, and
15.705 GB peak footprint. Its self-backend IR-to-output window is 74.4% of the
stage, native emission alone is 63.3%, and the owned linker is only 11.0%.

Final-source item311 caller attribution moves the dominant owner earlier than
the already-packed safepoint loop: `_parse_instruction` is 42.36% inclusive
and the full parser is 56.54%. A parse-to-stackmap structural inventory counts
59,984 instruction payload tuples / 427,967 fields and, across the reachable
graph, 287,605 tuples, 112,635 lists, 9485 dicts, 137,468 `TypeDesc` records,
and 17,471 `SlotInfo` records. Instruction, kernel, and safepoint diagnostic
projection counters are all zero; that only proves lazy views are absent, not
that their persistent tuple/dataclass/list/dict data plane disappeared.

The next proposal is therefore the parser-to-emitter packed instruction table.
Its `_parse_instruction` Amdahl ceiling is 1.735x for the representative worker
and about 1.37x for the complete sampled Stage2 if the worker share
generalizes. It cannot by itself close the 2.965x Stage2/Stage1 gap; shared
analysis, value-record, and explicit object-projection closure remain required
after it. Full receipts and counts are recorded in
`docs/goal/evidence/2026-08-27-native-data-plane-stage2-profile.md`.

## Update 2026-08-28 — per-field packed raw arena is denied

The first parser-to-emitter packed proposal stored opcode, tags, payload IDs,
and sequence spans in one module-owned `CompilerIntArena` family and retained
only traced text/`TypeDesc` side tables. It structurally removed all 59,984
live item311 instruction payload tuples, reported zero diagnostic/unsupported
projection, and emitted exact `ff943e10...` assembly. It nevertheless measured
82.33 seconds / 1.130 trillion instructions / 4.495 GB footprint versus the
accepted 30.00 seconds / 407.414 billion / 4.190 GB.

A caller flamegraph made the failure mechanism explicit: parser work was only
0.11% and raw `get2/get4` leaves only 0.15%, while verifier was 74.01%, kernel
construction 24.55%, and bound instruction getter paths 43.88% inclusive.
Every pass paid another Python call/argument/type/GC boundary for every field
of the same instruction. Replacing bound getters with static module functions
barely changed the result: 81.41 seconds / 1.113 trillion instructions, with
footprint worsening to 7.120 GB. The inclusive adapter frames were not pure
removable overhead.

This proposal is `[DENIED]`. It repeats the earlier per-scalar-arena mistake at
instruction scale and must not be revived as “packed” merely because its
persistent storage is raw. The compiler files were restored to the accepted
pre-proposal source; no Stage2 run was authorized. The next proposal must read
a complete schema record per operation through a batch value/intrinsic ABI and
must reuse analysis rather than making verifier, kernel, stackprep, and
stackmap decode the same fields independently. Full receipts are in
`docs/goal/evidence/2026-08-28-packed-instruction-raw-arena-denial.md`.

## Update 2026-08-28 — batch/raw follow-ups do not rescue tagged decoding

Consumer-local raw reads progressively moved item311 from 82.33 to 69.88,
58.93, then 49.30 seconds while preserving exact assembly. Instructions fell
from 1.130 trillion to 675.375 billion and footprint to 4.679 GB. A single
shared local-valueclass batch interface measured 54.84 seconds / 691.327
billion / 4.680 GB. All remain decisively behind the accepted 30.00 seconds /
407.414 billion / 4.190 GB arm and none entered the worktree.

The profiles show why continued getter work is off-mainline. Kernel and
verifier batching did remove their representation overhead, but the owner then
moved to stackmap, verifier semantic checks, and emit. A generic tagged record
still asks every pass to decode the same schema. The next proposal must be
kind-specific and publish shared semantic facts once from the kernel; raw
loads/getters are no longer an optimization candidate by themselves.

## Update 2026-08-28 — compiler-owned i64x4 projection removes helper tax only

A generic `pcc.unsafe` four-i64 value projection now lowers four contiguous or
strided raw loads directly at the call site. The first full-closure build found
that closed-world `pcc.unsafe` source exports were overwriting the intrinsic's
synthetic `UnsafeI64x4` return with the fail-loud stub's `Any` annotation. A
focused external-export regression reproduced the exact `DynType` aggregate
marshal failure; the import binder now keeps the compiler intrinsic contract
authoritative, and the source-frozen strict pcc1 builds successfully.

Replacing the shared batch prototype's four scalar reads with this intrinsic
improves item311 from 54.84 to 48.53 seconds and 691.327 to 671.887 billion
instructions with unchanged 4.679 GB footprint and exact `ff943e10...`
assembly. This confirms that a shared batch API can avoid ordinary Python
helper overhead. It does not rescue the representation: the accepted arm is
still 30.00 seconds / 407.414 billion / 4.190 GB. The packed tagged arena
remains `[DENIED]`; the intrinsic is retained only as the foundation for
kind-specific TypeDesc/slot/value projections. Full receipts are in
`docs/goal/evidence/2026-08-28-unsafe-i64x4-value-projection.md`.

## Update 2026-08-28 — TypeDesc canonicalization and dense slot foundation

The new record inventory proved that item311 retained 137,468 TypeDesc
identities for only 74 structural values. Prefix parsing repeatedly allocated
leaf types and overwrote its cache; stackprep then created another pointer type
per alloca/GEP. Per-module leaf/pointer canonicalization plus kernel type-ID
publication now leaves exactly 74 TypeDesc objects after stackprep, down from
137,468, with exact assembly and 358 focused self-backend tests green.

SlotInfo had the same but smaller shape: 17,471 identities for 1,657 structural
`(offset,type)` values. Stackprep now interns those compatibility records and
publishes an authoritative dense `value_id -> slot_id -> offset/type_id` plus
alloca table. SlotInfo identities fall to 1,657 and the total reachable graph
falls 595,927 -> 442,715 objects.

Performance is deliberately not overclaimed. TypeDesc measured 28.97 -> 29.22
seconds with footprint 4.190 -> 4.134 GB; dense-slot dual-write measured 29.84
seconds / 416.120 billion instructions. The latter pays for both dense and
legacy representations and is an intermediate migration state, not an
accepted optimization. The next boundary is migrating AArch64/stackmap
consumers and disabling legacy SlotInfo materialization on that supported path.
Full receipts are in
`docs/goal/evidence/2026-08-28-compiler-type-slot-canonicalization.md`.

## Update 2026-08-28 — dense slots reach zero object projection; wrapper ABI denied

The supported AArch64 path now materializes zero SlotInfo and zero AllocaInfo
objects. All slot/allocation payloads are kernel integer columns; construction
indexes are dropped after stackprep. Item311 finishes with 74 canonical
TypeDesc objects, zero legacy slot projections, 9,486 dicts and 440,166 total
reachable objects versus the original 595,927. Dense-only self-backend and
precise-stackmap gates are 390 passed and strict pcc1 Stage1 builds green.

The first execution adapter is not acceptable for performance. It routed
consumer names through cross-module scalar helpers, which looked up offset and
type separately. A caller profile put 9.09% under those helpers plus 3.84%
under raw/bucket lookup. One-slot-ID lookup reduced item311 instructions
432.747 -> 428.749 billion, but the pre-dense compiler is still 406.805
billion. Exact assembly stayed `ff943e10...`; wall from the loaded v3 run is
not used.

The wrapper ABI is `[DENIED]` as a final hot interface, not the dense table.
The next vertical slice must carry kernel/value/slot IDs into consumer-local
emit loops and read arrays directly; rebuilding SlotInfo views or repeatedly
resolving names would negate the migration. Receipts and inventory are in the
linked evidence file.

## Update 2026-08-28 — indexed call operands expose the next data-plane boundary

Destination value IDs now flow from the indexed instruction loop into ordinary
call emission. Runtime-filtered operand IDs were slower (431.240 B), aligned
Python-list facts were worse (436.583 B), and replacing the per-block list
graph with three CompilerIntArena columns recovered only to 432.756 B versus
the 406.805 B pre-dense reference. All arms emit exact assembly.

The raw attempt also found a real multi-block indexing bug: block bases were
published in the definitions-first walk before the global instruction counter
advanced, so every base was zero. The error diagnostic proved an integer call
argument was mapped to `ov.flag.15`; publication now occurs in the actual use
walk and a two-block call regression locks the offsets. Strict Stage1 v11 and
392 focused tests pass.

Generic lists, runtime locality tests, and per-argument raw getters are all
`[DENIED]` as the final call-operand interface. The next slice must batch
aligned IDs per call at the call site or remove the helper boundary entirely.

## Update 2026-08-28 — call-site batch denied; block dictionaries removed

The fixed-width call-site experiment used the confirmed `load_i64x4`
intrinsic, padded each call's local operand IDs to four-wide groups, and read
one group at the consumer.  It passed 392 focused tests, strict closure and a
source-frozen no-libpython/self Stage1, while preserving exact item311
assembly.  It nevertheless measured 31.33 seconds / 433.784 billion
instructions / 4.209 GB footprint versus 28.97 seconds / 406.805 billion /
4.190 GB for the pre-dense reference.  This call-fact use of the intrinsic is
`[DENIED]` and its source was removed.  The operand population is simply too
sparse (56,233 arguments across 46,225 calls) to amortize a padded persistent
table.

The next measured deletion replaced `IndexedFunctionKernel.block_last_uses`,
which allocated one dictionary per block, with one dense value-ID-indexed
position column.  Definition-block IDs already carry the locality proof, and
the nested dictionary now exists only behind the legacy diagnostic projector.
Item311 dictionaries fall 9,486 -> 12 and the reachable graph 440,166 ->
430,692.  The source-frozen pcc1 is green (233.68-second Stage1, libSystem
only); item311 remains exact at 30.42 seconds / 418.044 billion instructions /
4.146 GB, versus dense-v5's 30.20 seconds / 422.956 billion / 4.152 GB.

This is `[CONFIRMED]` as an end-to-end structural projection deletion with no
observed regression, not a material speedup claim.  The remaining inventory is
112,640 lists and 287,605 tuples.  Before another representation change, their
retaining owners must be counted so the next proposal removes a coherent
kind-specific graph rather than repeating the denied tagged-record or
per-scalar getter designs.

## Update 2026-08-28 — def/use facts become batch value records

Durable owner attribution identified four exact 9,475-list families in the
kernel's definitions, use offsets, use IDs and terminator uses.  They are now
two fixed-width raw record arenas plus one rare overflow arena.  A block record
holds instruction span and the terminator's zero/one use; an instruction record
holds destination, use count, first use and either second use or an encoded
overflow start.  Item311 has only 550/59,984 instructions above two uses, so the
common path consumes one four-i64 aggregate and never calls a scalar use getter.

Item311 lists fall 112,640 -> 74,743 and reachable objects 430,692 -> 392,798;
37,900 nested lists are replaced by three host-oracle lists which are empty on
the pcc1 raw-storage path.  The raw arenas close after final stack-map rendering.

The first strict build exposed a separate imported-valueclass ABI gap.  Both
sequential and parallel export fixed points now expand valueclass descriptors
after re-export convergence and reconstruct them from their true
`owning_module`; exact field/local annotations keep the kernel's aggregate
calls out of Dyn.  Three-module absolute/relative regressions, the real
18-module parallel closure, 123 focused tests and the pcc1 strict i64x4 gate are
green.  The dedicated investigation is
`cross-module-imported-valueclass-return-abi.md`.

Source-frozen Stage1 B-A-B is 306.35s candidate outlier, 264.22s control and
245.61s candidate repeat.  The repeat is CPU/cycles/footprint neutral-to-better
and instructions differ only +0.039%; this supports no regression, not a
stable speedup claim.  Matched item311 is 31.36s / 418.297B / 4.146GB control
versus 31.27s / 417.935B / 4.130GB candidate; candidate-v5 repeats at 29.84s /
417.955B / 4.130GB.  All assemblies remain exact `ff943e10...`.

This batch is `[CONFIRMED]` at the structural/representative-worker boundary.
Whole Stage2 and fixed point remain deferred until the remaining container
projections are deleted.

## Update 2026-08-28 — slot/name maps deleted; shared IDs recover wrapper tax

Indexed stackprep no longer publishes value/alloca compatibility maps or
buckets.  Dense bindings remain 17,471/898 with zero legacy records.  Raw
open-address two-i64 text indexes replace the persistent value/block bucket
lists and tuples, with exact collision checks against traced name side tables.

The naive interface was decisively wrong: nested named slot helpers regressed
item311 417.847B -> 459.459B instructions, and the native text index alone was
461.319B.  Reusing shared instruction facts recovers the loss: destination and
call operand IDs reach materialization directly; stackmap/regalloc and
terminators consume existing IDs; one cached block fact supplies global
instruction and terminator facts.  Successive workers measure 435.344B,
432.287B and 424.900B instructions, exact assembly and ~4.08GB footprint.
The measured no-libpython/self pcc1 Stage1 is 252.22s / 296.703B instructions.

Immutable/shareable phi collections then remove 9,474 more lists.  A
module-scoped operand-intern reset fixes the ordering-sensitive string identity
leak this exposed.  Current inventory is 18,984 lists, 241,585 tuples, 12
dicts and 291,021 reachable objects.  The next proposal is the actual
kind-specific instruction plane: delete the remaining per-block data/flag
lists and 209,361 payload tuples without repeating the denied generic decoder.

## Update 2026-08-28 — packed instruction metadata removes a quadratic bytearray owner

Kind-specific call, alloca, fixed-instruction and GEP facts now survive through
verifier, stackprep, stackmap, register allocation and emit without diagnostic
tuple/dataclass projection.  Current item311 inventory reports zero
instruction, call and TypeDesc projection at every stage from verification to
emission, zero SlotInfo/AllocaInfo, and 175 final reachable objects.  The parse
construction boundary still owns 9,474 `_data` lists and 209,361 payload tuples;
that remaining boundary is explicitly not claimed complete.

The first memory explanation was `[DENIED]`.  A single v12 compiler with old
projection release enabled measured 476.654B instructions / 10.749GB footprint;
retaining the old projections measured 485.568B / 10.730GB.  Identical
`ff943e10...` assembly and nearly identical memory disprove release-induced
fragmentation.  The parser-column experiment was also removed after measuring
500.918B / 10.645GB.

Out-of-process allocator reads then found 6.166GB live requested in v12 at the
same early phase where v7 held 1.095GB, despite both having about 2.1M tracked
objects.  A targeted allocator-header scan attributed 3.193GB already live to
large bytearrays.  The kernel had introduced three function-wide bytearrays
for kind, volatile and arithmetic bits.  pcc's inline bytearray append creates
and copies a new length-`n+1` object, so 59,984 appends cost `sum(1..N)` per
column: a 5.4GB three-column quadratic envelope.

The retained fix replaces those columns and the separate payload arena with
one native four-i64 metadata record per instruction: kind ID, payload ID,
volatile bit and arithmetic-flags bit.  Every hot consumer reads that complete
record once.  Source-frozen v13 pcc1 (`987f4947...`) passes strict no-libpython
closure and links only libSystem.  Repeated item311 results are 33.06s /
439.096B / 4.033GB and 33.37s / 439.253B / 4.036GB, versus v12's 37.89s /
476.802B / 10.752GB, with exact assembly in every arm.  Early live requested
falls to 1.166GB.

This proposal is `[CONFIRMED]` for the worker and structural claim.  Whole
Stage2, parse-boundary closure, fixed point and GC0..4 transfer remain open.

## Update 2026-08-28 — call-attached batch liveness replaces per-slot frozensets

Sizing overturned a dense-bitset assumption: item311 tracks 518 managed values
but live-after sets have at most two members, 519 distinct contents and only
8,997 total memberships over 59,984 slots.  The first sparse implementation
still used `instruction -> state -> span -> scalar` arena getters and repeated
the already-denied per-scalar interface.  With a similarly scalarized packed
entry state, v14 timed out its existing 360s Stage1 gate after all 630 emit
results.  A direct four-i64 record per instruction then passed correctness but
regressed item311 to 447.856B / 34.84s; both forms were removed.  v15 separately
caught an imported-valueclass method-argument ABI mismatch before emit.

The retained design attaches the state ID to the reserved fourth field of the
existing packed call span.  Empty states publish nothing; nonempty calls point
to one whole `(count, first, second-or-overflow, reserved)` state record.
`_record_kind_indexed` carries that scalar in its existing batch result, so the
consumer performs no second call-span read.  Persistent
`list[list[frozenset]]` liveness is gone; build-time dataflow sets remain.

Source-frozen v19 pcc1 (`db540512...`) links only libSystem.  Repeated item311
is 439.329B/34.24s and 439.341B/32.79s at 4.031GB, versus the paired v13
control's 439.398B/33.24s/4.033GB.  All emit exact `ff943e10...`.  This is
`[CONFIRMED]` as performance-neutral structural deletion, not a speedup.

A proposed direct packed-route cursor for removing `build_line_index` was
`[DENIED]` before pcc1 rebuild: controlled host instructions rose 95.086M ->
101.402M because it reintroduced per-block/per-route arena getters.  The code
was removed.  Lazy block-entry state construction remains pending pcc1
measurement: ordinary blocks reuse the incoming tuple without creating an
active dict, and the CFG queue uses a head index instead of `pop(0)`.

## Update 2026-08-28 — lazy entry state wins; integrated route mapping loses

The lazy block-entry candidate is `[CONFIRMED]`.  It creates an active root
dict only after a call is proven to be frame protocol and advances a list FIFO
by head index instead of shifting with `pop(0)`.  Source-frozen v20 emits exact
item311 assembly at 429.106/429.147B instructions, 3.849GB footprint and
32.13/32.38s, versus v19's 439.329/439.341B and 4.031GB.  That is a 2.33%
instruction and 4.5% footprint reduction without changing protocol semantics.

Cloudflare's freeze/contiguity lesson motivated a second line-index design:
reuse reserved call/terminator fields rather than allocate route spans.  It
required all route publication to occur only after planning, because call
fields hold liveness state during the plan.  The source-frozen v21 was correct
and its Stage1 instructions improved, but item311 regressed to 445.783B /
36.29s while saving only another 0.5% footprint.  It is `[DENIED]` and removed.
The accepted compiler sources are byte-identical to v20 in the four touched
backend modules.

## Update 2026-08-28 — parser call scratch deletes objects but loses on pcc1

The Cloudflare-style construction experiment used a function-local mutable
scratch followed by one frozen indexed translation for parser calls.  It
removed all 194,908 call payload tuple references from frozen item311 and
reduced total parsed payload tuples 211,961 -> 17,053 and parsed reachable
objects 258,157 -> 65,858.  Host instructions were neutral/slightly lower and
host footprint fell about 14%; host/internal assembly remained exact and no
diagnostic projection entered verification or emission.

This is nevertheless `[DENIED]`.  Source-frozen strict pcc1 v22
(`8953bfb2...`) built successfully and Stage1 instructions were neutral, but
item311 repeated at 496.569/496.659B instructions and 3.694GB versus the
paired v20 control's 429.113B and 3.849GB.  A 15.7% instruction regression is
not justified by a 4.0% footprint reduction.  The implementation was removed
and the three affected compiler files are byte-identical to accepted v20.

The result classifies call tuples as a parser memory owner, not the current
CPU owner.  It also reinforces the existing denial of per-record arena method
traffic inside pcc1.  The next implementation target is the measured precise
stack-map owner: construction-time uses/defs/live-in/live-out sets and the
RootGroup/location object plane on actual frame-protocol blocks.  Parser
packing should not be retried without a direct exact-slab write/freeze path
that avoids this interface cost and has a CPU-owner argument, not only an
object-count argument.

## Update 2026-08-28 — main planner reuses immutable entry root states

A fresh sizing pass rejected an empty-state fast path: 9,473 of item311's
9,474 block entries are nonempty.  The useful redundancy is representation,
not emptiness.  Those states contain 1,626,262 root-group references but only
446 distinct contents, and only 1,278 blocks execute frame enter/leave.  The
main plan was rebuilding a string-keyed dict for every block, even though only
protocol instructions can mutate it.

The retained implementation consumes the shared entry-state tuple directly,
materializes a mutable dict only at the first protocol operation in a block,
and refreshes the tuple lazily before the next record.  On item311 this avoids
8,196 dict constructions and 1,407,124 group insertions while leaving 219,138
necessary protocol-block insertions.  It adds no transition cache/table and is
therefore not the transition-replay shape denied as historical No.78.

Focused tests are 397 passed, strict closure passes and all host/pcc1 assembly
is exact `ff943e10...`.  Claim-grade v24 pcc1 (`d5cae2e7...`) links only
libSystem.  Against an adjacent v20 item311 control it improves 33.72s/33.26
CPU/429.399B/3.849GB to 31.90s/31.58 CPU/416.012B/3.617GB: 1.057x wall,
1.053x CPU, -3.12% instructions and -6.02% footprint.  Stage1 remains neutral
at 305.40s/306.756B versus v20's 302.85s/307.113B.  `[CONFIRMED]` and retained;
actual protocol-block `_RootGroup`/location/dict projection remains open.

## Update 2026-08-28 — direct aggregate loads do not rescue intermediate call scratch

Profiling the preserved failed v22 compiler localized its 67.5B-instruction
regression: kernel construction roughly doubled its sample ownership because
the parsed scalar scratch was decoded and copied into final kernel tables.
A retry replaced the scratch getter/method chain with four-lane argument
records and call-site `load_i64x4`; native aggregates were destructured before
joining the host oracle.  It again removed all 194,908 call tuples, passed 399
focused tests and strict closure, retained exact output, and was host-neutral
with lower memory.

It is still `[DENIED]`.  Source-frozen v25, against the same runtime/build
configuration as accepted v24, reached final link only around 387 seconds and
timed out at 420 seconds without a successful Stage1 artifact/receipt; v24
finishes in 305.40 seconds.  The direct branches made the compiler itself at
least 38% slower before any pcc1 worker result existed.  The candidate was
removed and compiler source is byte-identical to v24.

Do not retry an intermediate parser call arena with another accessor.  The
next viable call design has to publish/fuse the final indexed-kernel layout so
there is no second decode/copy pass.  Until that design exists, v24's 416.012B
/ 3.617GB worker is the accepted envelope.

## Update 2026-08-28 — split kernel construction phases are retained

The v22/v25 denials also showed that growing the already large
`IndexedFunctionKernel.__init__` can make pcc-compiled construction
non-linearly slower.  The existing definitions-first and uses-second walks are
now exact class methods, with their analysis imports resolved at module scope.
The split changes no ID assignment, freeze order, phi-edge position, malformed
SSA diagnostic ownership or packed payload; it creates bounded landing points
for a future parser-to-final-kernel fusion.

The 397-test focused packet remains green and host item311 is exact while
dropping from 106.136/106.289B control instructions to 103.409B and from about
218MB footprint to 192.8MB.  Source-frozen strict v26 pcc1
(`faffe09c1...`) completes Stage1 in 311.15s / 306.690B instructions and links
only libSystem, versus v24's 305.40s / 306.756B.

Two v26 pcc1 item311 runs measure 412.712B and 412.522B instructions with an
identical 3.541GB footprint, around an adjacent v24 control at 415.912B /
3.617GB.  All emit `ff943e10...`.  This is a stable 0.8% instruction and 2.1%
footprint reduction with no claimed wall/CPU speedup.  `[CONFIRMED]` as a
structural/lifetime improvement; it does not itself remove parser payload
tuples or complete the native data plane.  The accepted worker envelope is now
approximately 412.5B / 3.541GB.

## Update 2026-08-28 — parser writes the final call plane without a copy

The third call design removes the boundary that made v22/v25 fail.  The parser
writes the exact final call/argument scalar layouts, definitions and uses
resolve text fields to dense value IDs in place, and the kernel adopts the
same arenas by identity.  Construction indexes are discarded; only the
explicit diagnostic API boxes the legacy tuple.

An ordered focused packet exposed and fixed an `id()` cache lifetime error in
the new type index.  With that key retained, 351 relevant tests pass (one
legacy cold-layout test is deselected because frozen v24 fails it identically),
all three strict compiler closures pass, and item311 remains exact.  Inventory
shows all 46,225 reachable calls packed at the parse boundary, call tuple
references 194,908 -> 0, all parse tuples 211,961 -> 17,053 and parsed reachable
objects 258,157 -> 65,864.  There are 1,005 unused final call records from
blocks filtered after parsing; removing them without compaction requires an
earlier reachability phase and remains open.

Receipt-bound v29/control-v30 manifests differ only in IR/parser/kernel and
share the same current-source 186-object runtime archive.  Stage1 is neutral:
257.16s/306.429B candidate versus 260.17s/305.901B control.  Formal item311 is
413.603/413.822B and 3.357GB candidate versus 410.870B and 3.541GB control,
with wall/CPU neutral and exact `ff943e10...` assembly.  The 0.67--0.72%
instruction cost is recorded; the 5.21% footprint reduction and complete call
projection deletion justify `[CONFIRMED]` as a structural/memory migration,
not a speedup.  The next direct-publication family is fixed instructions; GEP
and alloca follow separately.

## Update 2026-08-28 — fixed direct publication is structurally right but denied on pcc1

Extending the parser-owned final plane to load/store/cast/icmp/binop/select
removed another 11,763 persistent tuples.  Item311 reached 57,988 packed
payloads out of 59,984 and only 5,290 tuples remained (GEP 4,392, alloca 898).
The six-kind identity/differential regression, 352-test packet and strict
closures were green.  Host item311 improved about 4% in instructions and
10--12% in footprint with exact assembly.

Source-frozen pcc1 denied it.  With an identical current 186-object runtime and
only IR/parser/kernel changed, v33 versus v34 control was 307.460B versus
305.407B Stage1 instructions and 312.19s versus 257.48s wall.  Item311 repeated
at 418.491/418.358B, 3.321GB and 29.93/30.00s CPU versus control 414.199B,
3.357GB and 29.65s.  Thus ~1.0% worker CPU/instructions buys only 1.1% memory;
the proposal is `[DENIED]` and removed.  Source is byte-identical to the
accepted call-plane control and the runtime is restored to checksum
`77ca361c...`.

Do not infer that GEP/alloca should receive the same smaller treatment.  The
matched caller profiles localize the regression to parsing: 974 versus 207
inclusive samples, while kernel construction improves from 1,476 to 1,319
samples.  The failed shape pays ordinary parser-plane method/intern/arena
traffic per fixed record; the later per-use branch is not the measured main
owner.  A future fixed-family attempt must assign dense IDs during one
definitions-first parse/build pass without that Python method chain; another
direct per-kind adapter has negative evidence.

## Update 2026-08-28 — entry tuple identity avoids repeated location fingerprints

No.76 leaves 9,474 entry references sharing only 854 tuple identities, yet
`add_record` rebuilt No.75's XOR fingerprint at the first safepoint of every
block.  Durable sizing measures 8,620 identity hits and 1,479,955 avoidable
group scans versus 146,307 scans on misses.

The retained fast path caches `(exact state tuple, validated locations)` by
the tuple's id, keeps the key alive and requires identity on every hit.  Misses
retain the existing XOR/content collision oracle.  The 351-test packet and
strict closure pass; host item311 falls about 4.1% in instructions and 12% in
footprint with exact assembly.

Source-frozen pcc1 uses a single current runtime and a one-file source diff.
The B-A Stage1 bracket is 324.07s/306.461B candidate outlier,
255.35s/306.396B control and 250.84s/306.145B candidate repeat.  Item311 is
411.507/411.455B candidate versus 414.191B control, with identical ~29.6s CPU,
3.357GB footprint and `ff943e10...` output.  `[CONFIRMED]` as a stable 0.65%
instruction reduction with neutral wall/CPU, not a large speedup claim.

## Update 2026-08-28 — exact entry identity also caches active offsets

The location identity cache does not cover `_planned_managed_reloads`' first
step: `active_offsets_for_version` rebuilt a set from the same shared entry
tuple once per block.  A parallel exact-identity cache now retains the tuple
and its read-only offset set; protocol-mutated tuples miss conservatively.

The 351-test packet and strict closure pass.  Host item311 improves about
3.5--3.7% in instructions, 6% CPU and 4% footprint.  Source-frozen candidate
v38/control v39 differ only in the precise-stackmap file and share one current
runtime.  Stage1 is 251.89s/306.234B/1.647GB candidate versus
249.80s/305.852B/1.678GB control.  Item311 repeats at
400.736/400.570B, 3.149GB and 28.57/28.51s CPU versus control 411.882B,
3.357GB and 29.50s.  All output is exact `ff943e10...`.  `[CONFIRMED]` as a
2.7% instruction and 6.2% footprint worker win; Stage1's 0.12% instruction
warning is recorded rather than claimed as a speedup.

## Update 2026-08-28 — numeric safepoint suffixes work only with direct limbs

`safepoint_id` remained add_record's largest child at 305 samples.  A numeric
decimal feeder removes per-record `str`/join/encode work and is bit-identical
on host.  Its first prefix handoff was nevertheless `[DENIED]`: packing the
prefix into a 64-bit Python int and splitting it under pcc1 changed every
stackmap ID (`19c2bd0a...` assembly).  Reverse FNV of the first wrong record
showed a zero/`0x8000000000000000` prefix instead of the correct limbs,
directly identifying the bignum projection boundary.

The retained form recomputes high and low separately once per function, keeps
all intermediates under 2**42 and feeds ordinal/kind digits numerically.  The
351-test packet, core/planner strict closures and host bracket pass.  With one
current runtime and a two-file source diff, v42 versus v43 Stage1 is
286.65s/306.266B/1.627GB versus 320.77s/306.327B/1.644GB.  Item311 repeats at
396.932/396.793B, 3.144GB and 29.31/29.25s CPU versus control 401.410B,
3.149GB and 29.84s.  Assembly is restored to exact `ff943e10...`.
`[CONFIRMED]`; the packed-prefix variant remains explicitly denied.

## Update 2026-08-28 — reachability now precedes instruction publication

The accepted parser used to publish instruction payloads, including calls,
before `_filter_reachable_blocks`.  Item311 therefore retained 1,005 dead-CFG
call records and 1,127 dead argument records.  Parsing is now split into a
structural phase (phi and terminator), reachability filtering, and reachable
instruction publication.  Dead instruction text is still cold-parsed for
fail-closed diagnostics, so an unsupported opcode in an unreachable block
continues to raise instead of disappearing silently.

The dedicated dead-call and dead-unsupported-op regressions, 353-test packet
and strict parser closure pass.  Durable inventory records calls
47,230 -> 46,225 and arguments 57,360 -> 56,233 with zero legacy call
projections.  Host item311 improves from 94.555B to 91.473/91.462B
instructions and from 236.5MB to 211.5/206.3MB footprint with exact output.

Source-frozen candidate v44/control v45 share one current 186-object runtime
and differ only in the parser file.  Stage1 deterministic counters are neutral
(306.791B/1.643GB candidate versus 306.802B/1.645GB control); its
310.03s-versus-267.53s wall result is not claimed as a regression or speedup.
Item311 repeats at 396.215/396.486B, 3.145GB and 29.44/29.51s CPU versus
397.598B, 3.144GB and 29.94s control, with exact `ff943e10...` assembly.
`[CONFIRMED]` as a small measured win and the phase boundary required for the
definitions-first seed; the remaining fixed-record tuples are not yet closed.

## Update 2026-08-29 — definitions-first payload closure is structural, not yet performance-accepted

All 59,984 supported item311 instructions now publish final payloads with zero
persistent payload tuples or per-block `_data` lists.  Native open-address
block/value indexes remove the construction bucket graph, and canonical-only
TypeDesc identity entries leave 74 TypeDesc objects at parsed and verified
boundaries.  Parsed lists/tuples/reachable objects fall from
27,945/58,439/117,703 to 29/10,085/39,440.

LLDB established the pcc1 correctness boundary: an empty arena's real length
was 0 while `len(arena)//4` crossed modules as 1; a cross-module seed counter
then read stale 0 after its owner advanced to 1.  A parser-local block cursor
fixes both.  The 518-test packet, strict/self-emission closures and two pcc1
canaries pass with exact output.

Performance has not converged.  Per-record use bookkeeping was denied at
446.209B.  One kernel use scan recovered 431.155B; native name indexes reached
429.991B; an exact call-plane interface repeats at 420.337/419.950B, 3.131GB
and 30.97s CPU.  Frozen controls are ~396.5--397.4B / 3.145GB, so the retained
structural migration is still ~6% instruction-negative.  Full-lifetime
profiles put the excess at prepare (+1,169 samples) and final emit (+892), not
the 258-sample indexed use scan.

Removed `[DENIED]` shapes: payload-only construction (430.5B), base final-mode
publication (435.0B), optional cross-module arena fields (call canary
failures), and exact arenas threaded through every parser frame (Stage1
exceeded 420s with no artifact).  Current source retains the v57 exact call
interface, native name indexes, and canonical-only TypeDesc keys.  The last
TypeDesc refinement has host evidence only; terminator/phi closure, the pcc1
regression, whole Stage2 and GC0..4 fixed points remain open.

## Update 2026-08-29 — construct-then-handoff terminator/PHI arenas are denied

Candidate v61 closes the pending current-source pcc1 receipt: exact item311
assembly at 419.498B instructions / 3.132GB footprint.  It remains about 5.9%
instruction-negative versus frozen v44.

A subsequent parser-owned terminator/PHI arena eliminated the kernel's second
encoding pass and let AArch64 release valid scalar projections before verify.
Inventory moved parsed/verified tuples 10,085 -> 77 and parsed reachable
objects 39,440 -> 19,167 with exact output.  The source-frozen v62 Stage1 was
280.73s / 310.438B / 1.664GB, and pcc1 item311 was exact but slower at
423.326B instructions / 3.131GB / 31.66s CPU.

`[DENIED]`: the shape still allocated every ordinary terminator/PHI object and
then paid a new cross-module publication interface, so representation
retention improved while execution regressed 0.91% versus v61.  It was removed
and the four affected compiler files were hash-verified equal to the frozen
v61 snapshot.  Do not retry a seed handoff over already-materialized objects;
the next proposal must parse directly into final IDs/spans or leave a named
cold diagnostic projection.

## Update 2026-08-29 — authoritative metadata keeps only a kind mirror

Block arenas duplicated function-wide kind/volatile/arithmetic-flag metadata.
Deleting all three columns was host-negative because stackprep lost its cheap
kind-byte traversal.  The retained v63 keeps kind only and lazily projects the
two cold flag fields.  Source-frozen item311 repeats at 418.638/418.559B
instructions and 3.111GB with exact output, versus v61 419.498B/3.132GB;
Stage1 is 310.152B/1.657GB.  `[CONFIRMED]` as a small representation and
instruction win, not a material speedup.

The next attempt prepublished destination facts.  Direct parser access was
pcc1-invalid: v64 failed a 114-byte emit canary at `prepare begin` with
`append4`, despite host and closure green.  An exact seed method fixed the
boundary, but v65 measured 419.236B/3.107GB item311 and 310.209B Stage1,
worse in instructions than v63.  `[DENIED]`; both variants were removed and
the retained compiler sources hash-match frozen v63.  Do not infer that a
host-safe arena attribute is a pcc1-safe typed projection, and do not retry
destination prepublication without eliminating the method cost itself.

The last no-extra-method form bound the facts arena to an exact local only in
`_parse_blocks`, leaving call-parser signatures untouched.  V66 Stage1 was
309.954B/1.662GB, but its 114-byte single-function emit canary segfaulted at
`prepare begin` (exit 139), before any item311 measurement.  `[DENIED]` and
removed.  Direct property, exact-method and exact-local variants are now all
exhausted; destination prepublication is closed unless new compiler-level ABI
evidence changes this boundary.

## Update 2026-08-29 — v67 retained and remaining parser adapter routed to ABI work

Call-specific facts/metadata/kind publication is now inlined inside the exact
seed override.  V67 is exact and repeats at 418.176/418.108B instructions and
3.111GB; Stage1 is 310.085B/1.660GB.  `[CONFIRMED]` as a 0.1% simplification.

An early-attached full profile has 28,744 samples.  Versus v44, final emit is
faster (16,006 vs 16,700) and verifier is faster (3,363 vs 5,554); all remaining
excess is parse/prepare (8,922 vs 5,278 parser samples).  The long
`append_parsed_call` native adapter owns 943 samples.

Three interface remedies are `[DENIED]`: split base/final publication v68
regressed to 434.749B; pointer-bearing valueclass publication v69 made Stage1
314.155B and item311 418.290B; exact seed receiver v70/v71 improved Stage1 but
both fail a 114-byte canary at `append4`, even after exact slot annotations.
The source is restored to frozen v67.  Generic exact-field/method ABI work is
routed to `pcc1-exact-compiler-arena-field-method-abi.md`; do not retry another
backend-local adapter shape.

## Update 2026-08-29 — generic exact ABI repair makes v73 the accepted worker source

The routed prerequisite is resolved generically.  Closed-world exports now
publish inherited-first field schemas after re-export convergence; early
extern declarations preserve their base graph; direct method dispatch rejects
known subclass overrides; and the PEP 604 optional forms `T | None` / `None |
T` retain the same object projection already supported for `Optional[T]`.
There is no backend-module or arena-name special case.

Source-frozen v73 links only libSystem, passes the former 114-byte `append4`
canary, and its full-closure `_call_instr_from_parts` branches directly to
`IndexedFunctionSeed.append_parsed_call`.  Two exact item311 repetitions are
384.116B/384.247B instructions, 28.38/28.39s CPU and 3.105GB footprint with
unchanged `ff943e10...` assembly.  This removes about 8.1% versus v67 and is
about 3% below the pre-migration v44 instruction envelope while retaining the
structural native data plane.

V73 is therefore the accepted worker source for the next value-record slice.
This does not close terminator/PHI/ParsedBlock projection, whole Stage2,
fixed-point or GC1..4 work.  Full evidence is
[`2026-08-29-exact-compiler-arena-field-abi.md`](../goal/evidence/2026-08-29-exact-compiler-arena-field-abi.md).

## Update 2026-08-29 — direct terminator parsing awaits pcc1 verdict

Fresh v73 caller attribution records 9,662 on-CPU samples: parse owns about
65.3%, verify about 32.5%, call parsing about 32.9%, fixed hot parsing about
9.9%, and `_parse_block_structure` only about 3.9%.  This bounds terminator/PHI
work as a structural closure slice, not a promised large speedup.

The current proposal does not repeat v62's construct-then-handoff.  Reachability
reads successor spellings directly from the existing raw terminator text; each
live terminator is then parsed once into the seed's final kind/type/value/
target/case scalar arenas, which the kernel adopts by identity.  ParsedInstr
terminators exist only for dead fail-closed parsing and explicit legacy/x86
diagnostic projection.  Known and missing target spellings remain recoverable
from the indexed plane.

Item311 inventory moves parsed/verified reachable objects from 39,440/39,460
to 20,496/20,512 and tuples from 10,085 to 611.  All 9,474
`ParsedInstr.data` terminator tuples are gone; the remaining 611 tuples are
267 PHI incoming tuples, 267 per-block PHI tuples, 74 canonical type identity
entries and three module/global tuples.  Assembly remains exact in the
inventory and normal-path terminator diagnostic projections are zero.

The focused packet is 549 passed with one explicitly deselected stale control;
that control fails identically on frozen v73 because it still inspects the
retired `func.aarch64_block_layout` instead of authoritative
`kernel.block_layout_ids`.  Six current strict closures pass; x86 standalone
closure hits the same unrelated `lane` for-target join error on frozen v73.
No pcc1 performance verdict exists yet, so the proposal remains pending.

## Update 2026-08-29 — direct terminator parsing is denied on pcc1

Source-frozen v74 completes Stage1 and passes the 114-byte canary, but the
execution result denies the proposal.  Stage1 is 267.60s / 309.859B
instructions / 1.687GB footprint versus v73 299.28s / 308.470B / 1.665GB;
the mixed wall/CPU signal does not erase the 0.45% deterministic instruction
regression.

More importantly, repeated pcc1 item311 is exact `ff943e10...` but measures:

```text
v73 accepted     384.116 / 384.247B   28.38 / 28.39s CPU   3.105GB
v74 direct term  393.849 / 393.704B   30.81 / 29.57s CPU   3.158GB
```

The repeat confirms about 2.46% more instructions and 1.7% more footprint.
Deleting 9,474 terminator objects and reducing parsed tuples 10,085 -> 611 is
not worth that regression.  The raw reachability scan plus direct parser-to-
seed scalar publication remains an execution interface cost even after the
exact-field ABI repair.  `[DENIED]`; restore accepted v73 narrowly.  Do not
retry terminators or PHIs with another parser publication interface.  They
remain construction-only compatibility projections unless a future fused
block parser can eliminate work rather than moving it.

The candidate was removed with narrow patches.  All seven affected compiler
files hash-match the accepted v73 source snapshot; the restored focused packet
is 547 passed with the same one frozen-v73 stale control deselected.

## Update 2026-08-29 — early compatibility-projection release awaits pcc1

The next object-closure proposal adds no parser record or publication
interface.  After `_build_cfg` has consumed exact missing-target diagnostics,
the verifier moves the existing scalar-PHI and terminator release calls earlier
from stackprep.  Verifier, stackprep, stack maps, regalloc and AArch64 emit then
retain only the already-authoritative kernel plane.  X86 remains an explicit
legacy target and lazily projects PHIs/terminators at its emitter boundary.

Item311 construction remains honest at parsed: 39,440 reachable objects and
10,085 tuples.  Immediately after verify it now has 19,177 objects and 77
tuples instead of 39,460/10,085; the 77 are 74 canonical type identity entries
plus module/global tuples.  Stack-prepared remains 152 objects / one tuple.
Normal AArch64 diagnostic counters stay zero and inventory assembly is exact.

The focused packet is 548 passed with the same one frozen-v73 stale control
deselected.  A source-frozen pcc1 receipt and repeated item311 comparison are
still required before retention.

Source-frozen v75 closes that boundary.  Stage1 is 295.58s / 310.025B
instructions / 1.651GB footprint; versus v73 its deterministic instruction
count is 0.50% higher while CPU and footprint are lower, so no Stage1 speedup
is claimed.  Repeated item311 is exact at 384.141B/384.113B instructions,
27.82/28.09s CPU and 3.105GB, indistinguishable in instructions/footprint from
v73.  `[CONFIRMED]` as a zero-extra-work lifetime/structure improvement: the
same release loops execute earlier, no publication interface is added, and the
verified object graph loses 20,283 compatibility objects without worker
regression.  V75 becomes the accepted source for final object-closure/GC0
transfer.

## Update 2026-08-29 — verifier/stackprep block projection closure awaits pcc1

The final supported AArch64 consumer migration replaces stackprep's last
`CompactParsedInstrArena` kind/count traversal with existing block facts and
instruction metadata.  Verifier diagnostics now take canonical block names
from the kernel; after successful CFG validation, unknown/AArch64 workers can
release `ParsedBlock` and instruction-arena projections before stackprep.
Explicit x86 keeps lazy PHI/terminator object projection at its emitter seam.

Item311 inventory remains honest at parsed (39,440 objects / 10,085 tuples) but
drops to 227 objects / 77 tuples immediately after verify, versus v75's
19,177/77.  The 77 tuples are 74 canonical type-identity pairs plus three
module/global tuples; no instruction, block, terminator, PHI, slot, liveness,
reload or register record object remains.  Stack-prepared is 152 objects / one
tuple, normal projection counters are zero, and inventory assembly is exact.

The focused packet is 548 passed with the same one frozen-v73 stale control
deselected; four changed-module strict closures pass.  Source-frozen pcc1,
item311 and GC0 whole-stage/fixed-point evidence remain required.

Source-frozen v76 denies the stronger early block/arena release.  Although its
inventory reaches 227 objects immediately after verify, item311 repeats at
386.615B/386.247B instructions and 3.117GB versus v75/v73 about 384.1B and
3.105GB.  The 0.55-0.65% instruction plus 0.4% footprint regression is real;
`[DENIED]`.  The changed stackprep path replaced v63's deliberately retained
direct `_kind_ids[index]` mirror with nested block-fact/metadata method calls.
An early caller profile sees that interface in the stackprep path and provides
no evidence for accepting the regression merely to shorten object lifetime.

V77 therefore restores verifier/kernel byte-for-byte to accepted v75 and makes
one narrower consumer change: stackprep obtains each block's existing compact
instruction arena from `kernel.instruction_arenas` rather than traversing
`func.blocks`, while keeping the proven direct kind mirror.  ParsedBlock is no
longer a stackprep consumer; compatibility blocks remain live only until the
unchanged end-of-stackprep release.  The focused packet is 548 passed and the
changed stackprep strict closure passes.  Pcc1 evidence remains pending.

Source-frozen v77 still regresses: item311 repeats at 386.540B/386.537B
instructions versus v75 about 384.1B, although footprint returns to 3.105GB.
The nested metadata lookup was not the full mechanism.  Inspection shows the
new per-block `kernel.instruction_arenas` access itself lacked a class-level
field type, so pcc1 could not use the repaired exact-field ABI for those 9,474
lookups.  V77 is `[DENIED]` as built.  V78 adds only
`instruction_arenas: list[CompactParsedInstrArena]` to the kernel schema; host
focused tests and strict kernel/stackprep closures pass.  Pcc1 evidence is
required before deciding whether that exact schema rescues the consumer path.

## Update 2026-08-29 — V78 exposes an identity-unsafe hoist cache at fixed point

V78 itself passes the worker boundary.  Source-frozen Stage1 is 260.65s /
308.925B instructions / 1.648GB footprint; repeated item311 is exact at
384.062B and 383.841B instructions with a stable 3.105GB footprint.  The
class-level `instruction_arenas` schema therefore recovers V77's dynamic-field
tax without changing assembly, and the object inventory reaches 19,177
objects / 77 tuples after verification and 152 objects / one tuple after
stackprep.

Its first sequential GC0 transfer did not reach a fixed point.  Stage2
completed in 700.125s and Stage3 in 293.958s, but raw pcc2/pcc3 comparison
reported 65,158,973 differing bytes.  Section classification reduced the
apparent binary-wide drift to one module: Stage3's stackprep `__text` is 1,840
bytes smaller and its stack maps are 2,720 bytes smaller.  The only cache miss
among 508 Stage3 native objects was
`pcc.backend.self_backend_stackprep`.  Its Stage2 assembly called nested
`alloc`/`alloc_value_slot` with 6/10 parameters; Stage3 used the correct 3/8
capture ABI.

The same frozen source reproduces in under one second with `--emit-llvm`:
V78 pcc1 reports 28 free-name analyses / 10 hits and emits the 6/10 signatures,
while V78 pcc2 reports 27 / 9 and emits 3/8.  `compute_free_names` and five
sibling/local hoist caches keyed entries by a string containing `id(fd)` but
stored only the result.  They did not retain the AST owner or validate identity
on a hit.  A forced-ID-collision regression observed the exact stale-capture
failure before the repair: a second function reading `right_capture` received
the first function's cached `left_capture` set.

V79 stores `(result, exact AST owner)` in all six caches and accepts a hit only
when the owner is identical.  It changes no free-name algorithm or backend
rule.  The two collision regressions pass; the complete nested-hoist file is
7/7 and the compiled closure/module-boundary packet is 5/5.  The repaired host
and source-frozen pcc1 both converge to pcc2's 27/9 profile and 3/8 signatures;
`pcc_ir_diff.py` reports no structural difference for stackprep.

Source-frozen V79 Stage1 is 304.15s / 309.666B instructions / 1.683GB footprint,
links only libSystem, and has SHA `f4c7dc7d...`.  The 114-byte canary exits 0.
Repeated item311 is exact `ff943e10...` at 384.016B/384.018B instructions,
26.92/26.94s CPU and 3.105GB footprint.  `[CONFIRMED]` at the hoist-correctness,
Stage1 and representative-worker boundaries.  The final source-frozen GC0
Stage2/Stage3 comparison remains pending and is required before V78/V79's
object-projection closure can be accepted.

## Update 2026-08-29 — V79 closes the object-projection and GC0 fixed-point boundary

The pending V79 transfer is now `[CONFIRMED]`.  A cache-off Stage2 completes in
690.571s; a second cache-off process-tree run completes in 683.796s with 638
synchronized samples, 27 processes at peak, and 10.857GB peak aggregate RSS.
That is about 756MB / 6.5% below the prior complete 11.613GB process-tree
Stage2 baseline.  Both runs produce pcc2 SHA `1c62b168...`, and the resource
run is byte-identical to the fixed-point artifact.

The first Stage3 attempt correctly remains failed evidence: its 600-second
watchdog expired after all 508 cold objects were atomically published but
before a pcc3/result existed.  It left no compiler children.  With the same
source/compiler/cache namespace now warm, the ordinary retry completes in
211.379s with 508 hits / zero misses.  Pcc2 and pcc3 are raw byte-identical at
SHA `1c62b168...`; both link only libSystem.

Fresh V79 inventory again records all 59,984 instruction payloads packed,
zero normal instruction/call/type/slot projections, and parsed/verified/
stackprep/stackmap graphs of 39,440/19,177/152/176 objects.  Parser terminator
and PHI objects are the explicit construction compatibility seam; they are
released after CFG diagnostics and absent from downstream supported consumers.
The outer arena/name tables are classified indexes or traced spelling side
tables, not per-record object projections.  The inventory/source-shape ratchet
passes 5/5.

This closes `PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE` at its finite
GC0/AArch64 representation boundary.  It does not claim GC1--4, provenance or
parallel emit, and it does not claim the global ratio: cache-off Stage2
683.796s is still about 2.25x V79 Stage1 304.15s.  Full receipts and claim
limits are in
[`2026-08-29-native-data-plane-object-projection-closure.md`](../goal/evidence/2026-08-29-native-data-plane-object-projection-closure.md).

## Update 2026-08-29 — parallel/provenance baseline and first rejection lines

The dependent parallel/provenance row starts from source-frozen V79, not from
the retired pre-reload or pre-data-plane profiles:

```text
Stage1                         304.15s / 309.666B instructions
cache-off Stage2              683.796s / 10.857GB peak process-tree RSS
Stage2 frontend codegen       123.448s
Stage2 native emit            398.806s
  oversized                    44.400s / 7 objects
  safe                        341.445s / 501 objects / 501 fresh processes
owned link driver              67.818s
```

Two tempting proposals are rejected by sizing before source work.  Restoring
batch4 can eliminate at most 342 process launches; one V79 pcc1 launch plus a
114-byte full emit takes 0.10s, so even assigning all of it to startup gives
only about 4.3s of eight-way wall.  The historical same-shape measurement also
found neutral wall and approximately doubled RSS.  Do not revive batch4 as the
Stage2 solution without a new mechanism beyond startup amortization.

The current early/late V73/V79-compatible item311 profiles attribute
9.48--9.67% to the complete granule predicate but only 0.30--0.77% to the old
managed-pointer find-slot leaf.  Exact raw `CompilerIntArena` loads/stores
already use the unsafe/raw projection and do not ask object provenance.  The
remaining predicate callers are ordinary dict/regex/list/object operations;
globally replacing them with a managed-header read would repeat the unsafe
`py_incref_managed` design denied earlier.  Even impossible deletion of the
whole 9.7% has only a 1.107x worker ceiling.  Static provenance still requires
its exact-type differentials, but it cannot be represented as the 380-second
complete-stage answer.

The first no-source-change discriminator is medium safe-lane width: replay 32
frozen medium inputs through the same V79 pcc1 at width 8 and 12, with one
fresh process per item, exact assembly comparison and synchronized process-tree
RSS.  Pre-registered acceptance for a source candidate: width 12 must improve
wall by at least 1.25x, keep aggregate CPU/instructions non-regressed, and keep
peak tree RSS below 18GB / 1.6x the width-8 arm.  A first pair below 1.15x or
above the memory limit denies widening before a Stage1 rebuild.  If it passes,
repeat in reverse order before changing the scheduler.

### Width-12 result `[DENIED]`

The first pair is decisive and the early-stop line applies.  Both arms used
the same V79 pcc1, the same first 32 frozen medium inputs, one fresh process per
item, exact item ordering and output checks; only concurrency changed.  All 32
assembly hashes match item by item.

```text
metric                         width 8             width 12       C / B
rank-harness wall              29.691s             25.849s        0.8706 (1.149x)
sampled outer wall             30.737s             26.948s        0.8767 (1.141x)
aggregate CPU                 198.12s             223.64s         1.1288
instructions                  2.69676T            2.69825T        1.00055
cycles                        642.01B             689.83B         1.0745
peak process-tree RSS          5.000GB              6.017GB        1.2035
```

Wall misses the 1.15 early line, and the candidate buys that wall by burning
12.9% more CPU and 7.4% more cycles.  It also fails the pre-registered
non-regressed-compute requirement.  Width 10/11 cannot supply the missing
complete-stage factor and is not tested.  No scheduler source changes.

The zoomed-out unsplit hypothesis is also denied by sizing.  The complete
frozen 464-shard manifest totals 387,352,276 IR bytes, versus 393,209,372 bytes
before current splitting; self-contained shards do not duplicate a second
full IR corpus.  Disabling splitting would mainly save the same few seconds of
worker startup already bounded above while reviving the historical giant-
worker footprint.  No unsplit stage is authorized.

This leaves an architectural fact rather than another scheduler knob: V79
Stage2 retires 3,345s of aggregate CPU versus Stage1's 1,067s.  Twelve-core
parallelism cannot robustly turn that into a 304s wall while current workers
run at less than one core each and the link has serial work.  The remaining
ratio requires deleting generic pcc object operations in parser/verification/
stackmap/emission or materially improving their generated code; concurrency
alone is exhausted at its current resource boundary.

## Update 2026-08-29 — object closure retracted: transient verifier definitions

The stage-end inventory was necessary but insufficient.  It counts reachable
objects after verification and therefore reported zero retained def-use record
projection, while `_build_definitions` created and discarded one frozen
`_Definition(name, type_id, block_index, position)` per value inside the
verifier.  Item311 has 18,444 values.  The records are inserted into
`dict[int, list[_Definition]]`; every operand use hashes its spelling, walks a
bucket list and compares strings.  Current caller attribution puts verifier at
32.5% of the early worker and `_verify_ordinary_uses` at about 22%.

The kernel already owns the corresponding facts: `value_id`, definition block
and type in `value_header`, packed instruction/PHI/terminator use IDs, and the
value-name index for cold diagnostic spellings.  Only duplicate-seen and
definition position are absent from the dense verifier boundary.

### Proposal — dense verifier definition-position plane `[pending]`

Replace `_Definition` plus its bucket dict/list with one temporary
`CompilerIntArena` holding `(seen, position)` per value.  Definition type and
block come from `kernel.value_header(value_id)`.  Indexed call/fixed/GEP/use/
PHI/terminator verification consumes value refs directly; unsupported/cold
spelling paths resolve through `kernel.value_id` and preserve the same
diagnostics.  Close the temporary arena on success and failure.  Do not alter
dominance, pointer-type equivalence, duplicate definition, undefined value or
PHI-edge rules.

A source-shape ratchet was observed red before implementation.  Acceptance
requires all verifier malformed-IR/differential tests, strict closure, exact
item311 assembly, and a source-frozen pcc1 item311 comparison.  Require at
least 1.05x CPU/instruction improvement or a material footprint reduction with
no wall/CPU regression; otherwise remove the new plane and retain the honest
open boundary.  No Stage2 rebuild before the representative-worker verdict.

### V80 result `[DENIED]`; eager diagnostic spelling identified

V80 removed `_Definition` and its bucket graph, passed 19 verifier/inventory
tests, strict closure, the 114-byte canary and exact item311 output.  Stage1
retired 308.748B instructions and used 1.470GB peak footprint versus V79
309.666B / 1.683GB.  Item311 was exact but missed the retention line:

```text
V79 adjacent control   32.52s / 31.03 CPU / 384.463B / 3.105GB
V80 run 1              29.48s / 29.23 CPU / 376.940B / 3.085GB
V80 run 2              33.30s / 31.86 CPU / 377.485B / 3.085GB
```

Instructions improve only about 1.8%; footprint only 0.6%; the repeat is
slower in wall and CPU.  V80 was removed by an `apply_patch` forward restore;
the verifier is byte-identical to frozen V79 and its 18-test packet is green.

The V80 caller profile supplies new mechanism evidence rather than permission
to accept the old candidate.  Verifier inclusive falls 32.50% -> 28.33% and
`_verify_ordinary_uses` 22.05% -> 17.59%; raw arena get/set is only 0.04%.
The remaining new hot path is `_require_local_type_ref`, which unconditionally
recovers `kernel.value_name(value_id)` before knowing whether a diagnostic is
needed.  Its descendants include granule provenance, `_strs_eq`, type-object
checks and managed-object traffic.

### V81 proposal — lazy diagnostic spelling on the dense definition plane `[pending]`

Reapply the V80 dense table but recover a value spelling only inside the
undefined/type-mismatch branches.  A successful indexed operand verification
then consumes only value ID, packed definition/type facts and integer compares;
cold/unsupported string inputs retain the old path and exact messages.  The
same correctness/output gates and 1.05x representative-worker line apply.

## Update 2026-08-29 — shared definition facts win; local and bit planes denied

V81 delayed diagnostic spelling but still rebuilt a verifier-local arena. Its
adjacent result was 380.837B instructions / 33.04 CPU / 36.52 wall / 3.085GB
versus V79 385.292B / 31.58 / 32.39 / 3.105GB. `[DENIED]` and removed.

V84 instead publishes definition position and first-duplicate ID with the
kernel's existing definition block/type facts. The verifier reads that shared
record and constructs no pass-local table. Source-frozen Stage1 is
309.248B instructions / 1.653GB and links only libSystem. Item311 A-B-A is
376.880B / 376.963B versus adjacent V79 384.031B, with all CPU/wall/footprint
signals non-regressed and exact assembly. `[CONFIRMED]` as a roughly 1.9% small
win plus shared-analysis closure, not a 1.05x claim.

The next root-state experiment exposed a generic raw-int constructor bug:
valueclass fields were extracted normally except when used as ordinary class
constructor arguments, where classgen forced `py_obj_getattr` on the aggregate.
A checked-in reduction reproduces the self-verifier failure. Classgen now uses
normal expression lowering for a named receiver whose recorded storage IR type
is non-pointer; object receivers retain dynamic lookup. Source-frozen V85
crosses the boundary and completes Stage1.

V85's packed provenance/liveness plane is nevertheless `[DENIED]` on
performance: 429.775B instructions / 30.93 CPU / 31.05 wall / 3.110GB versus
adjacent V84 377.190B / 27.50 / 27.54 / 3.085GB. Per-word raw arena calls
regress instructions by 13.94%. The precise-stackmap source was restored
byte-identical to frozen V79. An immediate current-owner flamegraph puts the
whole stack-map plan at 36.1%, but origins+liveness at only 2.2%; the real
sub-owners are `_block_entry_states` (8.2%) and `add_record` (5.0%). Future
work must batch/fuse the root-state transition/location/record plane and must
not retry the per-scalar bit matrix. Full receipts are in
`docs/goal/evidence/2026-08-29-shared-definition-and-stackmap-denials.md`.

## Update 2026-08-29 — V104 closes worker projection but exposes coordinator growth

The subsequent batched native-plane source now fuses the block plane, packs
root states and liveness, uses one monotonic safepoint suffix cursor, dispatches
the supported AArch64 packed emitter statically, and batches raw liveness row
operations.  Source-frozen V104 pcc1 is
`8c6abbd28bd13d9789996169886f1f5547e1d203425a0caf18fc7c7f3df7a559`.
Repeated item311 output remains `ff943e10...`; instructions fall to about
292.7B and footprint to about 1.725GB.  The exact current Stage2 shard for
`method_call_expression_lowering` likewise emits the same assembly under V79
and V104; V104 retires 483.25B versus 601.40B instructions and uses 2.751GB
versus 4.395GB footprint.  This confirms the worker data plane rather than a
representative-input illusion.

Future growth is now guarded mechanically rather than by a hand-maintained
inventory whitelist.  `scripts/pcc_record_inventory.py` AST-discovers every
top-level `self_backend*.py` class and requires one fail-closed classification;
every concrete class remains visible to stage graphs.  It also count-registers
every direct diagnostic-record constructor site by enclosing owner and policy.
A regression proves both an unclassified `FutureHotRecord` and a new direct
`ParsedInstr` construction are rejected.  Current item311 reports 44/44
classes classified, 35/35 constructor sites owned, all five graph stages
present, and zero normal call/instruction/type/block/arena/phi/terminator
projection across 59,984 instructions and 20,004 stack-map records.  Receipt:
`build/native-data-plane-v104-current-inventory-v2.json`, SHA-256
`733cd913c35f431be2e5059cbad457d43d4741e906f5fb30e190cefbf4a2f4ff`.

The claim-grade GC0 Stage2 did not complete.  With the exact V79 cache-off
shape (10 frontend jobs, 8 emit/link jobs, 800-second watchdog), V104 timed out
at 800.47 seconds, produced no pcc2 or stage result, and left no child after
process-group cleanup.  Peak process-tree RSS was 23,018,766,336 bytes versus
V79's 10,856,660,992.  The root pcc1 coordinator lived for the whole run and
peaked at 11,845,746,688 bytes; V79's root peaked at 6,089,441,280.  All seven
V104 oversized emit inputs were then replayed serially: their maximum isolated
footprint was only 2.751GB.  Therefore the doubled stage peak is not a hidden
native worker regression; the coordinator retains its frontend/export/IR
universe while correct lower-footprint workers execute.

The pipeline and scheduler files are byte-identical between V79 and V104.
V104's final emit-input corpus is about 436.1MB versus V79's 387.4MB, and the
self-backend source family grew about 11%, neither of which linearly explains
the 94.5% coordinator peak increase.  Growth exposed a non-linear ownership
seam: parallel frontend worker `.ll` files are all read into
`module_ir_by_index`, returned as all-resident strings, copied into another
list, then written back to temporary `.ll` files before native workers consume
them.  Investigation Update No.53 already denies a new structured IR sidecar
because frontend instructions are text records; it does not deny canonical
file-path handoff, which Update No.41 explicitly left open.

### Proposal — canonical file-backed IR ownership handoff `[pending]`

Reopen `PERF-P1-STAGE2-COORDINATOR-IR-STREAMING` as a prerequisite.  Keep the
canonical LLVM text and every verifier/cache/fallback semantic check; change
only its owner.  While the frontend worker temp lifetime is active, hand
receipt-bound ordered `.ll` paths directly to the self-backend split/emit
pipeline instead of reading every file into a pcc string and writing it back.
The old in-memory adapter remains for `--emit-llvm`, LLVM backends, IR-pass or
target-triple rewrites, cache-artifact decoding and unsupported paths.  First
prove exact ordered path/text parity, diagnostics and output on focused
multi-module plus split-worker gates; then build one source-frozen pcc1 and
repeat the sampled GC0 Stage2.  Acceptance requires a complete pcc2, identical
output, coordinator peak at most 8GB and no more than 5% wall regression.
Do not claim the full 11.8GB is IR text before this controlled result.

### V106 focused result `[CONFIRMED; whole Stage2 pending]`

The canonical path handoff now crosses the real pcc1 boundary.  V106 pcc1
`b746d84e...` builds in 279.24s and links only libSystem.  A two-module package
profile records `multi_ir_file_handoff=1` and
`link_self_backend_ir_paths`; its executable prints `42`.  Forcing the legacy
adapter on the same pcc1 records `emit_ll_many` plus `link_self_read_ll` and no
handoff counter.  The two executables are byte-identical at `5ccd4923...`.
The handoff path also retains streaming `py_cpy_*` rejection, module order,
temp-lifetime cleanup and exact split/cache worker inputs in focused tests.

V106 item311 remains exact `ff943e10...` at 292.688B instructions and 1.725GB
footprint.  This clears the focused and compiled-stage boundary, not the row:
the required sampled GC0 Stage2 was withheld when launch preflight found
34.3/35.8GB swap used after consecutive builds.  No timeout was widened and no
pcc2/fixed-point claim exists yet.

### V106-V109 whole-boundary result `[DENIED; removed]`

The focused path result did not survive the complete owner boundary.  The
source-frozen candidate sequence built five pcc1 binaries; this is the complete
Stage1 spend for this line, not five performance samples of one candidate:

```text
candidate   wall       instructions   purpose
V105        318.73 s   315.890B       first canonical path handoff
V106        279.24 s   313.570B       resolve runtime before frontend
V107        243.37 s   315.145B       use worker-owned fallback metadata
V108        245.78 s   314.573B       explicit post-frontend collection seam
V109        386.47 s   379.796B       lazy AST decode experiment
total      1473.59 s                  24.56 minutes
```

V106 was the only candidate allowed to reach the full 800-second watchdog.  It
timed out at 800.411 seconds with no pcc2.  Its 7.865GB tree peak and 3.405GB
largest-process peak looked promising, but source inspection found that the
coordinator was spending roughly three minutes scanning fallback calls one
line at a time before normal emission.  Moving that already-owned metadata to
the worker removed the accidental delay and exposed the retained live set:

```text
candidate   elapsed/status       tree peak      largest process   pcc2
V106        800.411 s TIMEOUT      7.865 GB       3.405 GB          absent
V107        179.870 s INTERRUPT   22.704 GB      14.861 GB          absent
V108        169.496 s INTERRUPT   16.144 GB      14.847 GB          absent
V109        interrupted           no final receipt                  absent
```

V107 and V108 were stopped by the registered memory threshold rather than
allowed to repeat a known 800-second failure.  Explicit collection did not
remove the underlying owner.  V109 tried decoding one AST at a time for the
closed-world virtual-thread fixed point, but it reloaded and rescanned modules
across iterations.  It regressed Stage1 instructions by 20.7% and wall by
57.2% versus V108, enough to deny it before any Stage2 claim.  The V109 sampler
was interrupted during a double-SIGINT cleanup race and left a `RUNNING`
partial JSON without samples; that artifact is not performance evidence.

The sampler now installs its interrupt flag handler before launching the child
and ignores repeated interrupts while terminating the whole process group; its
double-SIGINT regression passes.  The canonical path handoff, worker-metadata,
collection and lazy-AST production changes were all removed with forward
patches.  Focused retained-path gates pass 8/8.  V104's fused/packed worker data
plane, fail-closed record inventory and all failed receipts remain intact.

The result narrows the real boundary: `.ll` readback/writeback is not the
dominant retained owner.  The coordinator still materializes the complete
decoded AST/export universe for closed-world semantic joins before codegen.
The next proposal must define a compact worker-published semantic summary
(dense function/method IDs plus effect/call edges) and run the fixed point on
that summary exactly once.  Repeated AST decode, a second file-path adapter and
another Stage1 build without a cheap summary-equivalence proof are denied.

### Dense semantic-summary plane `[DENIED; removed]`

The next candidate tested that proposal without stacking another file-handoff
adapter.  Each AST wire was decoded exactly once; callable/effect seeds and
edges were interned to dense IDs; reverse CSR plus an arena worklist ran one
global fixed point.  String/dict tables existed only during ID construction
and publication.  The production path retained no decoded-module list.

Cheap proof was nontrivial and completed before the one allowed build:

```text
retained Stage2 corpus             216 modules / 237,690,536 wire bytes
largest wire module                pcc.cli_bootstrap / 15,048,245 bytes
summary graph                      4,765 nodes / 7,605 edges
wire loads                         216 (exactly one per module)
eager-versus-summary exports       complete dict equality
host decoder RSS sizing            513.3MB eager-all / 161.7MB one-at-a-time
focused retained/candidate gates   33 passed
strict 3-module native canary      0 py_cpy calls / 0 strict stubs / output 1
```

A source-frozen Stage1 then succeeded rather than repeating V109's source-size
regression:

```text
pcc1 SHA-256          04c91c6d9d8196832c35ebd64fd70605d88dbe1731a02ce63cb549ea52994b68
wall                  298.13 s
instructions          281.911B
CPU                   1171.26 s
peak footprint        1,205,487,104 B
linkage                libSystem only
```

That pcc1 compiled and ran the two-module coordinator canary in 4.2 seconds,
printing `42` and recording `ast_summary_load_count=2`, node count 2 and edge
count 1.  The full-boundary failure therefore is not dead candidate code or a
host-only result.

The one allowed sampled GC0 Stage2 launched with 75% physical memory free,
no lock/process owner, caches off and the established 10/8/8 worker shape.  It
was interrupted as soon as the root crossed the registered 8GB limit:

```text
status / elapsed              INTERRUPTED / 124.069 s
root pcc1 maximum             14,666,301,440 B at 81.757 s
process-tree maximum          25,454,723,072 B
pcc2                          absent
leftover processes            none
```

The root grew monotonically before worker overlap: about 1.87GB at 10s,
3.13GB at 20s, 5.11GB at 40s, 8.06GB at 61s, 10.73GB at 70s and 14.57GB at
81s.  Worker overlap raised the tree only afterwards.  Since the streaming
path visits `pcc.__main__` then the 15.0MB `pcc.cli_bootstrap` wire first, one
large decoded/analysed module or allocator high-water retention is sufficient
to recreate the whole coordinator peak; retaining all 216 ASTs is not required.

This candidate and its tests were forward-removed.  The pipeline/vthread files
are byte-identical to the retained V104/V108 sources and the retained-path
packet passes 42/42.  The next finite slice is measurement, not another build:
use the frozen failed-candidate pcc1 to isolate `pcc.cli_bootstrap` AST decode,
analysis and post-release allocator footprint as separate arms.  Only a result
which puts a named owner under the 8GB ceiling may authorize a new production
candidate.

### Single-module and allocator controls refine the owner

The initial inference that one `pcc.cli_bootstrap` decode alone recreated the
14.7GB root is retracted.  The exact candidate pcc1 codegen worker, consuming
the exact 14,488,049-byte AST plus the complete 12,215,569-byte native-exports
wire, completed in 15.790s at 2,337,619,968B peak and emitted 19,279,504 bytes
of IR.  One module is large but remains well below the 8GB line.

A first-10-module worker reached 4,781,047,808B, but source audit shows
`run_codegen_worker` loads every assigned AST into `parsed_modules[index]`
before codegen and never clears the entries; that arm therefore contains an
explicit ten-AST live set and is not allocator-only evidence.

The stronger control assigned index 1 four times.  Each decode overwrites the
same list slot, so at most one AST is reachable through `parsed_modules`.
Without debugger attachment it completed four exact outputs in 75.971s and
peaked at 8,106,917,888B.  RSS rose monotonically through 2.56GB, 3.54GB,
4.85GB, 5.75GB and 7.73GB.  This proves cross-round high water or retention
without four distinct AST list entries.

The pcc allocator exports mapped/live counters.  Two ordered read-only samples
from a repeated-module worker recorded:

```text
                              first            later
mapped capacity               4,230,361,088    5,894,324,224
allocator metadata               77,430,784      148,504,576
live requested                2,278,267,073    3,099,533,015
live usable                   2,870,372,720    3,959,230,048
mapped - live usable          1,359,988,368    1,935,094,176
```

LLDB attachment disturbs the sampler's wait/ownership relationship, so the
counter run's process-tree completion receipt is not a performance result; the
four counter values themselves are direct reads of exported 64-bit globals.
The unattached 8.107GB x4 receipt is the performance evidence.

Allocator source explains the mapped-live gap.  Every 64KiB small raw/object
slab remains mapped after its cells return to global freelists; free only
decrements live accounting.  Span arenas, radix nodes and previous granule
table generations are permanent, with old tables explicitly leaked for
lock-free-reader safety.  No trim/decommit API or per-slab live count exists,
so adding `madvise` at a phase boundary is not currently safe: it would have
to unlink every free cell, preserve immutable provenance descriptors and
support recommit/reuse.

The remaining live-byte growth means allocator retention is a confirmed
contributor, not the whole answer.  The next bounded production design should
avoid paying sequential-module high water in the long-lived coordinator:
publish per-module effect summaries from short-lived, memory-bounded summary
workers, solve the small dense graph in the parent, and let process exit return
worker allocator capacity to the OS.  An allocator trim is a separate runtime
architecture task and must not be improvised inside the compiler fix.

### Short-lived summary-worker implementation `[focused confirmed; Stage1 pending]`

The next source slice moves AST scanning out of the long-lived parent without
reviving the denied text/path adapters.  After export metadata converges, the
parent writes a vthread-only export surface.  One `summary` worker owns exactly
one AST, emits a deterministic effect wire and exits.  At most two summary
workers run concurrently.  The parent never decodes an AST; it interns summary
keys to `CompilerIntArena` IDs, scans packed caller/callee pairs to a fixed
point and publishes the existing export metadata.

The worker surface contains only fields actually read by the analysis:
function kind/owner/export/decorator/effect and class owner/export plus method
name/effect.  On the retained corpus it is 1,402,392 bytes versus the exact
12,215,569-byte full exports wire; all 216 effect summaries total 1,358,138
bytes.  The merged graph is 4,600 nodes / 7,605 edges and remains completely
equal to eager metadata.

Differential review caught a real semantic bug before any build: two
module-level definitions with the same name made the first summary edge survive
the second definition, while eager/Python rebinding lets the last definition
replace it.  Summary construction now resets a caller's prior seeds/edges on
each definition; the last-definition regression is green.

Focused evidence:

```text
summary wire/eager + worker ownership + real reexport route    green
pipeline/record/sampler/virtual-thread packet                   30 passed
existing package reexport/class alias/typing metadata           3 passed
retained 216-module corpus                                      exact equality
```

This is source-level/focused evidence only.  No current pcc1 contains the
worker protocol yet, and no Stage1/Stage2/fixed-point claim follows from it.
The next allowed expensive action is exactly one source-frozen Stage1.  It may
proceed only if the source identity is fixed; then a focused pcc1 summary-worker
canary must prove real activation, exact metadata and per-worker/root RSS below
8GB before any Stage2 launch.

### Short-lived summary workers `[DENIED; removed]`

The single permitted Stage1 succeeded and materially reduced host work rather
than slowing the denominator:

```text
pcc1 SHA-256          eb5b97e10656339a2aa5299a88c3355323b4f3b078bde69de9532652c33cef54
wall                  303.07 s
instructions          190.739B
CPU                   1145.68 s
peak footprint        1,289,455,104 B
linkage                libSystem only
```

This is 32.3% fewer instructions than the 281.911B dense-parent Stage1 at a
similar wall, and host Stage1 visibly ran the new concurrency-2 summary phase.
It is nevertheless not pcc1 correctness evidence.

The mandatory first pcc1 canary failed in 0.594s before any native emission.
All four one-module summary workers reported:

```text
NotImplementedError: no-libpython function unavailable:
pcc.py_frontend.codegen.vthread_effect_analysis.write_closed_world_vthread_effect_summary
```

The canary produced no program.  No Stage2 was launched.  Per the pre-registered
one-build rule, the candidate was not patched and rebuilt; all summary worker,
wire, dense-merge and routing source/tests were forward-removed.  The five
production pipeline/vthread files are byte-identical to retained V104/V108 and
the retained packet passes 42/42.

The failure identifies a missing cheap gate, not permission to retry: host
wire/equality tests and a strict closure of `pipeline_frontend_worker_execution`
did not compile/execute the separate summary writer.  A future design must put
the wire codec in a small standalone pcc module and make the already-built
retained pcc1 compile and run that codec as an ordinary strict multi-file
program before the codec is connected to compiler source.  Only then may one
new integrated source candidate be considered; another Stage1-first discovery
is denied.

### Standalone pcc1 effect-summary codec `[CONFIRMED]`

The line codec now lives independently at
`pcc/py_frontend/vthread_effect_summary_wire.py`; it imports no compiler
pipeline and uses only file/string/list primitives.  Host tests cover exact
deterministic bytes, roundtrip, bad schema, missing/duplicate module, malformed
edge, unknown row, empty key and validate-before-write behavior: 8/8 pass.

The codec source and canary copy share SHA-256
`c683ea08c21cd95895479263225d64ccbcb9af9b9c840e071f882f17f4b7e5cc`.
The first canary directory used a hyphen, which correctly failed self-backend
symbol validation before codec execution; it is harness evidence only.  With
an underscore package name, retained accepted V104 pcc1 compiled the two-module
strict self/no-libpython canary rc0 in 16.378s at 225,312,768B tree peak.

The executable prints `codec-ok` and emits the exact expected six-line wire.
Both dumped input IR modules contain zero `call ... @py_cpy_*` and zero
`strict.nolib.stub`; `write_summary`, `read_summary` and canary `main` have real
definitions.  The image links only libSystem and contains no codec/canary
unavailable string.

This confirms only the standalone codec's pcc1 language/execution boundary.
It is not wired into the compiler and makes no Stage1/Stage2 claim.  A future
integration must use thin adapters around this proven module; duplicating the
denied JSON writer inside vthread analysis is forbidden.

### Line-codec worker integration `[DENIED; removed]`

The proven line codec was connected through thin adapters to the previously
validated one-AST/concurrency-2 worker design.  Focused gates (21), retained
216-module equality (4,600 nodes / 7,605 edges; 1,330,150 summary bytes), three
existing multi-file nodes and inventory/sampler gates all passed before the
single allowed build.

Stage1 succeeded but its wall/CPU envelope regressed despite stable
instructions:

```text
pcc1 SHA-256          05ef30372935569167ea6a7a6280d9994855b5e225a299ba61cddaabe14b8e1f
wall                  404.52 s
instructions          191.575B
CPU                   1399.03 s
peak footprint        1,301,136,872 B
linkage                libSystem only
```

Instructions are only 0.44% above the JSON-worker Stage1, but wall is 33.5%
and CPU 22.1% worse; this is recorded as risk, not hidden as noise.

The mandatory pcc1 four-module canary failed rc1 in 1.182s.  The first two
concurrent summary workers reported `AttributeError: __init__`; no executable
was produced and no Stage2 ran.  Because the standalone codec already has a
green strict pcc1 roundtrip, this failure belongs to the real compiler-package
summary-builder/`__init__.py` closure rather than codec serialization.

No patch/rebuild was attempted.  All integrated worker/adapter/dense-merge
source and tests were forward-removed.  The standalone codec remains as an
independently proven utility; retained compiler paths plus codec tests pass
50/50.  Before any future integration, an accepted pcc1 must compile and run a
small ordinary program that exercises summary construction for package
`__init__.py` ASTs with the compact export surface.  Another Stage1-first
discovery is denied.

## Update 2026-08-30 — class canary corrected; Stage2 memory confirmed

The four-module class canary was an invalid discriminator.  Accepted V104 pcc1
fails the same source with the same `AttributeError: __init__` after export;
the candidate summary wires were already complete.  A replacement function-only
four-module/two-level-reexport canary activates four summary jobs at concurrency
two, compiles and links in 18.305s at 311.5MB tree peak, runs with output `42`,
and links only libSystem.  The earlier package-`__init__.py` attribution is
therefore retracted; the historical failed receipts remain retained.

The frozen line-codec candidate then completed a real cache-off GC0 Stage2:

```text
pcc1 SHA-256              05ef30372935569167ea6a7a6280d9994855b5e225a299ba61cddaabe14b8e1f
stage wall / rc           732.058s / 0
pcc2 SHA-256              4772435883edb165ad37302197b89de39ca257c6b332d64663baabaf2c534b56
tree / any-process peak   11.031GB / 4.091GB
summary jobs/parallel     218 / 2
summary nodes/edges       4,731 / 7,789
linkage                   libSystem only
```

This confirms the intended coordinator-memory mechanism and produces a usable
pcc2.  It does not yet accept the source: 732.058s is 7.06% slower than the
retained V79 683.796s control and misses the pre-registered +5% ceiling by
14.072s.  The human reports that ZDB testing was active during the candidate
run, so that wall result is contaminated rather than a valid denial.  Candidate
aggregate CPU is also lower at 3437.255s versus V79's 3523.362s.  Summary/
export itself improves 56.375s -> 45.013s; the observed wall delta is in later
codegen/emit/runtime phases and may reflect the external load.

The integration remains absent from the current worktree and Stage3 has not
run.  The next source-free discriminator is one adjacent cache-off V79 versus
candidate Stage2 pair under the performance lock after machine load is quiet.
Full receipts and the exact claim boundary are in
`docs/goal/evidence/2026-08-30-summary-worker-stage2-memory-confirmed-wall-unresolved.md`.

## Update — summary coordinator accepted and GC0 fixed point

The required adjacent cache-off comparison reverses the contaminated wall
reading.  V79 completes Stage2 in 708.970s / 4023.414 CPU at 14.374GB tree
peak.  The line-codec summary candidate completes in 578.301s / 2953.624 CPU at
10.569GB, with every process below 4.111GB.  It is 1.225953x faster in wall and
uses 26.6% less CPU.  Both produce runnable self/no-libpython pcc2 artifacts;
their bytes differ because their compiler sources differ.

Candidate pcc2 then completes Stage3 in 549.770s.  Pcc2 and pcc3 are raw
byte-identical at `47724358...`, both link only libSystem, and the complete
Stage3 tree/any-process peaks are 10.894GB/4.224GB.  Summary counters remain
218 jobs, concurrency two, 4,731 nodes and 7,789 edges.

The five production files were reapplied by forward patch and match the frozen
candidate byte-for-byte.  New retained regressions cover eager-summary equality,
duplicate-definition last-binding semantics and one-AST worker ownership;
focused summary/reexport/worker gates pass 24 + 3 and bootstrap baseline passes
2 with two intentional deselections.

This closes the coordinator and GC0 object-projection boundary.  It does not
claim the global Stage2<=CPython3.15-Stage1 ratio, parallel/provenance emit or
GC1--4.  Full receipt:
`docs/goal/evidence/PERF-P1-STAGE2-COORDINATOR-IR-STREAMING/001-summary-worker-fixed-point.md`.

## Update 2026-09-04 — compiled export/summary light lanes use measured admission

The retained short-lived summary-worker design was still hard-limited to two
native processes because its original acceptance preceded the current
process-tree guard. A new same-source pcc1 checkpoint separates these
one-module workers from the 3 GiB compiled codegen risk class.

Export widening alone was insufficient: width five changed the combined
checkpoint phase only 69.983s -> 55.826s (1.254x). Under one 16 GiB outer
guard, widening both export and summary from two to the formula-derived seven
changes the source-identical phase 69.069s -> 38.838s (1.778x) and total
checkpoint 116.018s -> 88.334s (1.313x). Tree peak changes only 6.997GB ->
7.293GB. Native exports and all 224 AST sidecars are byte-identical, summary
nodes/edges remain 4,988/8,264, and codegen remains width two.

`[CONFIRMED]` for measured light-lane admission. Unknown and 8 GiB budgets
still fail closed to width two; the measured 16 GiB envelope admits seven.
This is not worker reuse (which remains denied), not a codegen widening, and
not a full Stage2 result. Full receipts and the invalid numeric-override arm
are documented in
`docs/goal/evidence/PERF-P0-COMPILED-EXPORT-MEASURED-ADMISSION/001-export-summary-light-lane-admission.md`.

## Update 2026-09-05 — native encoding siblings need the same probe model

The original closed-world probe correction covered only self_backend_* module
names. The native producer buffer also imports arm64_asm_driver, arm64_encode,
native_object and macho_spec as part of that same compiler closure. Isolated
OFF probes invent CPython imports/arena/relocation/struct accesses: encode has
191 actions, native_object 213 and macho_spec 14; the driver requires siblings
to compile independently. Exact IR/source receipts are under
`build/native-buffer-off-attribution/`.

Before changing policy, an explicit four-module contextual probe using the
actual 210-module closure passed with all four counts exactly zero in 13.30s.
The native v76 ASM/PCO canaries and raw-byte GC0 pcc2/pcc3 fixed point separately
prove the production execution boundary (evidence066). The new policy test
then observed standalone != closed-world for the encoding siblings in 0.10s.

The bounded correction is to apply the existing closed-world/no-L1-mixin
model to those four encoding/format siblings. Their contextual ON gate must
remain exact-zero and use real schemas; do not increase their raw ceilings or
silently skip their coverage. This extends the already-confirmed probe model,
not the compiler runtime's import owner. Qualification is pending.
