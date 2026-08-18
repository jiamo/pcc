# 041 — relocation-bearing call records and first source-current Stage2 transfer

Date: 2026-09-04

## Structured call family

`StructuredAArch64Module` now carries an extensible four-integer instruction
record `(line_index, word, relocation_kind_id, symbol_id)`.  The complete
emitter-owned direct-`bl` family uses interned symbol IDs: recursive and
same-atom calls resolve locally, while cross-atom and external calls publish
exact `ARM64_RELOC_BRANCH26` records.  Malformed or unowned forms remain on the
text oracle.

The frozen `pcc.py_frontend.py_ast` worker inventory is now:

```text
structured unscaled load/store   72,970
structured move                  38,319
structured direct call           47,015
remaining text fallback          46,523
```

Focused recursive/local/cross-function/external relocation differentials plus
the existing assembler, direct-object and worker packet pass **180/180**.  The
strict no-libpython closures for the encoder, assembler driver and frontend
worker pass.  The representative PCO remains byte-identical at SHA-256
`9987edea18e5fde14fd279cfdbfdfefc429eb9bddbae99122102702f4f13d3c0`.

Against v26 on that frozen worker, v27 moves wall `27.32 -> 26.98s`, timed CPU
`27.19 -> 26.87s`, instructions by about `-0.59%`, and max RSS
`3.242 -> 3.124GB`.  This confirms the representation and memory direction;
the standalone speed movement is deliberately not called material.

## Host/native cost-model routing

The structured instruction tail conversion is useful under native pcc1 but is
extra Python work under host CPython.  Worker construction now obtains the
existing native-worker predicate from the pipeline and enables this transport
only for a native worker.  Both modes keep the structured stackmap section and
the direct Section-to-packed-PCO codec.

The resulting v28 Stage1 is libSystem-only and function-canary green:

```text
source identity                   3e61ddacfcc8a7766aa0f4583f510b3532eba8ab466032d47021cc4f3d99c60f
pcc1 SHA-256                      caf78f564c22356f8387d23099375b6995e6b454a3e6c7d8d8c5cd148dc59cba
Stage1 wall / timed-tree CPU      171.16s / 689.56s
export / codegen / link           9.645s / 102.090s / 51.121s
```

On the same frozen pcc1 worker, v28 publishes the exact PCO in `26.78s`,
retires `367.722B` instructions and peaks at `3.124GB` max RSS.  This Stage1 is
a successful transfer build, not an adjacent-pair speed verdict.

## Safe Stage2 validation

The current-source GC0 Stage2 ran once through the receipt-bound process-tree
guard with frontend width 2, self-backend width 2 and an 8 GiB hard tree-RSS
limit:

```text
receipt              build/structured-mode-stage2-v28/stage2-record.json
compile wall         995.648s
publish barrier        9.957s
end-to-end wall      1005.626s
timed-tree CPU       2301.856s  (2054.295 user + 247.561 sys)
peak tree RSS        7,619,608,576 B
peak processes       11
result               return code 0; runnable pcc2 --help
pcc2 SHA-256          d2116e2095b299cf87c5e1a7e17b61c81037da79164d37c54922c6d695e56be5
linkage               libSystem only; no libpython or LLVM
```

The older safe v18 receipt was `1349.675s / 3037.835s CPU /
7,812,333,568 B`, so the cross-source transfer is about 25.5% lower wall and
24.2% lower tree CPU without exceeding the safety envelope.  This is useful
directional evidence, not the adjacent alternating same-source A/B required
for final acceptance.

Stage2/Stage1 wall is still **5.875x**.  Deferred emit occupies roughly 796s;
the current lane floors permit only about six seconds of safe cross-lane
packing improvement, so scheduler tuning cannot close the gap.  Stage2 also
repeats a 13.06MB native-export decode in every one of 224 fresh workers.

## Verdict and open boundary

`[CONFIRMED]` for the direct-call representation, exact output, safe Stage2
transfer and material end-to-end progress.  `[OPEN]` for the task: source
helpers still create instruction strings, 46,523 text instructions remain,
Stage2 is 5.875x Stage1, and no GC0 Stage3/fixed point or GC1-4 transfer has
run.  The next performance slice must address a whole repeated-worker owner,
starting with the shared exports/type-inference plane, rather than another
adjacent opcode helper.
