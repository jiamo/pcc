# Native data-plane object-projection closure

Date: 2026-08-29  
Task: `PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE`

## Frozen identities

The accepted GC0/self/no-libpython source and runtime are frozen under
`build/native-data-plane-stage1-candidate-v79-hoist-cache-identity/`:

```text
bootstrap source SHA-256  0e1a7a69467399043da7a8fbccc9c4a812b5aa0c4d07d339f173d497254a4e3f
pcc1 SHA-256              f4c7dc7d1bd5567faa517a21fa1de2a670c6fda117a5186247271ed5f1184174
runtime archive SHA-256   cd32acd72b114d63604f1a7eb2dc9798b4e84121c47bfd955ba0ac159150e64e
build receipt SHA-256     1a051fe0c4134b2c1d6bf0a2cf36e155e8cce150a1bd7afbc935eab9f2337363
```

V79 differs from V78 only at the hoist-cache correctness boundary after the
native-data-plane source was frozen: every `id(fd)`-keyed hoist cache now
retains the exact AST owner and requires identity on a hit.  This prevents a
released/reused AST address from assigning another nested function's capture
set and makes pcc1/pcc2 agree on stackprep's closure ABI.

## Complete record inventory

The current-source inventory is
`build/native-data-plane-record-inventory-v160-v79-object-closure.json`
(SHA-256 `5fd7d01d...`) over item311 SHA-256 `76af6689...`:

```text
instructions                              59,984
packed instruction payloads               59,984
instruction tuple/list payload refs             0 / 0
call records / call-arg records            46,225 / 56,233
legacy SlotInfo / AllocaInfo projections        0 / 0
legacy value/alloca slot maps                   0 / 0
instruction/call/type projections
  verified, stackprep, stackmap, emitted        0 at every stage

stage                 reachable objects   tuples
parsed                           39,440    10,085
verified                         19,177        77
stack-prepared                      152         1
stackmap-planned                     176         2
```

Every supported instruction, operand, type, value, slot, CFG, def-use,
last-use, liveness, safepoint, reload, register and emission record is owned by
the packed/indexed kernel or packed safepoint plan.  The remaining named
containers are one of:

- construction indexes into final arenas (`instruction_arenas`, name/type
  indexes and slot-offset indexes), not record object projections;
- traced spelling/diagnostic tables (`block_names`, `value_names`, call text,
  labels and exceptional-block spellings);
- host-oracle backing for `CompilerIntArena`; pcc1 uses the raw arena
  projection; or
- explicit parser compatibility projections.  Terminator/PHI objects remain
  honest at the parse boundary, are released after CFG diagnostics, and are
  absent from verifier-success downstream consumers.  The supported AArch64
  stackprep path obtains the compact arena through the kernel index and never
  traverses `func.blocks`; x86 remains the named lazy compatibility adapter.

The inventory assembly SHA is `d167ea28...`; the normal receipt-bound worker
assembly remains the historical exact `ff943e10...`.

## Focused correctness and closure gates

Final-source results:

```text
forced id-collision regressions                       2 passed in 23.21s
complete nested-hoist file                            7 passed in 6.04s
compiled hoist closure/module-boundary packet         5 passed in 16.87s
record inventory/source-shape ratchet                 5 passed in 0.09s
114-byte GC0 pcc1 self-emitter canary                  exit 0
```

Before the fix, the forced-ID test returned `left_capture` for the second
function instead of `right_capture`.  On frozen V78, pcc1 reported 28
free-name analyses / 10 hits and generated stackprep nested `alloc` /
`alloc_value_slot` with 6/10 parameters; pcc2 reported 27/9 and generated the
correct 3/8 ABI.  Repaired host and V79 pcc1 both report 27/9 and 3/8, and
`scripts/pcc_ir_diff.py` reports no structural pcc1/pcc2 stackprep IR
difference.

## Stage1 and representative worker

V79 Stage1 completed with a formal receipt:

```text
wall                 304.15s
instructions         309,665,786,320
peak footprint       1,682,999,048 bytes
linkage               libSystem only
```

Two receipt-bound item311 repetitions are stable and exact:

```text
run   wall    CPU     instructions       peak footprint   assembly
1     26.98s  26.92s  384,016,310,546   3,104,721,584    ff943e10...
2     26.96s  26.94s  384,018,413,032   3,104,803,528    ff943e10...
```

Their manifests have SHA-256 `106adf7d...` and `8a4b7f8b...`.  The result is
inside the accepted V73/V78 383.8--384.2B instruction and 3.105GB footprint
band; the correctness repair introduces no representative-worker regression.

## Complete cold Stage2 and resource receipt

The first current-source cache-off Stage2 completed in 690.571s.  A second
cache-off run under `scripts/run_process_tree_sample.py` is the claim-grade
resource receipt:

```text
Stage2 result             rc=0, 683.796s
process-tree samples      638 at one-second cadence
peak process count        27
peak aggregate RSS        10,856,660,992 bytes
pcc2 SHA-256              1c62b168ff034022f0a28d26086f77cf43dfa17e2e64cb427b483fc37e5b9d46
linkage                   libSystem only
```

The produced pcc2 is byte-identical to the later fixed-point pcc2.  Peak
aggregate RSS is about 756MB (6.5%) below the prior complete process-tree
Stage2 baseline of 11.613GB.  The resource receipt SHA-256 is `74ce9617...`;
the inner Stage2 result receipt SHA-256 is `2cb1efdc...`.

## Sequential GC0 fixed point

The source-frozen chain is under
`build/native-data-plane-v79-gc0-fixed-point-v1/`.

```text
Stage2 cold               690.571s, rc=0
Stage3 warm               211.379s, rc=0
Stage3 object cache       508 hits / 0 misses / 508 objects
pcc2 SHA-256              1c62b168ff034022f0a28d26086f77cf43dfa17e2e64cb427b483fc37e5b9d46
pcc3 SHA-256              1c62b168ff034022f0a28d26086f77cf43dfa17e2e64cb427b483fc37e5b9d46
raw cmp                   equal
pcc3 linkage              libSystem only
```

One preceding Stage3 cache-warming attempt hit its registered 600-second
watchdog after publishing all 508 cold objects and produced no pcc3/result;
it is retained as failed evidence at `stage3.timeout600.live.log` (SHA-256
`b1bb4d47...`).  No success is inferred from it.  The ordinary warm rerun has
the final successful result receipt (SHA-256 `e5ae59dc...`) and raw fixed
point above.  No compiler/bootstrap children remain.

## Supported claim

On frozen Darwin arm64, GC0, self backend and no-libpython, the supported
AArch64 compiler path carries its instruction/operand/type/value/slot/CFG/
analysis/stackmap/reload/register/emission record families through the packed
indexed data plane with zero normal record object projection.  Explicit
construction and diagnostic seams remain classified and exact.  Focused
differentials, strict closure, representative output/performance, complete
cold Stage2 resource evidence, and raw pcc2/pcc3 fixed point all pass.

## Not proven

This does not prove GC1--4 equality, provenance fast paths, parallel emit, or
the global performance goal.  In particular, current cache-off Stage2
683.796s remains about 2.25x V79 Stage1 304.15s.  Those are downstream task
boundaries; they are not hidden by marking this finite representation closure
complete.

## Retraction — transient verifier projection was not counted

The `DONE_STRONG` claim above was retracted later on 2026-08-29.  The inventory
counts objects reachable at named stage boundaries, but the verifier created
and discarded one `_Definition` dataclass per value plus hash-bucket lists
inside the stage.  Item311 has 18,444 values, so those transient allocations
and string-key traversals were invisible in the reported 19,177-object
post-verifier graph.  The kernel already owned definition block/type and every
use as dense value IDs.  The task was reopened to remove this redundant
projection; the fixed-point and resource receipts above remain valid evidence
for V79, but they do not prove zero transient materialization.
