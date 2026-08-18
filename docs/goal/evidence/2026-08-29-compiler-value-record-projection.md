# Compiler value-record projection closure

Task: `PERF-P0-COMPILER-VALUE-RECORD-PROJECTION`

## Accepted source and scope

The accepted source remains frozen v73:

- bootstrap source: `d1b50f5990907e939bccf93ef050cd53874f33c9792f0796c8f3649b55ce2715`
- pcc1: `650169b5028a46cccb1bdb58375737a91489d1dd0621129c2b1e5e728a7ae06f`
- Stage1: 299.28s / 308.470B instructions / 1.665GB footprint
- item311: 384.116/384.247B instructions, 28.38/28.39s CPU,
  3.105GB footprint, exact `ff943e10...` assembly

This evidence closes the finite semantic-type-to-value-projection task.  It
does not claim that every parser construction object is gone; that stronger
parse-to-emit deletion claim is owned by
`PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE`.

## Record contracts

| Semantic record | Authoritative value projection | Object/diagnostic projection | Escape rule |
|---|---|---|---|
| `TypeDesc` | dense type IDs, `type_scalars`, `type_field_ids`, pointer-type IDs | one canonical object per structural type in the traced `types` side table; 74 on item311 | `type_desc(id)` only at explicit diagnostic/unsupported seams; hot consumers use `type_header`/field IDs |
| value/definition/use | value IDs plus two `value_scalars` records, packed use IDs and overflow span | canonical value-name text side table | names project only for diagnostics/symbol emission; analysis uses IDs |
| slot/alloca | `slot_scalars`, value→slot ID, offset/type ID, alloca offset/type ID | lazy `SlotInfo`/`AllocaInfo` compatibility constructors | supported AArch64 path records zero legacy projections; direct legacy API calls increment the projection counter |
| call/fixed/GEP instruction | parser-owned final scalar arenas adopted by the kernel | lazy exact instruction/call diagnostic projection | unsupported/cold instruction kinds alone enter `cold_instruction_data` |
| terminator/PHI | kernel `terminator_scalars`, case spans, `phi_scalars`, incoming spans and block facts | parser construction compatibility objects plus lazy kernel diagnostic projection | objects are dropped before stackprep; every verifier/stackprep/AArch64 supported consumer uses the indexed plane |

The last row is an explicit construction boundary, not a hidden hot consumer.
A fused block parser may remove it later, but another per-record publication
interface is denied by measurement below.

## Direct-terminator denial

V74 tested a true parser-to-final terminator plane, not v62's
construct-then-handoff.  It removed every one of item311's 9,474 terminator
objects and moved parsed/verified tuples 10,085 -> 611 and reachable objects
39,440/39,460 -> 20,496/20,512.  Normal diagnostic projections were zero and
assembly stayed exact.

The execution result was negative:

| Source | Stage1 instructions | item311 instructions | CPU | Footprint |
|---|---:|---:|---:|---:|
| v73 accepted | 308.470B | 384.116/384.247B | 28.38/28.39s | 3.105GB |
| v74 direct terminator | 309.859B | 393.849/393.704B | 30.81/29.57s | 3.158GB |

V74 regresses item311 instructions about 2.46% and footprint about 1.7%.
`[DENIED]`; all seven affected compiler sources were restored byte-for-byte to
v73.  PHIs have only 267 records and a still smaller ceiling, so no analogous
publication interface is authorized.  A future attempt must fuse block parsing
and remove work, not merely move records.

## Correctness and closure evidence

- exact-field/optional receiver regression: 5 passed
- annotation/schema: 37 passed
- multi-file/class ABI: 68 passed
- restored self-backend packet: 547 passed, one stale control deselected after
  proving it fails identically on frozen v73
- strict self/no-libpython closures passed for the accepted backend modules;
  x86 standalone retains the same unrelated `lane` join failure as frozen v73
- bootstrap baseline: 2 passed, 2 deselected
- complete fallback baseline covered by final-summary shards: 34/34; IR
  fallback baseline: 8/8
- v73 links only libSystem and passes the 114-byte self-emitter canary

## Claim and deferred gates

The supported hot consumers now operate on value IDs, type IDs, slot IDs and
packed aggregate/span records; ordinary class semantics and arbitrary-precision
Python integers are unchanged.  V73 is the accepted source for the next native
data-plane tasks.

Whole Stage2/Stage3, the GC0 fixed point and GC1..4 equality are deliberately
not claimed here.  The human-selected execution order defers those expensive
gates until the implementation freezes.  Their obligations remain explicit in
`PERF-P0-NATIVE-DATA-PLANE-OBJECT-PROJECTION-CLOSURE` (GC0 fixed point) and
`PERF-P0-NATIVE-DATA-PLANE-GC1-GC4-TRANSFER` (the final five-GC matrix); no gate
is deleted from the overall goal.
