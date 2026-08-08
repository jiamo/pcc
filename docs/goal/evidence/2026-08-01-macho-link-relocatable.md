# LINK-P1-MACHO-LINK-STATIC — the link engine, proven against `ld -r`

Mode: host pcc, Darwin arm64. `pcc/backend/macho_link.py`; the merge itself
uses no ld.

## The slice and why this one

The row's engine is symbol resolution + section merging + layout +
relocation application. The *executable container* (dyld surface, stubs, GOT,
entry point) is a separate row (LINK-P1-MACHO-LINK-DYLD), so the engine
cannot be proven by producing a runnable binary yet. It can be proven exactly
in the shape `ld -r` occupies: relocatable linking — the same engine, minus
the container.

## What it does

- Sections merge by (segname, sectname); each input's payload lands at its
  own alignment, and segment ordering follows the same __TEXT-before-__DATA
  rule the assembler driver uses, because it decides every symbol's address.
- Defined symbols keep identity and get their merged address (converting the
  input's section-relative `n_value` through the section's own `addr`).
  A symbol undefined in one input and defined in another is **resolved** —
  it leaves the undefined list and relocations re-point at the definition.
- Relocations keep type/extern/pcrel/length, shift `r_address` by the input's
  offset within the merged section, and re-point at the merged symbol table.
  `ARM64_RELOC_ADDEND` companions are folded on read and re-emitted on write,
  so an addend travels with the relocation it modifies.
- Fail closed on duplicate definitions, zerofill inputs, non-extern
  relocations, and empty jobs.

## Evidence (tests/python/test_macho_link_relocatable.py, 6 passed)

Inputs are **pcc-emitted objects** (assembler driver + object writer), so the
whole chain from asm text to merged object has no system tool in it.

- Merging two mutually-referencing units equals `ld -r` on section payloads,
  relocation tables (entry by entry, symbols by name), and symbols
  (type/section/address).
- `_helper`, `_table` and `_bmarker` are each undefined in one input and
  defined in the other; after the merge nothing is undefined.
- Both merged objects link and run identically (the cross-object call, the
  GOT load of the other unit's data, and the local table read all produce 35).
- `.quad _table+8` still names the second element after the merge — the
  addend survives.
- **At real scale**: a 64-function unit with hundreds of relocations, an
  external hook, and a 32-entry data table, merged with a unit calling into
  it, still equals `ld -r` exactly (>150 text relocations, >60 symbols).

Additionally verified out-of-band on genuine compiler output: the real
2,138-line self-backend emission linked against a second pcc object gave
payloads, 300 relocations, and 67 symbols all identical to `ld -r`.

## What is not done

The executable container: no `LC_LOAD_DYLIB`/`__stubs`/`__got`/fixups/
`LC_MAIN`, therefore no pcc-linked *executable* yet — that is
LINK-P1-MACHO-LINK-DYLD, which this engine now feeds. Archive (.a) member
selection with repeated-scan semantics is also still open; the current
engine links explicit object lists.
