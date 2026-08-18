# Compiler TypeDesc and slot-record canonicalization

Date: 2026-08-28

Task: `PERF-P0-COMPILER-VALUE-RECORD-PROJECTION`

## Claim boundary

This slice establishes one canonical object projection per structural LLVM
type and an authoritative dense slot table. It does not yet remove the final
legacy SlotInfo projections from AArch64 consumers, improve item311 wall time,
or prove whole-Stage2/fixed-point/GC0..4 transfer.

## Durable inventory tool

`scripts/pcc_record_inventory.py` now runs the real parse -> verify -> indexed
kernel -> stackprep -> precise-stackmap path under the shared performance lock
and writes a source-hashed JSON receipt. Its focused contract is
`tests/python/test_pcc_record_inventory_tool.py` and it is indexed in
`AGENTS.md`.

Frozen input:

```text
build/stage2-current-object-inputs-no62-v1/item_311.ll
sha256 76af6689f079d29a5965733c4e7b365c9d4a8ccc16d0ce8a70e21fea6b65468c
5,108,635 bytes; 1 function; 9,474 blocks; 59,984 instructions
```

## TypeDesc result

Before this slice, the reachable parse-to-stackmap graph contained 137,468
distinct TypeDesc identities but only 74 structural values. The dominant
duplicates were 64,474 `void` and 66,469 `ptr` objects. The prefix parser was
constructing a new frozen dataclass before it knew the token boundary and then
overwriting the text cache.

Leaf types are now canonical per parse, typed-pointer wrappers pin their
identity keys, and stackprep publishes the kernel's canonical object for each
type ID. The final graph is exactly one TypeDesc identity per structural value:

```text
TypeDesc unique objects       137,468 -> 74
TypeDesc structural values         74 -> 74
TypeDesc shallow bytes       6,598,464 -> 3,552
parse-stage TypeDesc objects              69
post-stackprep TypeDesc objects           74
```

The `id()`-keyed pointer cache retains the pointee in each entry, so allocator
address reuse cannot turn a stale key into a wrong hit.

## Slot result and dense data plane

The same baseline held 17,471 SlotInfo objects but only 1,657 distinct
`(offset, type)` values. Stackprep now interns the identity-free legacy records
and publishes an authoritative indexed representation in
`IndexedFunctionKernel`:

```text
value_id -> slot_id -> offset/type_id
value_id -> alloca_offset/allocated_type_id
```

Current inventory:

```text
SlotInfo unique objects        17,471 -> 1,657
SlotInfo structural values      1,657 -> 1,657
SlotInfo shallow bytes        838,608 -> 79,536
reachable unique graph        595,927 -> 442,715
```

The remaining 1,657 SlotInfo objects are deliberate temporary compatibility
projection. They must reach zero on the supported AArch64 path after consumers
move to the dense table; the dual-write state is not completion.

## Correctness and pcc1 evidence

```text
Type/parser/kernel/inventory focused gate       28 passed
self-backend focused packet                    358 passed
strict no-libpython closure: parse/kernel/stackprep  passed
host item311 assembly                          ff943e10... exact
```

Source-frozen strict Stage1 compilers:

```text
TypeDesc candidate pcc1  74d5bcba4f12a0efe80c07eebb2d56f1575afc0c66902f869fd1a9f89d2f15b4
  Stage1 234.51 s; libSystem only; smoke passed
slot-dense pcc1          2f0b605a6f120e8c43d8ab8027d5a94949afdc18573f07202722894e95af58f3
  Stage1 237.18 s; libSystem only; smoke passed
```

All item311 arms emitted exact assembly SHA-256
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`:

| arm | wall | instructions | footprint |
|---|---:|---:|---:|
| pre-TypeDesc pcc1 | 28.97 s | 406.805 B | 4.190 GB |
| canonical TypeDesc | 29.22 s | 408.867 B | 4.134 GB |
| dense slot + legacy dual write | 29.84 s | 416.120 B | 4.162 GB |

The TypeDesc slice lowers footprint about 1.3% but is wall/instruction neutral.
The slot dual-write state is slower because it pays for both representations.
No performance claim is attached; the required next step is to migrate the
supported consumers and delete the legacy projection on that path.

## Status

`[CONFIRMED]` structural TypeDesc canonicalization and dense slot-table
foundation. `IN PROGRESS` for complete slot/value projection; no Stage2 or
fixed-point claim.

## Dense-only AArch64 follow-up

All supported AArch64, precise-stackmap, register-allocation, call, compute,
memory, materialization, flow and prologue consumers were migrated off direct
SlotInfo/AllocaInfo access. The AArch64 preparation path now stores slot IDs
and alloca value IDs in the compatibility name maps; the authoritative payload
is in kernel integer columns. Construction-only slot interning indexes are
cleared after stackprep.

The final item311 inventory is:

```text
TypeDesc unique / structural       74 / 74
SlotInfo unique                     0
AllocaInfo unique                   0
legacy slot projections             0
dicts                           9,486
reachable unique objects       440,166   (baseline 595,927)
```

Dense-only focused gates are 390 passed and host item311 remains exact
`ff943e10...`. Strict single-module closure passes for 16/17 changed backend
modules; the remaining `self_backend_aarch64_darwin.py` failure is identical
on the frozen pre-change source (`iterable splat cannot precede ...`) and is
not attributable to this slice.

Two strict pcc1 builds passed:

```text
dense-only v2 pcc1 a902aa456cc60b71f44bd5cffa8f41042855aad7a396e92558d29bfa8a1a579a
  Stage1 264.63 s
dense accessor v3 pcc1 6dbd88ad60b4abd2b731db6e68fabe7f1462d1a3db3bdd12d81d877a4b272ad4
  Stage1 244.14 s
```

The first dense-only worker was 32.46 s / 432.747 B instructions. A caller
profile attributed 9.09% to the new slot-helper path and another 2.32%/1.52%
to raw/bucket accessors: each operation looked up the same name once for offset
and again for type. Collapsing that to one slot-ID lookup reduced instructions
to 428.749 B, but the pre-dense compiler is still 406.805 B. The v3 wall/CPU
run was load-contaminated (39.44 s / 36.49 s) and is not used as a ratio.

`[DENIED]` as the final execution interface: cross-module
`name -> scalar slot helper -> parts helper` still adds call/frame/lookup tax.
The zero-object structural representation remains; the next implementation
must carry `kernel + value_id/slot_id` into consumer-local emit loops and read
the columns directly, without reconstructing names or SlotInfo views.

## Indexed consumer and call-operand follow-up

`emit_function_blocks` now carries kernel/block/instruction IDs into the
indexed AArch64 callback. Ordinary call destinations consume the defined
value ID directly, and local call arguments can consume aligned call-argument
value IDs. A two-block regression locks global instruction offsets; without
it every non-first block incorrectly read call facts from offset zero.

Measured iterations, all exact `ff943e10...` assembly:

| arm | wall | instructions | footprint | verdict |
|---|---:|---:|---:|---|
| pre-dense | 28.97 s | 406.805 B | 4.190 GB | reference |
| dense wrapper v4 | 32.45 s | 422.140 B | 4.152 GB | wrapper denied |
| runtime-filtered use IDs v6 | 31.00 s | 431.240 B | 4.151 GB | denied |
| aligned Python-list facts v7 | 31.26 s | 436.583 B | 4.220 GB | denied |
| raw aligned arena v11 | 32.49 s | 432.756 B | 4.209 GB | incomplete/denied as speed win |

The raw arena removes the per-block Python-list graph and improves over v7,
but per-argument arena access still costs more than the old object projection.
One correctness build failed because the global block base was accidentally
published in the definitions-first loop; the enhanced diagnostic showed
`int.obj.base...` mapped to `ov.flag.15`. Moving publication to the actual
instruction-use loop plus the new multi-block regression fixed it. Final v11
strict Stage1 is green (`1bd5c765...`, 279.86 s, no libpython/LLVM, smoke
passed), and the final focused dense/stackmap packet is 392 passed.

No whole-stage or performance acceptance follows. The next representation
must batch aligned call facts at the call site (using the confirmed fixed-i64
aggregate intrinsic) or otherwise eliminate per-argument arena helper calls;
reintroducing generic lists or runtime `is_local_value_ref` is denied.

## Fixed-width call-site batch denial

The requested consumer-local follow-up packed each call's local operand IDs
into padded four-i64 groups, published one global instruction base per block,
and loaded the group with the compiler-owned `load_i64x4` intrinsic.  A
multi-block regression covered the global instruction-ID boundary.  The
candidate passed 392 focused tests, strict closure, a no-libpython/self Stage1
build, and emitted exact item311 assembly, but it was slower than both the
pre-dense reference and the retained dense source:

```text
candidate pcc1  5d24ccca8957fbb52640317fa82dd7b7c0e63901b71380f9ac5ebbea0725b96b
Stage1          236.09 s; libSystem only; smoke passed
item311          31.33 s / 433.784 B instructions / 4.209 GB footprint
assembly         ff943e10... exact
```

`[DENIED]`: item311 has 46,225 calls but only 56,233 arguments, so padding and
publishing a persistent per-instruction call table adds more work than the
consumer avoids.  The candidate source and its temporary regression were
removed.  This also overturns the previous task-board prescription that a
fixed-i64 call-site aggregate was the next accepted representation; the
intrinsic remains useful, but this call-fact application is not.

## Dense last-use column follow-up

The next persistent owner was not speculative: `block_last_uses` allocated one
dictionary for every one of item311's 9,474 blocks.  Last-use is now one dense
`value_id -> position` integer column.  The already-published definition block
validates block locality, and the legacy nested dictionary projection is built
only by the explicit diagnostic adapter.  AArch64 register allocation consumes
`kernel.last_use(block_id, value_id)` directly; the small MADD extension uses
parallel temporary integer lists rather than mutating a persistent block
dictionary.

Current inventory (`build/native-data-plane-record-inventory-v11.json`):

```text
dicts                         9,486 -> 12
reachable unique objects    440,166 -> 430,692
TypeDesc unique / structural            74 / 74
SlotInfo / AllocaInfo                         0 / 0
legacy slot projections                         0
```

The source-frozen candidate and representative worker are:

```text
candidate pcc1  8463af64cc0ac660255439c276f6097b3dd4cd88e925b6923fb669dc074db6ff
Stage1          233.68 s / 295.775 B instructions; libSystem only
item311          30.42 s / 418.044 B instructions / 4.146 GB footprint
dense-v5         30.20 s / 422.956 B instructions / 4.152 GB footprint
pre-dense        28.97 s / 406.805 B instructions / 4.190 GB footprint
assembly         ff943e10... exact
```

The 0.22-second wall difference from dense-v5 is inside single-run noise;
instructions improve 1.16% and footprint 0.15%.  This is `[CONFIRMED]` as a
native-data-plane structural deletion with no observed performance regression,
not as a material speedup claim.  Current-source gates:

```text
pytest indexed-kernel + AArch64 regalloc             18 passed
strict self/no-libpython kernel closure              passed
strict self/no-libpython AArch64 regalloc closure    passed
```

The remaining item311 graph still contains 112,640 generic lists and 287,605
tuples.  They require owner attribution and kind-specific end-to-end deletion;
the denied generic tagged instruction arena, per-scalar raw getters, and this
denied fixed-width call-fact table must not be revived under a new name.

## Def/use batch value records

The inventory tool now attributes every unique container to its first
breadth-first nearest non-container field; the owner rows partition the exact
container count.  On the last-use source, four kernel def/use families retained
9,475 lists each:

```text
IndexedFunctionKernel.defined_value_ids          9,475 lists
IndexedFunctionKernel.instruction_use_offsets    9,475 lists
IndexedFunctionKernel.instruction_use_ids        9,475 lists
IndexedFunctionKernel.terminator_use_ids         9,475 lists
```

The replacement is kind-specific rather than another tagged instruction
arena.  One four-i64 block record carries instruction start/count and the
terminator's zero/one use.  One four-i64 instruction record carries
`dest_id`, `use_count`, first use, and either the second use or a negative
overflow-span token.  Item311's measured distribution makes that the normal
path: 57,124/59,984 instructions have zero or one use, 2,310 have two, and
only 550 use the raw overflow arena.  Common consumers therefore read one
whole value record; there is no per-use getter on the common path.

Deletion receipt (`build/native-data-plane-record-inventory-v13.json`):

```text
lists                    112,640 -> 74,743
reachable unique objects 430,692 -> 392,798
removed persistent lists              37,897 net
dicts                                      12 unchanged
tuples                                287,605 unchanged
```

The 37,900 old nested lists are replaced by three host-oracle `_values` lists;
under pcc1 those `CompilerIntArena` instances use raw malloc-backed i64
storage and the lists remain empty.  The arenas are explicitly closed after
the target's final stack-map rendering so this representation does not leak
one raw allocation per function.

The first source-frozen Stage1 attempt exposed a real generic value-model gap:
an imported valueclass used in a provider's public annotation was expanded
before re-export convergence, and class reconstruction used the viewing module
instead of `info.owning_module`.  Sequential and parallel export fixed points
now share the owner-aware expansion.  Explicit arena field and batch-local
annotations keep the smaller pcc1 subset on the aggregate projection.  The
three-module absolute/relative regressions and the real 18-module
value-arena/kernel/stackmap parallel closure pass.  Details are in
`docs/investigations/cross-module-imported-valueclass-return-abi.md`.

Focused result:

```text
cross-module export/wire, unsafe i64x4, inventory, kernel, verifier,
stackmap, AArch64 regalloc/target and x86 harness       123 passed
real cache-off parallel kernel closure                  passed
pcc1 i64x4 strict no-libpython aggregate gate           passed
```

Source-frozen Stage1 B-A-B used the same GC0 runtime bundle and jobs:

| arm | wall | CPU | instructions | footprint |
|---|---:|---:|---:|---:|
| candidate-v4 (loaded outlier) | 306.35 s | 1109.01 s | 296.669 B | 1.576 GB |
| last-use control | 264.22 s | 1039.74 s | 295.950 B | 1.609 GB |
| candidate-v5 | 245.61 s | 1006.56 s | 296.066 B | 1.600 GB |

Candidate-v5 is
`f47882bfe22d40715b423516c70ea1d330fcf4a3f4174fe28988b479af0f903f`,
links only libSystem, and passes its smoke.  The bracketing pattern denies the
v4 wall as a performance conclusion; it supports no Stage1 regression, not a
robust 7% speedup claim.

Matched item311 control/candidate and the v5 repeat all emit exact
`ff943e10...` assembly:

| arm | wall | instructions | footprint |
|---|---:|---:|---:|
| last-use control | 31.36 s | 418.297 B | 4.146 GB |
| candidate-v4 | 31.27 s | 417.935 B | 4.130 GB |
| candidate-v5 repeat | 29.84 s | 417.955 B | 4.130 GB |

`[CONFIRMED]` as an end-to-end batch value projection and deletion of the four
nested def/use list families, with no observed Stage1 or worker regression.
No whole-Stage2, fixed-point or GC0..4 claim follows.  The remaining dominant
owners are `CompactParsedInstrArena._data` (209,361 tuples; generic tagged
packing remains denied), value-name buckets (18,444 lists + tuples), legacy
value-slot buckets (17,471 lists + tuples), block-name buckets (9,474 lists +
tuples), per-block instruction/flag lists, and phi lists.

## Slot/name deletion and shared-ID recovery

Indexed AArch64 stackprep now leaves all four compatibility slot maps/buckets
empty.  Supported consumers use dense kernel IDs; the legacy materialized path
still passes its direct-map mutation tests.  Inventory v15 proves that deleting
the view did not delete semantic storage:

```text
dense value-slot / alloca bindings       17,471 / 898
legacy value/alloca map entries                 0 / 0
legacy SlotInfo/AllocaInfo projections          0 / 0
lists                                74,743 -> 56,374
tuples                              287,605 -> 269,236
reachable                           392,798 -> 356,060
```

The first execution interface was `[DENIED]`: nested name helpers regressed
matched item311 from 417.847B to 459.459B instructions.  Freezing value/block
names into two malloc-backed open-address `(stable_hash,id)` two-i64 tables
removed another 27,916 lists and 27,918 tuples, but by itself measured
461.319B; packing storage does not remove call-boundary tax.

The retained recovery reuses already-published instruction facts instead of
adding a call table.  Destination and call operand IDs flow as raw scalars;
materialization reads dense alloca/register/slot columns; stackmap reloads,
regalloc and terminators consume IDs they already own.  One cached block fact
supplies the global instruction start/count and terminator facts, so emission
uses `instruction_fact_by_id` rather than rereading a block record per
instruction.

All measured arms emit exact `ff943e10...` assembly:

| representation | item311 instructions | footprint | wall |
|---|---:|---:|---:|
| pre-map-deletion control | 417.847 B | 4.130 GB | 29.88 s |
| maps deleted, named helpers | 459.459 B | 4.128 GB | 32.72 s |
| native text index only | 461.319 B | 4.093 GB | 38.89 s |
| shared call/destination IDs | 435.344 B | 4.087 GB | 30.95 s |
| direct analysis + terminator IDs | 432.287 B | 4.077 GB | 31.13 s |
| cached block/global instruction ID | 424.900 B | 4.079 GB | 30.21 s |

The last measured compiler is
`69108a591edd48c85c7a8ed464b78475d466ade42edf6104ca1ec1ff648020e7`:
Stage1 252.22s / 296.703B instructions, no libpython/LLVM.  The worker remains
1.69% above the old-map instruction count, so this is structural progress and
performance recovery, not a speedup claim.

`ParsedBlock.phis` is now a shared empty tuple or an immutable tuple for the
267 non-empty blocks, rather than 9,474 per-block lists.  The parser resets its
operand-intern table per module; a deterministic two-parse regression prevents
a prior module from stealing the next module's within-module string identity.
Inventory v17 on current source:

```text
lists                 18,984   (initial 112,640)
tuples               241,585   (initial 287,605)
dicts                      12   (initial 9,486)
reachable            291,021   (initial 595,927; dense-v5 440,166)
dense slot/alloca      17,471 / 898; legacy maps/projections zero
```

The current phi composite has 376 focused tests and strict closure but awaits
inclusion in the next pcc1 build.  The dominant open family is now exactly the
instruction plane: 9,474 `_data` lists, 9,474 arithmetic-flag lists and
209,361 payload tuples.  The denied generic tagged arena must not return; the
next representation must publish kind-specific facts once and delete their
tuple/list projection through every consumer.

## Kind-specific instruction plane and quadratic-bytearray correction

The supported scalar instruction families now publish kind-specific call,
alloca, fixed-instruction and GEP records plus operand spans once.  Verifier,
stackprep, precise stack maps, AArch64 register allocation, target passes and
emit consume dense IDs and whole `CompilerInt2`/`CompilerInt4` records.  The
final item311 inventory is:

```text
instruction/call/TypeDesc projections, verified -> emitted       0
SlotInfo / AllocaInfo / legacy slot projections                   0
packed instructions                                          59,984
final reachable unique objects                                  175
final tuple/list/dict containers                              2 / 45 / 15
```

The parse construction boundary is not closed: before kernel construction it
still owns 9,474 `_data` lists and 209,361 nested payload tuples.  Inventory
`build/native-data-plane-record-inventory-v61.json` records 258,157 reachable
objects at `parsed`, falling to 151 after stackprep.  This slice therefore
proves zero supported hot projection after kernel construction, not a complete
parse-to-kernel native builder.

Two candidate ideas were denied before the retained fix:

- Python-list parser columns reduced the persistent tuple graph but measured
  500.918B instructions and 10.645GB footprint; it was removed.
- On the same v12 pcc1, retaining all old compiler projections measured
  485.568B / 10.730GB versus normal release at 476.654B / 10.749GB, both exact
  `ff943e10...`.  This disproved deallocation fragmentation as the 6GB owner.

Live allocator reads established the real mechanism.  At about seven seconds,
v12 had 6.166GB live requested versus 1.095GB for the pre-regression v7, while
both had about 2.1M tracked objects.  A targeted heap scan attributed 3.193GB
already live to large bytearray objects.  `IndexedFunctionKernel` had added
three function-wide bytearrays and appended one byte per instruction.  pcc's
inline bytearray append constructs and copies a length-`n+1` object, so each
column costs `sum(1..N)` bytes; for `N=59,984`, three columns have a 5.4GB
quadratic envelope.

The retained representation is one four-i64 `CompilerIntArena` metadata record
per instruction:

```text
(kind_id, packed_payload_id, volatile_bit, arithmetic_flags_bit)
```

Hot consumers load the whole metadata record once.  The old three bytearray
columns and the separate payload arena no longer exist, and focused tests
assert that they cannot silently return.

Current-source gates and source-frozen evidence:

```text
focused self-backend/native-plane packet                       405 passed
strict self/no-libpython full compiler closure                    passed
v13 pcc1 sha256       987f49473166f3b2c0ae0883301ee528635ff5d05950766bbaf97446343195d7
v13 Stage1            288.81s / 305.787B instructions / libSystem only
```

The Stage1 receipt uses the current compiler source with the archive-matched
v9 runtime mirror, because unrelated in-flight GC runtime source does not match
that frozen archive; it is not a full-current-runtime claim.

Repeated item311 results, both exact
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`:

| compiler | wall | instructions | footprint |
|---|---:|---:|---:|
| v12 quadratic byte columns | 37.89s | 476.802B | 10.752GB |
| v13 native metadata | 33.06s | 439.096B | 4.033GB |
| v13 repeat | 33.37s | 439.253B | 4.036GB |

At the same early phase, v13 live requested is 1.166GB versus v12's 6.166GB.
This is `[CONFIRMED]` as a semantic/output-neutral deletion of a quadratic
managed-object path: about 8% fewer worker instructions, 12% lower wall and
62% lower footprint versus v12.  It does not prove whole Stage2, fixed point,
GC0..4 transfer, or complete construction-boundary projection closure.

## Managed liveness state projection

Post-bytearray caller attribution puts stack-map planning at about 47% of the
item311 sample.  Sizing proved that managed liveness is sparse:

```text
tracked managed values                 518
instruction slots                   59,984
distinct live-set contents             519
all slot memberships                  8,997
maximum members per live set              2
```

Several apparently packed forms were rejected before the retained interface:

- `instruction_id -> state_id -> span -> scalar values` repeated the known
  per-scalar arena mistake.  Combined with a similarly scalarized entry-state
  experiment, v14 exhausted the existing 360s Stage1 gate after all 630 emit
  results; it has no success receipt.
- Passing imported `CompilerInt4` through a method ABI failed the pcc0->pcc1
  frontend gate in v15.  The aggregate is now consumed at its owning callsite.
- One four-i64 record per instruction built correctly but measured 447.856B /
  34.84s on v16 versus v13's 439.096-439.398B / 33.06-33.24s.  It was removed.

The retained form stores a managed-live state ID in the already-reserved
fourth scalar of each packed call span.  Empty state zero is implicit; only
nonempty live calls publish an ID.  One state record contains
`(count, first_id, second_id_or_overflow, reserved)`, and uncommon tails use a
raw overflow arena.  `_record_kind_indexed` already reads the call span and
returns the state ID in the same batch, so the hot consumer does not reread the
record.

Source-frozen v19 (`db540512...`) is strict no-libpython/self and links only
libSystem:

```text
Stage1                  293.15s / 306.374B instructions
item311 first           34.24s / 439.329B / 4.031GB
item311 repeat          32.79s / 439.341B / 4.031GB
paired v13 control      33.24s / 439.398B / 4.033GB
assembly                ff943e10... in every arm
```

This is `[CONFIRMED]` as a performance-neutral deletion of the persistent
`list[list[frozenset[int]]]` projection, not a speedup claim.  The liveness
dataflow builder still uses temporary Python sets for uses/defs/live-in/live-
out and remains an open native-plane boundary.

The next line-index experiment was `[DENIED]`: raw route spans consumed through
per-block/per-route getters increased controlled host inventory instructions
95.086M -> 101.402M (+6.6%) with exact assembly.  It was removed without a
pcc1 build.  A separate lazy-entry change (head-index FIFO and no active dict
for blocks without frame protocol) is host-exact and reduces host instructions
slightly, but has no pcc1 receipt yet and remains pending rather than accepted.

## Lazy root-entry state and route-index denial

The pending entry-state deletion is now source-frozen and `[CONFIRMED]`.
`_block_entry_states` uses a head-index FIFO rather than `pop(0)`, and delays
constructing the active root dictionary until a call is proven to be one of
the four frame-enter/leave protocol symbols.  Ordinary call-heavy blocks reuse
their immutable incoming state tuple without allocating a dict.

Source-frozen v20 (`a26f667f...`) links only libSystem.  Repeated item311:

```text
v19 liveness control       439.329 / 439.341B; 4.031GB
v20 lazy entry             429.106 / 429.147B; 3.849GB
wall                       32.13 / 32.38s
assembly                   ff943e10... exact
```

This removes 2.33% worker instructions and 4.5% footprint on top of the
quadratic-bytearray fix.  RootGroup/location dataclasses and dictionaries on
actual protocol blocks remain open; only the provably unnecessary normal-
block projection is deleted.

Two attempts to remove `build_line_index` were denied:

- separate raw block/route spans regressed controlled host instructions 6.6%;
- reusing reserved call/terminator fields avoided new containers and passed
  all correctness gates, but source-frozen v21 measured 445.783B / 36.29s /
  3.830GB versus v20's 429.1B / ~32.3s / 3.849GB.  The 0.5% memory saving does
  not justify a 3.9% instruction regression.

Both route experiments were removed.  The worktree's compiler sources are
byte-identical to the accepted v20 snapshot for kernel, precise-stackmap,
indexed emit and AArch64 top-level modules.

## Parser call scratch freeze denial

Cloudflare's mutable-builder-to-frozen-storage pattern was tested at the
largest remaining parser owner rather than accepted by analogy.  A
function-local `ParsedCallScratch` wrote call headers and arguments into
`CompilerIntArena`, then the indexed kernel translated those scalar records
directly and released the scratch.  Diagnostic tuple projection remained
explicit and unused on the normal path.

The structural result was real.  On frozen item311
(`76af6689...`, 5,108,635 bytes), inventory
`build/native-data-plane-record-inventory-v93-call-scratch.json` changed the
parse boundary as follows while retaining the internal host assembly
`d167ea28...` and zero verified-to-emitted diagnostic projections:

```text
parsed payload tuple references       211,961 -> 17,053  (-91.95%)
call payload tuple references         194,908 -> 0
parsed reachable objects              258,157 -> 65,858  (-74.49%)
reachable call records                46,225
reachable call argument records       56,233
```

A frozen v20/current/v20 host bracket emitted exact `ff943e10...` assembly.
Current-source host instructions were 106.875B versus 107.342/107.461B for
the controls, and peak footprint was 191.5MB versus 222.9/223.9MB.  Thus the
object deletion was host-CPU-neutral and reduced host memory about 14%.

The self-compiled result overturned that apparent win.  Source-frozen v22
(`8953bfb2...`) built strict no-libpython/self successfully, linked only
libSystem, and its Stage1 was neutral at 295.18s / 306.702B instructions
versus v20's 302.85s / 307.113B.  But the representative pcc1 worker bracket
was:

```text
v22 call scratch first     496.569B / 3.694GB / 46.80s
v20 control                429.113B / 3.849GB / 32.50s
v22 call scratch repeat    496.659B / 3.694GB / 39.33s
assembly                   ff943e10... exact in every arm
```

The candidate therefore costs a repeatable 15.7% more pcc1 instructions for
about 4.0% less footprint.  It is `[DENIED]` and the implementation plus its
temporary tests/inventory schema fields were removed.  The compiler sources
are again byte-identical to v20 for `self_backend_ir.py`,
`self_backend_parse.py`, and `self_backend_kernel.py`; the post-removal focused
packet is 396 passed.  The retained lesson is narrower: parser call tuples are
a large memory/object owner but not the current CPU owner, and
`CompilerIntArena` method/getter traffic is not an acceptable parser builder.
The measured CPU owner remains precise stack-map construction, so its
temporary liveness sets and real protocol-block root/location projections
take priority over another parser representation experiment.

## Main-plan immutable root-state reuse

The post-call-scratch profile was split by deepest precise-stackmap owner
before selecting another change.  `_managed_live_after` had only about 110
samples in the retained full capture; the direct
`build_function_stack_map_plan` body had 3,726, `_block_entry_states` 1,125,
`add_record` 831 and line-index construction 544.  This ruled out immediately
converting the liveness sets merely because they remained Python objects.

Additive inventory sizing in
`build/native-data-plane-record-inventory-v96-stackmap-active-insertions.json`
then measured the actual root-state plane on frozen item311:

```text
reachable blocks / nonempty entry states            9,474 / 9,473
distinct entry-state contents / identities             446 / 854
root groups / root locations                            615 / 615
entry-state group references                              1,626,262
blocks containing frame enter/leave                      1,278
group insertions required by those blocks                 219,138
avoidable main-plan dict group insertions               1,407,124
avoidable whole-block dict constructions                    8,196
```

Therefore an empty-state fast path had zero useful ceiling: only the function
entry is empty.  The retained change instead keeps the already canonical
immutable entry-state tuple in the main planning loop.  It materializes the
string-keyed mutable `active` dict only when a block actually reaches a frame
enter/leave operation, and refreshes the tuple lazily only before a subsequent
safepoint consumes the mutated state.  Location interning now accepts that
tuple directly; the existing XOR collision check still compares every pinned
group by identity.  The authoritative `_block_entry_states` join analysis,
duplicate-enter/leave-without-enter diagnostics, frame offsets, managed
reloads and emitted map format are unchanged.

This is distinct from two historical experiments in
`pcc1-stage2-emit-throughput-and-memory.md`: No.76 reused an unchanged tuple
inside the entry-state CFG analysis itself (already present in v20), while
No.78 retained one transition reference per instruction and was denied.  This
slice adds no transition table and does not cache protocol decoding; it avoids
re-expanding the finished entry state in the second/main consumer.

Correctness and host evidence:

```text
focused self-backend/native-plane packet             397 passed
strict self/no-libpython precise-stackmap closure      passed
host item311 instructions              107.193/107.373B controls
                                       103.670B candidate
host item311 footprint                  222.3/218.4MB controls
                                       200.9MB candidate
host/pcc1 assembly                      ff943e10... exact
```

The first receipt attempt reached a complete executable pcc1 at the 360-second
watchdog but timed out during final link/receipt flushing, so it was used only
as an early-denial diagnostic.  The calibrated 420-second rerun produced the
claim-grade v24 receipt:

```text
v24 pcc1 SHA-256       d5cae2e7d35bc712ff610834d74ac243c3b7a115997a344ac5948fad04e4e60f
v24 Stage1             305.40s / 306.756B instructions
v20 Stage1             302.85s / 307.113B instructions
linkage                 libSystem only; no libpython or LLVM
```

The formal v24 item311 run and its adjacent v20 control were:

```text
                         v20 control        v24 immutable entry state
wall                     33.72s             31.90s       1.057x
CPU                      33.26s             31.58s       1.053x
instructions             429.399B           416.012B    -3.12%
peak footprint           3.849GB            3.617GB     -6.02%
assembly                 ff943e10...         ff943e10... exact
```

Three earlier candidate diagnostics were 416.824B, 416.250B and 416.319B,
so the instruction reduction is stable rather than a load artifact.  Wall
varied with machine load, which is why CPU, instructions and footprint carry
the acceptance.  This slice is `[CONFIRMED]` and retained as a semantic-neutral
removal of repeated mutable object projection.  It does not complete the
root plane: the 1,278 actual protocol blocks still require mutable dictionaries
and `_RootGroup`/`PlannedRootLocation` object projections.

## Parser call scratch direct-batch retry denial

The first call-scratch denial was reopened only after profiling its preserved
v22 pcc1.  Against the later v24 profile, the failed compiler moved
`IndexedFunctionKernel.__init__` from 1,474 to 3,079 deepest-owner samples and
`get_indexed_function_kernel` from 294 to 1,718.  Parser call helpers also
grew, but the dominant new cost was the scratch being decoded and copied by
the kernel through `ParsedCallScratch.header/span/arg/text/type` and
`CompilerIntArena.get*` method frames.

The bounded retry used four-i64 argument records and compiler-owned
`load_i64x4` directly at the three kernel consumption sites.  Native value
aggregates were destructured inside their branch and only scalar IDs crossed
to the host-oracle branch; the strict closure initially caught and prevented a
`ValueClassType -> ClassType` join.  Final focused gates were 399 passed and
the kernel closure added only one int box/unbox call relative to v24.

The structural/host result again looked sound:

```text
parsed call tuples                         194,908 -> 0
all parsed payload tuples                  211,961 -> 17,053
host instructions                  106.140/106.507B controls
                                     105.903B candidate
host footprint                       ~219MB -> 194MB
assembly                              exact in every arm
```

But the source-frozen v25 Stage1 is decisive negative evidence.  With the
same runtime archive and build settings as v24, it entered final linking only
after about 387 seconds and timed out without a compiler or receipt at the
420-second inner watchdog.  The accepted v24 completed the whole build,
linkage checks and receipt in 305.40 seconds.  Widening the timeout would only
hide a >=38% Stage1 regression and cannot satisfy the user's Stage1/Stage2
goal.

This direct-batch retry is `[DENIED]` and removed.  The three compiler sources
are byte-identical to accepted v24 and the post-removal focused packet is 397
passed.  Both attempted intermediate-scratch shapes are now exhausted:
method-based v22 regressed item311 15.7%, while direct-batch v25 regressed
Stage1 before item311 could be measured.  A future parser call plane must
eliminate the intermediate scratch-to-kernel translation itself (publish the
final kernel layout or fuse construction), not tune the adapter around that
translation.

## Indexed-kernel definition/use phase split

The two failed parser-call builders exposed a second constraint: adding the
construction adapter directly to the already large
`IndexedFunctionKernel.__init__` produced non-linear pcc-compiled cost.  The
retained v26 slice moves the existing definitions-first and uses-second walks
into exact `IndexedFunctionKernel` methods and moves their analysis imports to
module scope.  ID assignment, malformed-SSA preservation, phi-edge use
positions, call/fixed/GEP publication, freeze order and every stored payload
remain unchanged.  This is a control-flow/lifetime split, not another record
representation.

Host evidence was exact and positive: the frozen item311 bracket emitted
`ff943e10...` in every arm, with 103.409B candidate instructions versus
106.136/106.289B controls and 192.8MB candidate footprint versus
218.3/217.2MB controls.  The focused self-backend/native-plane packet remains
397 passed.

The source-frozen strict no-libpython/self Stage1 completed successfully:

```text
v26 pcc1 SHA-256       faffe09c1e8f5b065f60b894c5c598e7e412dc5009ad40bacd8dcd89b98083ea
v26 Stage1             311.15s / 306.690B instructions / 1.639GB footprint
v24 Stage1             305.40s / 306.756B instructions / 1.639GB footprint
linkage                 libSystem only; no libpython or LLVM
```

The adjacent pcc1 item311 bracket is:

```text
                         wall       CPU       instructions     footprint
v26 candidate A          33.20s     31.64s    412.712B         3.541GB
v24 control              31.49s     31.32s    415.912B         3.617GB
v26 candidate B          33.95s     32.86s    412.522B         3.541GB
assembly                  ff943e10... exact in every arm
```

The candidate's repeatable instruction reduction is 0.77--0.82% and its
footprint reduction is 2.09%; wall/CPU varied with load and is not claimed as
a speedup.  Stage1 instructions are neutral.  This slice is `[CONFIRMED]` and
retained as a structural improvement: it shortens the native lifetime of one
giant function and provides bounded phase methods in which a future parser can
publish the final kernel call layout without a scratch-to-kernel copy.  It does
not remove any of the remaining parser tuples, protocol-block root objects or
temporary liveness sets, so projection closure and whole Stage2 remain open.

## Parser publishes the final indexed call plane

The two denied call-scratch designs copied a parser arena into the final
kernel arena.  The retained design has no intermediate representation:
`IndexedCallPlane` writes the kernel's exact two-record call header/span and
four-i64 argument layout while parsing.  Destination and argument text IDs
occupy their final scalar fields only until the definitions/uses phases resolve
them in place to dense value IDs.  `IndexedFunctionKernel` then takes ownership
of those same arena/text/type objects by identity and drops the construction
intern indexes.  The legacy tuple is created only through the explicit
diagnostic projector.

The first ordered test packet caught an `id()` cache lifetime defect in the
new type construction index: an equal temporary TypeDesc was not kept alive,
so address reuse could false-hit a different type.  Entries now retain and
identity-check the exact key object.  The dedicated regression verifies the
key lifetime as well as arena identity across the parser/kernel handoff.

Frozen item311 inventory
`build/native-data-plane-record-inventory-v101-final-call-plane.json` now
reports:

```text
reachable call instructions / packed parse payloads       46,225 / 46,225
call payload tuple references                           194,908 -> 0
all parse payload tuple references                      211,961 -> 17,053
parsed reachable objects                                258,157 -> 65,864
normal call/instruction/type projections verified->emit              0
```

The shared plane currently retains 1,005 calls from blocks filtered as
unreachable after parsing (47,230 stored records versus 46,225 reachable
instructions).  They are unused final-layout records, not a copied scratch,
but remain an explicit construction-memory boundary.  Avoiding them requires
terminator/reachability parsing before instruction payload publication; no
compaction/copy was added merely to improve the count.

Correctness/closure evidence:

```text
focused parser/kernel/verifier/stackmap/AArch64 packet   351 passed, 1 deselected
known deselection on frozen v24 control                  same cold-layout legacy-list failure
strict self/no-libpython closure                         ir / parser / kernel passed
runtime selector closure and archive-owner gate          5 passed
item311 host/internal/pcc1 assembly                       ff943e10... exact
```

The current-source runtime first had to be rebuilt from one compiler closure;
all 186 object receipts now share codegen checksum `77ca361c...`.  The separate
freestanding selector registry repair is recorded in
`2026-08-28-backend4-selector-counter-freestanding-registry.md`.  Candidate
v29 and control v30 use that identical runtime archive and their source
manifests differ only in `self_backend_ir.py`, `self_backend_parse.py` and
`self_backend_kernel.py`.

```text
                              v30 control       v29 final call plane
Stage1 wall                   260.17s           257.16s
Stage1 CPU                    1091.76s          1069.22s
Stage1 instructions           305.901B          306.429B  (+0.17%)
Stage1 footprint              1.622GB           1.640GB   (+1.1%)
pcc1 item311 wall             29.70s            29.64 / 29.81s
pcc1 item311 CPU              29.66s            29.58 / 29.75s
pcc1 item311 instructions     410.870B          413.603 / 413.822B
pcc1 item311 footprint        3.541GB           3.357 / 3.357GB
assembly                      ff943e10...        ff943e10... exact
```

The worker instruction cost is a stable 0.67--0.72%, while footprint falls
5.21% and wall/CPU are neutral.  This is `[CONFIRMED]` and retained as a real
end-to-end object-projection deletion with material memory benefit, not as a
speedup claim.  The remaining 17,053 parser tuples are 4,392 GEP, 4,237 store,
3,697 load, 2,062 cast, 1,506 icmp, 898 alloca, 255 binop and 6 select records.
The next coherent slice is the fixed-instruction family already consumed by
the kernel's final packed records; it must use the same direct-publication
shape rather than another adapter.

## Fixed-instruction direct publication denial

The next experiment extended the final parser-owned plane to load, store,
cast, icmp, binop and select.  It wrote the existing four-i64 final records
directly, resolved destination/operand text to dense IDs in place and retained
volatile/arithmetic flags plus the explicit diagnostic projection.  No
intermediate tuple or second record arena existed.  A six-kind regression
proved arena identity, use counts, result types and exact diagnostics before
and after kernel adoption; the focused packet was 352 passed (plus the same
frozen-v24 legacy cold-layout deselection), and all three strict closures
passed.

The structural result was exact:

```text
parse packed payloads                 46,225 -> 57,988 of 59,984
parse payload tuples                  17,053 -> 5,290
remaining tuples                      GEP 4,392 + alloca 898
host item311 instructions             104.338B control
                                      100.071 / 100.438B candidate
host footprint                        216.7MB -> 191--195MB
assembly                              ff943e10... exact
```

The self-compiled result reversed the host win.  Candidate v33 and control v34
used the same `03e12ac9...` runtime archive; their source manifests differed
only in the three compiler files:

```text
                              v34 call-plane control   v33 fixed direct plane
Stage1 wall                   257.48s                  312.19s
Stage1 CPU                    1082.82s                 1260.11s
Stage1 instructions           305.407B                 307.460B (+0.67%)
Stage1 footprint              1.650GB                  1.651GB
pcc1 item311 wall             29.77s                   29.98 / 30.05s
pcc1 item311 CPU              29.65s                   29.93 / 30.00s
pcc1 item311 instructions     414.199B                 418.491 / 418.358B
pcc1 item311 footprint        3.357GB                  3.321 / 3.321GB
assembly                      ff943e10...               ff943e10... exact
```

The repeatable ~1.0% worker instruction/CPU regression is not justified by a
1.1% footprint saving, and Stage1 also regresses 0.67% in instructions with a
large wall/CPU warning.  This proposal is `[DENIED]` and removed.  The three
compiler files are byte-identical to the v34 accepted call-plane control, 351
focused tests pass again, and the rebuilt 186-object runtime archive is back
to the accepted single checksum `77ca361c...`.

The denial is about this implementation shape, not about leaving the 11,763
tuples forever.  Matched 15-second caller profiles (11,538/11,540 samples)
place the regression in parsing: candidate `parse_self_backend_module` grows
to 974 inclusive samples from 207, while kernel construction falls to 1,319
from 1,476 (`_index_function_uses` is 280 versus 270).  Thus the dominant loss
is the parser plane's ordinary method/intern/arena traffic, not the later
per-use resolution branch.  A retry must arrange dense IDs in one
definitions-first parse/build pass without one Python method chain per fixed
record; applying the denied shape to smaller GEP/alloca families has no
supporting ceiling.

## Entry-state identity reuses validated locations

The accepted stackmap profile still put 5,140/11,540 samples under
`build_function_stack_map_plan`, led by `_block_entry_states` (931) and
`add_record` (871).  No.76 already makes ordinary blocks share immutable entry
tuples, but the first safepoint of every block still walked every group to
rebuild No.75's XOR content fingerprint.

Sizing in
`build/native-data-plane-record-inventory-v109-entry-identity-cache-sizing.json`
proved the reuse boundary:

```text
block entry references                         9,474
entry tuple identities                           854
identity hits / misses                       8,620 / 854
group refs scanned on misses                  146,307
group scans avoided                         1,479,955 (91.0%)
```

The retained cache maps `id(active_groups)` to `(active_groups,
validated_locations)`.  The stored exact tuple keeps the key alive and a hit
requires `candidate[0] is active_groups`; a miss still uses the existing XOR
fingerprint plus group-by-identity collision validation.  This is an exact
identity fast path over No.75/76, not a replacement for their content oracle.

Correctness and host evidence:

```text
focused parser/kernel/stackmap/AArch64 packet     351 passed, 1 known deselected
strict self/no-libpython stackmap closure          passed
host item311 instructions                          104.624B control
                                                   100.331 / 100.267B candidate
host footprint                                     219.0MB -> 190.8 / 192.6MB
assembly                                           ff943e10... exact
```

Candidate v35 and control v36 share one 186-object `351e9642...` runtime and
their source manifests differ only in
`self_backend_precise_stackmaps.py`.  The first candidate Stage1 wall was a
load/order outlier; the B-A repeat resolves it:

```text
                              v35 candidate A   v36 control   v37 candidate B
Stage1 wall                   324.07s           255.35s       250.84s
Stage1 CPU                    1408.52s          1068.33s      1061.45s
Stage1 instructions           306.461B          306.396B      306.145B
Stage1 cycles                 77.953B           77.781B       76.353B
Stage1 footprint              1.649GB           1.633GB       1.645GB
pcc1 item311 wall             29.62 / 29.53s    29.62s
pcc1 item311 CPU              29.59 / 29.49s    29.57s
pcc1 item311 instructions     411.507 / 411.455B 414.191B
pcc1 item311 footprint        3.357GB           3.357GB
assembly                      ff943e10... exact in every arm
```

The item311 instruction reduction is stable at 0.65%, with wall/CPU/footprint
neutral; Stage1 deterministic counters are neutral and its repeat is not
slower.  This slice is `[CONFIRMED]` as a small indexed-state reuse win, not a
material wall-speedup claim.  The accepted representative envelope is now
approximately 411.5B instructions / 3.357GB.

## Entry identity also reuses active offset sets

`active_offsets_for_version` still rebuilt a Python set by flattening every
active group's locations at the first safepoint of each block.  The exact same
entry tuple identity reuse applies.  A second identity-checked cache retains
`(active_groups, readonly_offsets)`; a hit requires exact tuple identity and a
miss executes the unchanged set comprehension.  Protocol mutation produces a
new tuple and therefore conservatively misses.

Correctness and host evidence:

```text
focused parser/kernel/stackmap/AArch64 packet     351 passed, 1 known deselected
strict self/no-libpython stackmap closure          passed
host item311 instructions                          103.426B control
                                                   99.571 / 99.808B candidate
host CPU                                           6.14s -> 5.75 / 5.78s
host footprint                                     218.3MB -> 209.0 / 210.2MB
assembly                                           ff943e10... exact
```

Candidate v38/control v39 share the same 186-object `d6ddcaa2...` runtime and
their source manifests differ only in the precise-stackmap file:

```text
                              v39 locations-only control   v38 + offsets cache
Stage1 wall                   249.80s                       251.89s
Stage1 CPU                    1052.99s                      1066.47s
Stage1 instructions           305.852B                      306.234B (+0.12%)
Stage1 footprint              1.678GB                       1.647GB (-1.9%)
pcc1 item311 wall             29.53s                        28.62 / 28.54s
pcc1 item311 CPU              29.50s                        28.57 / 28.51s
pcc1 item311 instructions     411.882B                      400.736 / 400.570B
pcc1 item311 footprint        3.357GB                       3.149 / 3.149GB
assembly                      ff943e10... exact in every arm
```

This is a repeatable 2.7% instruction, 3.2--3.4% CPU/wall and 6.2% footprint
worker win.  Stage1 has a small 0.12% instruction/1% CPU warning but lower
memory and remains in its non-regressed wall envelope.  `[CONFIRMED]` and
retained.  The representative worker envelope is now approximately 400.6B
instructions / 3.149GB.

## Safepoint IDs feed numeric suffixes from direct 32-bit prefix limbs

The post-offset-cache profile put 305 samples under `safepoint_id`, which built
`str(ordinal)`, `str(kind)`, a joined string and UTF-8 bytes for every one of
20,004 records.  The historical packed-prefix/resume optimization was already
denied because it created suffix objects and crossed pcc1's bignum projection.
The new hot function instead feeds the exact NUL separators and ASCII decimal
digits numerically into the two FNV limbs.

The first implementation still obtained those limbs by packing the prefix
into one unsigned 64-bit Python int and then shifting/masking.  Host equality
tests passed and host item311 improved strongly, but v40 pcc1 changed every
stackmap record ID and emitted assembly `19c2bd0a...`.  The first wrong ID was
`2289764854631723059` versus `1044709287126174772`; reversing its FNV suffix
proved the prefix reaching the feeder was `0x8000000000000000` or zero, not the
correct `0xb126e0c5b55a8bf7`.  This directly confirmed the known packed-int
projection failure rather than a decimal-feeder error.

The retained implementation computes high and low independently, twice per
function, with every intermediate below 2**42.  No packed 64-bit prefix crosses
the call boundary.  The bit-identity test covers several symbols/ordinals and
all safepoint kinds against both the public one-shot hash and string resume.

```text
focused parser/kernel/stackmap/AArch64 packet     351 passed, 1 known deselected
strict core + self planner no-libpython closures   passed
host item311 instructions                          102.605B control
                                                   91.514 / 91.505B candidate
host CPU                                           6.12s -> 5.30 / 5.45s
host footprint                                     239.6MB -> 212.0 / 210.1MB
host assembly                                      ff943e10... exact
```

Candidate v42/control v43 share the same 186-object `871535bf...` runtime and
their manifests differ only in the core/planner files:

```text
                              v43 offsets-only control   v42 numeric ID suffix
Stage1 wall                   320.77s                    286.65s
Stage1 CPU                    1243.29s                   1126.07s
Stage1 instructions           306.327B                   306.266B
Stage1 footprint              1.644GB                    1.627GB
pcc1 item311 wall             30.23s                     29.43 / 29.34s
pcc1 item311 CPU              29.84s                     29.31 / 29.25s
pcc1 item311 instructions     401.410B                   396.932 / 396.793B
pcc1 item311 footprint        3.149GB                    3.144 / 3.144GB
assembly                      ff943e10... exact in every retained arm
```

The corrected form is `[CONFIRMED]`: 1.1--1.15% fewer worker instructions,
about 2% less CPU and 3% less wall, with neutral memory and non-regressed
Stage1.  The representative envelope is now approximately 396.8B / 3.144GB.
The packed-prefix split form remains `[DENIED]` even though its host numbers
looked identical.

## Reachability precedes instruction-payload publication

The parser previously decoded every block's instruction payload and wrote all
calls into one final call plane, then filtered unreachable CFG blocks.  On
item311 that retained 1,005 unreachable call records and 1,127 argument
records in the live function plane.  The parser now has an explicit structural
phase: parse phi/terminator CFG, filter reachability, then publish instruction
payloads only for reachable blocks.

Fail-closed behavior is unchanged.  Unreachable CFG instruction text still
passes through a cold diagnostic parser and an unsupported opcode still raises
the established capability error; it simply does not publish a persistent
record.  Dedicated regressions prove both dead-call deletion and dead
unsupported-op rejection.

Inventory
`build/native-data-plane-record-inventory-v118-reachability-first-parse.json`
records:

```text
call records                         47,230 -> 46,225
call arguments                       57,360 -> 56,233
reachable call records                         46,225
legacy call projections                             0
```

Correctness and host evidence:

```text
focused parser/kernel/stackmap packet    353 passed, 1 known deselected
strict parser no-libpython closure        passed
host item311 instructions                 94.555B control
                                          91.473 / 91.462B candidate
host footprint                            236.5MB control
                                          211.5 / 206.3MB candidate
assembly                                  ff943e10... exact
```

Candidate v44/control v45 share the same 186-object `75a4206d...` runtime and
their source manifests differ only in the parser file:

```text
                              v45 parse-all control   v44 reachability first
Stage1 wall                   267.53s                 310.03s
Stage1 instructions           306.802B                306.791B
Stage1 footprint              1.645GB                 1.643GB
pcc1 item311 wall             32.14s                  29.62 / 30.52s
pcc1 item311 CPU              29.94s                  29.44 / 29.51s
pcc1 item311 instructions     397.598B                396.215 / 396.486B
pcc1 item311 footprint        3.144GB                 3.145 / 3.146GB
assembly                      ff943e10... exact in every arm
```

The Stage1 wall difference is load/order noise: deterministic Stage1
instructions and footprint are neutral.  The representative pcc1 worker
improves by 0.28--0.35% instructions and 1--2% CPU while deleting unreachable
persistent records.  `[CONFIRMED]` as a necessary phase boundary and small
measured win, not as a material Stage1 speedup.

## Definitions-first construction deletes the supported payload object plane

A parser-owned `IndexedFunctionSeed` now assigns block/value IDs before
reachable instruction publication and writes final call/fixed/GEP/alloca
payload arenas.  Kernel construction adopts those arenas and generates shared
use/last-use facts once; the supported item311 path no longer calls the legacy
payload parser.

The retained block cursor is parser-local.  Under pcc1, an empty metadata
arena had real `_length == 0` while `len(arena)//4` crossed the module boundary
as 1; a replacement seed counter was then observed stale at 0 after its owner
advanced to 1.  The local cursor fixes both while retaining the per-arena order
assertion and final whole-function count assertion.  Multi-block and
call-plus-multi-block pcc1 canaries are byte-identical to host.

Durable inventory
`build/native-data-plane-record-inventory-v137-canonical-type-keys.json`:

```text
supported instructions                            59,984 / 59,984 packed
payload tuple / `_data` list references            17,053 / 9,474 -> 0 / 0
parsed lists                                       27,945 -> 29
parsed tuples                                      58,439 -> 10,085
parsed reachable objects                         117,703 -> 39,440
parsed / verified TypeDesc identities          2,066 / 973 -> 74 / 74
normal call/instruction/type projections verify->emit                 0
```

Block/value construction buckets and id-cache tuples are replaced by
pre-sized `CompilerIntArena` open-address indexes adopted by the kernel.
Equal but noncanonical TypeDesc wrappers are not entered into an id cache at
all; canonical objects retain their exact identity entry, so address reuse
cannot false-hit.

Current-source correctness evidence is 518 focused passes (one frozen-control
deselection), strict IR/parser/kernel no-libpython closures, real self emission
of those closures, exact host item311 and exact pcc1 canaries.

### pcc1 performance remains open

The first correct per-operand construction form was denied at 446.209B
instructions / 3.137GB / 33.41s CPU.  One kernel integer use scan recovered
431.155B; native name indexes reached 429.991B; an exact
`IndexedCallPlane.append_parsed_call` interface repeats at 420.337B and
419.950B, 3.131GB and 30.97s CPU, with exact assembly.  This is a real 5.9%
recovery and a 63% object deletion, but it remains roughly 6% instruction-
negative versus frozen controls at ~396.5--397.4B / 3.145GB.  Stage1 is also a
warning at 310.525B/1.660GB versus v44's 306.791B/1.643GB.

Full-lifetime (process-exit, not fixed-25-second) profiles localize the excess:

```text
                                             v44 control    v57 candidate
total on-CPU samples                           28,035          30,137
prepare_module_for_target                      11,218          12,387
parse_self_backend_module                       5,278           8,734
verify_parsed_module                            5,554           3,307
emit_prepared_aarch64_module                   16,700          17,592
emit_function                                   8,997           9,745
```

Parser/kernel fusion is working, but prepare remains +1,169 samples and final
emit +892.  Removed `[DENIED]` variants include per-record use bookkeeping
(446.2B), payload-only construction (430.5B), base-class final-mode publication
(435.0B), optional cross-module arena fields (call canary failures), and exact
arenas threaded through every call-parser frame (Stage1 exceeded 420s without
an artifact).

The retained source is v57's exact call interface plus native name indexes and
canonical-only TypeDesc identity keys.  The final TypeDesc-only refinement has
host/structural evidence but no new pcc1 receipt.  Terminator/phi projection,
the remaining pcc1 regression, Stage2 and GC0..4 fixed points remain open.

## Follow-up — indexed verifier receipt and denied terminator/PHI handoff

The canonical-TypeDesc, whole-function-sized value index and indexed
terminator verifier source was rebuilt as candidate v61.  Its pcc1 is
`461ad821...`; item311 emitted exact `ff943e10...` assembly at
419.498B instructions and 3.132GB footprint.  This closes the previously
missing pcc1 receipt, but still leaves about a 5.9% instruction regression
against the frozen v44 396.215/396.486B controls.

A follow-up tested whether moving terminator and PHI publication into the
definitions-first seed would remove the remaining prepare cost.  The seed
owned the final scalar arenas, the kernel adopted them without rebuilding,
and the AArch64 hot preparation path released valid scalar object projections
before verification.  The structural result was large:

```text
item311 parsed/verified tuples       10,085 -> 77
parsed reachable objects            39,440 -> 19,167
assembly                              d167ea28... exact in the inventory
focused verifier/parser/kernel       423 passed, 1 frozen-control deselected
```

The source-frozen v62 build used the same `cd32acd...` runtime and completed
Stage1 at 280.73s / 310.438B instructions / 1.664GB footprint.  Its pcc1
`dac24194...` emitted exact `ff943e10...` item311 assembly, but measured
32.44s wall / 31.66s CPU / 423.326B instructions / 3.131GB footprint.  That is
0.91% more instructions than v61 and still about 6.8% above v44.

`[DENIED]`: retaining the ordinary terminator/PHI construction and then
crossing an additional parser-to-seed publication interface deletes the live
object graph but does not delete its construction cost.  The full v62 source
slice was removed with narrow patches; kernel, parser, prepare and verifier
then matched the frozen v61 source hashes exactly and the restored focused
packet passed 30/30.  A future terminator/PHI proposal must parse directly
into final IDs/spans or prove an explicitly cold diagnostic projection; it
must not repeat the construct-then-handoff shape.

## Follow-up — one kind mirror retained, duplicate flag mirrors removed

The function-wide metadata arena is authoritative for instruction kind,
payload, volatile and arithmetic-flag bits.  Definitions-first block arenas
were nevertheless appending all three one-byte shape columns again.  Removing
the kind byte too was denied by a host probe (92.107B instructions) because
stack preparation then had to read a full metadata aggregate per instruction.
The retained form keeps the one-byte block-local kind traversal index and
projects volatile/arithmetic flags lazily from metadata only for diagnostics;
it deletes two bytearray appends and two persistent bytes per instruction.

The 422-test focused packet passed with one frozen-control deselection, strict
pcc1 closure passed for every independently supported changed module, and
inventory retained 59,984 packed payloads with zero normal projections and
exact output.  Source-frozen v63 used runtime `cd32acd...`:

```text
Stage1                         335.27s / 310.152B / 1.657GB
pcc1 item311 A                30.91s / 418.638B / 3.111GB
pcc1 item311 B                31.10s / 418.559B / 3.111GB
assembly                       ff943e10... exact
```

This is `[CONFIRMED]` as a small structural win: 0.20--0.22% fewer item311
instructions and about 0.67% less footprint than v61, with a slight Stage1
instruction reduction.  It is not presented as a material speedup; the
remaining gap to v44 is still about 5.6%.

## Follow-up — destination-fact prepublication is denied

A definitions pass then attempted to publish each instruction destination
once so call/fixed parsing could avoid a second name extraction and native
index lookup.  The direct parser access built v64 at 309.817B Stage1
instructions, but every pcc1 self emit failed even on a 114-byte single-block
IR at `prepare begin` with `append4`.  Host and closure compilation were green;
the failure was the cross-module ordinary-object projection of
`indexed_seed.instruction_facts`.

Wrapping the write in exact `IndexedFunctionSeed.append_definition_fact`
fixed that pcc1 canary.  Source-frozen v65 completed Stage1 at
310.209B / 1.657GB and emitted exact item311 assembly at
419.236B / 3.107GB / 31.03s CPU.  It is `[DENIED]`: footprint improved only
about 4MB while Stage1 and item311 instructions were both worse than correct
v63.  Both destination variants and their temporary tests were removed; all
five affected compiler files hash-match the frozen v63 snapshot.

One final implementation removed v65's added per-record seed method without
threading arenas through the call parser: `_parse_blocks` bound the seed facts
arena to a statically annotated local and performed the same `append4` calls
that v63 performs later.  Source-frozen v66 completed Stage1 at
309.954B / 1.662GB, but the 114-byte canary crashed with exit 139 immediately
after `prepare begin`.  No item311 run was attempted.  `[DENIED]`; this closes
the destination-prepublication direction, including direct dynamic access,
exact seed method and exact local-arena variants.  Retained source again
hash-matches frozen v63.

## Follow-up — inlined call publication retained; adapter work moves to a prerequisite

V67 inlines the call-specific facts/metadata/kind publication inside the exact
seed override instead of nesting the generic instruction appender.  The
focused packet and 114-byte canary pass, and source-frozen results repeat:

```text
Stage1                         281.57s / 310.085B / 1.660GB
pcc1 item311 A                31.21s / 418.176B / 3.111GB
pcc1 item311 B                31.18s / 418.108B / 3.111GB
assembly                       ff943e10... exact
```

This is `[CONFIRMED]` as a small deterministic simplification, about
0.09--0.11% fewer worker instructions than v63.  A correctly early-attached
full-lifetime profile records 28,744 samples and moves the remaining delta
entirely into prepare:

```text
                                 v44       v67
prepare                         11,218    12,608
parse                            5,278     8,922
verify                           5,554     3,363
final emit                      16,700    16,006
emit_function                    8,997     8,485
```

The current worker gap to v44 is therefore about 5.53% in instructions and is
no longer an emit/stackmap owner.  The remaining call adapter is 943 samples.

Three attempts to remove that adapter are denied.  Splitting final call
publication into base append + two-argument finalizer (v68) re-interned call
destinations and regressed item311 to 434.749B.  A seven-field pointer-bearing
valueclass aggregate (v69) kept exact output but measured 314.155B Stage1 and
418.290B item311, both worse than v67.  Exact receiver typing after breaking
the parser dependency cycle improved Stage1 counters (v70 309.409B; v71 with
slot annotations 309.097B) but both compilers fail the 114-byte canary with
`append4`.  All three shapes were removed and the source hash-restored to v67.
The exact-field failure now lives in
`pcc1-exact-compiler-arena-field-method-abi.md`; backend helper variants are
closed until that generic prerequisite is resolved.

## Operational cleanup

With explicit human authorization, regenerated caches and superseded build
artifacts were removed after source-frozen evidence was recorded.  Shared
`~/.cache/pcc` fell from 84GB to 4KB and `build/` from 166GB to 60GB, releasing
about 190GB total.  V44/v57/v61/v63/v67, current Stage2 inputs/profiles, the
formal GC0 fixed point, five-GC evidence and all docs remain.  Future cache
temperature timings require a new baseline; the retained instruction counters
and frozen binaries are unaffected.
