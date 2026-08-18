# 035 — emitter-to-assembler line transport retained structurally

Date: 2026-09-04

## Change

The production singleton PCO worker no longer joins the complete indexed
AArch64 output into one assembly string and asks the assembler to split that
string again. New compatibility-preserving APIs carry `list[str]` chunks:

- `emit_aarch64_darwin_indexed_lines` returns the existing final emitter list;
- `assemble_lines` consumes line chunks directly;
- `assemble_text_lines` consumes the assembler's physical text lines;
- existing string APIs remain exact wrappers/oracles.

The directive parser, instruction encoder, labels, relocations, stack maps,
data-in-code and malformed-input behavior are unchanged. The worker enables
the path only for direct native-object output with validation/text-control off;
ASM and differential modes retain the string projection.

## Failure class closed before transfer

The first source-frozen build (v18) failed quickly and produced no pcc1.
Emitter entries are chunks, not necessarily physical lines: global/constant
emitters may place several `.quad/.long/.byte` directives in one string. The
new driver initially treated each chunk as one line and rejected every such
module.

The correction is generic: reuse a chunk by identity when it has no newline;
only multi-line chunks are locally split. A regression passes the entire real
assembler fixture as one chunk. No module or directive is special-cased.

## Gates

```text
arm64 encode/driver + owned linker/worker + direct indexed packet
80 passed, 12 environment-gated deselections, 46.31s

strict worker closure
run_codegen_worker is a real entry, no py_cpy/stub

contextual emitter closure
emit_aarch64_darwin_indexed_lines is a real entry
```

The old v14 standalone `assemble_text` and `assemble_file` functions are also
strict stubs outside their contextual closure, so the same standalone status
of the new line variants is a pre-existing module-closure limit, not a new
fallback claim.

Source-frozen v19 pcc1:

```text
SHA-256                 be7ce1bb0303602dbb386cd751c3c4f72c43a562958716aec055111980621ead
Stage1 wall / tree CPU  170.81s / 704.74s
tree peak               4,738,023,424 B
linkage / canary        libSystem only / 42
```

This Stage1 is transfer evidence, not an unpaired Stage1 speed claim.

## Current-pcc1 worker verdict

The control and candidate consume the identical v14 `py_ast` worker manifest,
AST and full export wire:

```text
metric                 v14 string control    v19 line candidate   control/candidate
wall                   41.87s                38.66s                1.083x
user+sys CPU            39.36s                38.48s                1.023x
instructions           561.547658B           561.422943B           1.00022x
cycles                 128.888578B           128.003737B           1.00691x
max RSS                5,253,480,448 B       5,118,443,520 B       1.02638x
peak footprint         5,209,510,016 B       5,074,292,768 B       1.02665x
```

Both publish byte-identical PCO SHA-256
`9987edea18e5fde14fd279cfdbfdfefc429eb9bddbae99122102702f4f13d3c0`.

## Verdict

`[CONFIRMED]` as a named representation deletion and modest memory improvement,
not as a material instruction/speed solution. The deterministic instruction
signal is essentially neutral; the task remains open. The next architectural
boundary is structured instruction/data emission that avoids reparsing textual
operands, not another join/split tweak. No Stage2 ran for this slice.
