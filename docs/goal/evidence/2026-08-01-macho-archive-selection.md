# Archive member selection — the last piece before pcc's linker can replace ld

Mode: host pcc, Darwin arm64. `pcc/backend/macho_archive.py`, wired into
`macho_exec.link_executable(archives=...)`.

## Why this was the blocker

The self path links `cc <asm> <runtime archive> -o out -lm`. Everything else
in that command pcc can now do itself — but the runtime archive is 2.9MB of
members, and an archive is not a bag of objects. It is a *pool*: a member
enters the link only when it defines something currently undefined. Without
that, either every binary carries the whole runtime or the link fails.

## What it does

- BSD `ar`: `!<arch>` magic, 60-byte member headers, `#1/<n>` extended names
  (the name follows the header and counts inside the member size).
- The `__.SYMDEF` table of contents is **skipped, not trusted** — pcc reads
  each member's own symbol table, so a stale or missing index cannot change
  what gets linked.
- `select_members()` pulls repeatedly until no new member qualifies, and
  returns the symbols still unsatisfied rather than swallowing them.

## The property that needed a real fixture

A member pulled late can reference a symbol defined by a member *earlier* in
the archive; a single forward pass has already walked past it. The test
archive is ordered `[early, late, unused]` where `late` calls `early` and
only `_late` starts undefined — and the test **runs the one-pass algorithm
inline first** and asserts it leaves `_early` undefined. That assertion is
what stops the fixture from silently degrading into one that cannot tell the
two algorithms apart.

## Evidence (7 + 8 passed)

- reads members and their defined/undefined symbols
- pulls only what is needed; the unreferenced member stays out
- the repeated scan resolves what one pass cannot (with the inline control)
- empty request pulls nothing; unsatisfiable symbols are reported
- **the real `libpy_runtime_pcc_py.a`** (~2.9MB, extended names,
  `__.SYMDEF SORTED`) parses to >50 members, the index is not mistaken for an
  object, and pulling one known runtime entry point drags in a strict subset
- fail-closed on non-archives and truncated headers
- end to end: a pcc-linked executable built against an `ar`-produced archive
  prints correctly, exits 7, and does **not** contain the unreferenced member

## What remains for LINK-P1-MACHO-LINK-SWITCH

The pieces now all exist: encoder, assembler driver, object writer, archive
selection, static merge, executable link, signature. The switch itself is
routing `_link_with_self_backend_ir_texts` through them behind a flag and
then flipping the default — and its acceptance gate is the strongest in the
repository: a full pcc1→pcc2→pcc3 chain linked by pcc's own linker with
pcc2/pcc3 byte-identity preserved. That is a bootstrap-matrix run.
