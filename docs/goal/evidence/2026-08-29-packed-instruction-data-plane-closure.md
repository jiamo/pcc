# Packed instruction data-plane closure

Task: `PERF-P0-PACKED-INSTRUCTION-DATA-PLANE`

## Accepted source

Accepted source and compiler are v73:

- bootstrap source `d1b50f5990907e939bccf93ef050cd53874f33c9792f0796c8f3649b55ce2715`
- pcc1 `650169b5028a46cccb1bdb58375737a91489d1dd0621129c2b1e5e728a7ae06f`
- item311 input `76af6689f079d29a5965733c4e7b365c9d4a8ccc16d0ce8a70e21fea6b65468c`

## End-to-end packed plane

Current item311 inventory records all 59,984 supported instructions in the
final kind-specific planes:

```text
call       46,225
alloca        898
load        3,697
store       4,237
cast        2,062
icmp        1,506
binop         255
select          6
gep          1,098
total       59,984
```

Parser-owned call/fixed/GEP arenas are adopted by the one
`IndexedFunctionKernel`; opcode IDs, result/value/type IDs, operand spans,
definition/use facts and arithmetic metadata remain indexed through verifier,
stackprep, precise stack maps, register allocation, target passes and AArch64
emission.  Inventory reports zero instruction tuple/list references and zero
instruction/call/type/slot projections at verified, stack-prepared,
stackmap-planned and emitted boundaries.

Spelling/type side tables are canonical and traced.  Supported diagnostics use
explicit lazy `diagnostic_*` projection counters; unsupported/cold kinds alone
use `cold_instruction_data`.  Ordinary Python object/container semantics are
unchanged.

## Performance and output

V73 repeats item311 at 384.116/384.247B instructions, 28.38/28.39s CPU and
3.105GB footprint with exact
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`
assembly.  This is about 8.1% fewer instructions than retained v67 and about 3%
below the pre-migration v44 instruction envelope.  V73 Stage1 is 308.470B
instructions and links only libSystem.

Earlier generic tagged/per-field/raw/getter designs remain denied at
49.30–82.33s and 675B–1.130T instructions; current success comes from
kind-specific final records and shared facts, not renaming generic containers
or adding another getter layer.

## Gates

- restored self-backend/inventory/stackmap packet: 547 passed, one frozen-v73
  stale control deselected after direct baseline reproduction
- annotation/schema and cross-module ABI packets: 37 and 68 passed
- strict self/no-libpython closures passed for every independently supported
  accepted backend module
- exact 114-byte pcc1 canary and repeated receipt-bound item311 passed
- bootstrap baseline and complete sharded fallback/IR ratchets passed

## Deferred whole-stage claim

This closes the packed-instruction implementation, not the full native-data-
plane program.  Same-source whole Stage2 and the GC0 fixed point are retained
as required final gates of
`PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE`; GC1..4 remain in the
dedicated final transfer row.  This preserves the human-selected native-first,
concentrated-validation order without deleting any overall obligation.
