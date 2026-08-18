# Packed-instruction raw-arena proposal denial — 2026-08-28

Claim level: source-frozen pcc1 representative-worker denial. This is not a
Stage2 result and does not weaken the active packed-instruction/native-data-
plane requirement.

## Proposal tested

Replace each persistent `ParsedInstr.data` tuple with one module-owned tagged
integer arena, dense opcode IDs, integer operand/sequence spans, and traced
text/`TypeDesc` side tables. Verifier, stackprep, precise stack maps, AArch64
target planning, register allocation, and emission consumed the arena through
per-field getters. A second version replaced bound getters with module-level
static getters while retaining the same per-field access pattern.

Both versions kept Python semantics unchanged, retained an explicit
diagnostic/unsupported adapter, reported zero diagnostic materializations and
zero unsupported payloads on item311, emitted exact assembly SHA-256
`ff943e10afe802c44faff43146a67b56735cd74bb6f1d79db1d8251cfe8f7251`,
and produced strict no-libpython/self pcc1 binaries linked only to libSystem.

The first raw-arena pcc1 source was
`5a5dab6a2cd53c5109fb356fdb6fa0a5419f58cd18ad1632a047c77163868073`
and compiler `5969c8ba...`. The static-getter source was
`b87cb10b730c5cd110932bc6ed4d159a25691d728e779fa330254766aa5c4f95`
and compiler `b6ffd68d...`.

## Representative-worker verdict [DENIED]

| arm | wall | CPU | instructions | peak footprint | assembly |
|---|---:|---:|---:|---:|---|
| accepted object/packed-safepoint baseline | 30.00 s | 29.89 s | 407.414 B | 4.190 GB | `ff943e10...` |
| raw arena + bound field getters | 82.33 s | 80.72 s | 1.130 T | 4.495 GB | `ff943e10...` |
| raw arena + static field getters | 81.41 s | 80.95 s | 1.113 T | 7.120 GB | `ff943e10...` |

The first implementation is 2.74x slower and executes 2.77x as many
instructions. Replacing bound methods with static module functions saves only
1.1% wall / 1.6% instructions and makes footprint 70% worse than the accepted
baseline. Neither is eligible for a Stage2 run.

## Caller attribution

The first raw-arena candidate produced 23,261 on-CPU samples:

- parser: 0.11%;
- verifier: 74.01%;
- verifier ordinary-use validation: 37.48%;
- verifier definition construction: 28.84%;
- indexed-kernel construction: 24.55%;
- `CompactParsedInstrArena` bound adapter paths: 43.88% inclusive;
- raw `CompilerIntArena.get2/get4` leaves: 0.15%;
- stack preparation: 7.51%;
- precise stack-map planning: 11.78%.

The result falsifies two tempting explanations. Parser packing is not the
regression owner, and raw loads are not expensive. The cost is exposing every
field read as another Python call/argument-binding/type/GC boundary while
verifier, kernel, stackprep, and stackmap rescan the same record family. The
adapter percentage is inclusive work, not removable pure overhead; deleting
the bound adapter frame without deleting the per-field operation leaves wall
almost unchanged.

This reproduces the investigation's prior `[DENIED]` lesson for per-scalar
arenas at a larger scale. A next proposal must batch a complete schema record
per consumer operation or make packed access a compiler-owned inlined
intrinsic, and it must eliminate repeated analysis traversal rather than
renaming per-field calls.

## Disposition

All 19 compiler files and four proposal-only tests were restored mechanically
to the accepted pre-proposal source after exact identity checks against the
frozen baseline. The source tree therefore does not retain the 2.7x regression.
No Stage2 or fixed-point claim was attempted.

A separate measurement-tool defect found during the build remains fixed:
`run_pcc_stage1_build.py` now verifies runtime provenance against its frozen
`source-snapshot`, not an unrelated live worktree. Seven focused tool tests
passed, including an isolated-root regression; no provenance/hash/target or
archive-member check was relaxed.

## Follow-up — batching and raw-inline variants remain denied

Three source-frozen follow-ups tested whether the tagged layout was salvageable
without per-field object adapters:

| follow-up | wall | instructions | footprint | result |
|---|---:|---:|---:|---|
| kernel + verifier consumer-local raw reads | 69.88 s | 990.755 B | 6.774 GB | exact asm |
| plus stackmap + stackprep raw reads | 58.93 s | 821.543 B | 6.390 GB | exact asm |
| plus AArch64 emit/call/compute/memory/regalloc raw reads | 49.30 s | 675.375 B | 4.679 GB | exact asm |
| shared local-valueclass batch interface | 54.84 s | 691.327 B | 4.680 GB | exact asm |

The best arm is still 1.64x slower, executes 1.66x the instructions, and uses
11.7% more footprint than the accepted 30.00 s / 407.414 B / 4.190 GB arm.
The shared interface is architecturally preferable but slower than duplicated
consumer-local unsafe decoding, so neither implementation is accepted.

Caller attribution did validate one structural direction. Moving kernel and
verifier to batch/raw consumption reduced kernel construction from 24.55% to
5.67% and verifier from 74.01% to 31.47%; extending that route moved the final
owners to roughly stackmap 34%, verifier 22%, and emit 32%. Raw helper leaves
remained negligible. The remaining cost is repeated semantic decoding and
analysis across passes, not raw storage access.

Therefore the next proposal is not another getter implementation. It must use
kind-specific columns and have the kernel construct shared definition, use,
type, call/frame-protocol, safepoint, and emission facts once. Generic tagged
field decoding by every pass remains `[DENIED]`, including when wrapped in a
batch valueclass.
