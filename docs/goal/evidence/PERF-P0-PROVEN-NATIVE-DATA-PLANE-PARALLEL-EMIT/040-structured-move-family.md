# 040 — move-wide/register moves join the structured word plane

Date: 2026-09-04

## Change and inventory

The exact emitter-owned `mov`, `movz` and `movk` integer-register family now
uses the same scalar word transport as the confirmed unscaled load/store
family. The parser accepts x/w registers, zero/SP aliases, 16-bit immediates
and target-valid `lsl` lanes without constructing operand lists/tuples;
unowned/malformed/FP shapes stay on the text oracle.

The timed compiled `py_ast` worker reports:

```text
structured unscaled instructions  72,970
structured move instructions      38,319
remaining fallback instructions   93,538
```

Thus 111,289 of 204,827 normal instructions cross the emitter/assembler
boundary as scalar records. This is not a zero-fallback or upstream-producer
completion claim.

## Gates and transfer

The move tests cover register aliases, width rejection, immediate bounds and
shift lanes against the text codec. Together with transport/worker closure:

```text
focused structured encoding/direct/worker packet   41 passed
arm64_encode.py strict closure                      PASS
pipeline worker strict closure                      PASS
source SHA-256  d347135ed9bf2f1957d918ecca9d852fc2bc33601c117a392fe985ef468dc6bf
pcc1 SHA-256    63c7fb26c38a12ddaffb0ec3b7572ad335f61064e2896218c3ee5c737fd6e713
Stage1 wall / tree CPU                              168.50s / 684.86s
linkage / function canary                           libSystem only / green
```

The single Stage1 transfer is not a paired speed verdict.

## Current-pcc1 representative worker

```text
metric                 v25 control             v26 move candidate   reduction
wall                   27.54s                  27.32s               0.80%
user+sys CPU           27.37s                  27.19s               0.66%
instructions           375.069259B             369.166369B          1.57%
max RSS                3,440,738,304 B         3,242,016,768 B      5.78%
peak footprint         3,395,586,928 B         3,196,603,104 B      5.86%
worker codegen         26.602s                 26.357s               0.92%
```

Both arms publish exact PCO SHA-256
`9987edea18e5fde14fd279cfdbfdfefc429eb9bddbae99122102702f4f13d3c0`.

## Verdict

`[CONFIRMED structural; memory material]` and retained. Instructions and
memory improve deterministically with no output regression; wall/CPU movement
is small and is not claimed as a standalone material speedup. The next family
is `bl`, which requires symbol IDs and BRANCH26 relocation records rather than
another no-relocation word-only extension. No Stage2 ran.
