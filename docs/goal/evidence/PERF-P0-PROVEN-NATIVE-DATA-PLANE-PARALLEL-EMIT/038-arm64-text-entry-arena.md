# 038 — AArch64 text entries use a native scalar arena

Date: 2026-09-04

## Change

The current-v23 full worker profile assigns 19.63% inclusive to
`assemble_lines`, 12.51% to `assemble_text_lines`, and 8.81% to `_encode_one`.
Its largest flat leaf remains `py_tuple_set_item` (6.5%).

`assemble_text_lines` previously materialized one Python
`("insn"|"data", payload)` tuple per physical instruction/data chunk and then
iterated that tuple list a second time. It now stores `(kind, cold-side-index)`
as two scalars in `CompilerIntArena`. Pointer-bearing instruction strings and
rare inline-data bytes remain in explicit side tables until their opcode
families migrate. The second pass is an indexed while loop over native scalar
storage; opcode semantics and final encoding are unchanged.

## Gates and transfer

```text
arm64 encoder/driver focused gate   8 passed, 12 provenance deselected
arm64_encode.py strict closure      PASS
source SHA-256                      6550d09dafcd59818fc67bef5ee9c5b25b8c2e388eee206fd335eca247773f0a
pcc1 SHA-256                        316b4538169530e4a5da2c58d8c78d20036124f18d32467dc9599a754a887a43
Stage1 wall / tree CPU              156.61s / 636.40s
Stage1 process-tree peak            4,993,417,216 bytes
linkage / function canary           libSystem only / green
```

The single Stage1 transfer is not a paired speed verdict.

## Current-pcc1 representative worker

Both arms use the same frozen `py_ast` manifest and
`PCC_PY_FRONTEND_WORKER_TIMING=1` environment:

```text
metric                 v23 tuple control       v24 arena candidate   reduction
wall                   28.76s                  27.86s                3.13%
user+sys CPU           28.63s                  27.82s                2.83%
instructions           389.072609B             389.051327B          0.005%
max RSS                3,977,166,848 B         3,977,199,616 B     -0.001%
peak footprint         3,932,245,256 B         3,932,114,160 B      0.003%
worker codegen         27.783s                 26.962s               2.96%
direct indexed emit     7.211s                  7.026s               2.57%
```

Both publish exact PCO SHA-256
`9987edea18e5fde14fd279cfdbfdfefc429eb9bddbae99122102702f4f13d3c0`.

## Verdict

`[CONFIRMED structural; speed weak]` and retained. It deletes a named hot
tuple representation with exact output and no deterministic CPU/instruction/
memory regression; the small wall/CPU movement is not claimed as a robust
speedup. The remaining instruction strings still dominate the assembler.
Next work must populate this scalar plane from measured opcode/operand
families and drive the supported normal-path string counter toward zero. No
Stage2 ran.
