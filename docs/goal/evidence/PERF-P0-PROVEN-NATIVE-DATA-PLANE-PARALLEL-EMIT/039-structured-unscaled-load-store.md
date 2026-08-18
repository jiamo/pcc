# 039 — unscaled load/store instructions cross as encoded scalar records

Date: 2026-09-04

## Change

The first opcode-bearing `StructuredAArch64Module` slice covers the complete
emitter-owned `ldur`, `stur`, `ldurb` and `sturb` family, including x/w/d/s,
zero registers, bare bases and signed 9-bit offsets.

After target-final labels and peepholes freeze, the emitter recognizes the
exact owned spelling without building operand lists/tuples and stores
`(line-index, encoded-u32-word)` in `CompilerIntArena`. It drops that line's
string before handing the module to the assembler. The assembler remaps the
word through section parsing, preserves its exact text position and writes it
directly into `__text`; it does not run line normalization, operand splitting,
register parsing or memory-operand parsing for that record. Every malformed or
unowned spelling remains on the exact text oracle path.

The retained frozen `py_ast` assembly contains 72,970 structured family lines
and 131,857 remaining instruction lines. This is a measured family inventory,
not a zero-fallback completion claim. Source helpers still construct the
recognized strings before target-final conversion; moving typed operands to
their producers remains open.

## Gates and transfer

Twenty-three dedicated tests cover 16 integer/FP/offset encodings, malformed
fallback, mixed label offsets and full module-driver equality. A direct
indexed alloca/store/load module proves emitter transport -> section bytes
against the string oracle. The complete focused packet is:

```text
codec/link/exec/incremental/stackmap/direct/worker  161 passed, 37.92s
arm64_encode.py strict closure                      PASS
arm64_asm_driver.py strict closure                  PASS
pipeline worker strict closure                      PASS
source SHA-256  67384f597010d09c26d607a87393cdbd532bc5af97cac30509fd32a0c2d20752
pcc1 SHA-256    d3cbf3a90501c019e0f5f1d27426b3e4463eeb11ddae59d84cba31fa4575f039
Stage1 wall / tree CPU                              171.77s / 699.87s
linkage / function canary                           libSystem only / green
```

The single Stage1 transfer is not a paired speed verdict.

## Current-pcc1 representative worker

Both arms use the same timed frozen `py_ast` worker:

```text
metric                 v24 line control        v25 structured family  reduction
wall                   27.86s                  27.54s                 1.15%
user+sys CPU           27.82s                  27.37s                 1.62%
instructions           389.051327B             375.069259B           3.59%
max RSS                3,977,199,616 B         3,440,738,304 B      13.49%
peak footprint         3,932,114,160 B         3,395,586,928 B      13.65%
worker codegen         26.962s                 26.602s                1.34%
direct indexed emit     7.026s                  7.636s              -8.68%
```

The conversion costs about 0.61s inside emit, while the downstream
assembler/object tail saves about 0.93s and roughly 537MB. Both arms publish
exact PCO SHA-256
`9987edea18e5fde14fd279cfdbfdfefc429eb9bddbae99122102702f4f13d3c0`.

## Verdict

`[CONFIRMED structural; memory material]` and retained. The family produces a
deterministic instruction reduction, material RSS reduction, exact object and
small end-to-end CPU/wall improvement. It is not the complete native
instruction plane: 131,857 normal-path instruction strings remain, and even
the migrated family still begins as strings inside source helpers. No Stage2
ran.
