# Bilingual book refresh: freestanding zero-libc and declarative GUI

Date: 2026-08-12

Status: `DONE_STRONG`

Task: `DOC-P1-BOOK-ZERO-LIBC-GUI-REFRESH`

## Claim boundary

This evidence closes the documentation slice only. It proves that the Chinese
and English books now describe the current runtime-ownership direction and the
declarative GUI source/design boundary consistently. It does not prove the
open full-runtime zero-libc acceptance task or any `GUI-P2-*` product gate.

## Changes

- Rewrote both Chapter 14 editions around three distinct claims:
  no-libpython, pcc-Python production ownership, and Linux zero-libc.
- Replaced the permanent-C-kernel model with the current four-layer contract:
  compiler intrinsics, freestanding pcc-Python, semantic pcc-Python, and C/libc
  differential oracles.
- Recorded the current production archive recipe, bounded Linux raw-syscall
  tracer proof, named Darwin libSystem boundary, and the still-open
  `LIBC-P3-FREESTANDING-RUNTIME-CLOSURE` acceptance surface.
- Added bilingual Chapter 20 for the webview-free declarative GUI: canonical
  composition ownership, keyed atomic commit, four-lane scheduling, event
  dispatch, bounded style compilation, commands, app lifecycle, native ABI
  boundary, failure history, and honest gate status.
- Updated both summaries, the book blueprint, repository maps, glossaries, and
  the affected ownership cross-references in Chapters 1, 2, 7, 10, 11, and 17.

Primary book artifacts:

- `books/cn/ch14-no-libpython.md`
- `books/en/ch14-no-libpython.md`
- `books/cn/ch20-declarative-gui.md`
- `books/en/ch20-declarative-gui.md`

## Source and evidence basis

The rewrite was checked against current source owners under
`pcc/py_runtime/py/`, the production archive recipe in
`pcc/py_runtime/Makefile`, the machine-readable GUI contract
`pcc/py_runtime/gui_declarative_contract_v1.json`, the declarative GUI design
contract, and the bounded ownership evidence referenced directly by Chapter
14. Historical C implementations are retained in the prose only when labeled
as migration history, ABI declarations, or differential oracles.

## Static gates

The book writing contract prohibits using compilation or tests as a substitute
for source-grounded prose review, so this slice ran static documentation gates
only.

1. A read-only Markdown audit checked 1,186 relative links across `books/`:
   no broken targets.
2. The audit found 24 matching Chinese/English filenames and equal H2 counts
   for every pair.
3. It checked 20 marked source excerpts across the four rewritten/new feature
   chapters; every excerpt is an exact substring of the referenced current
   source, and the Chinese/English source blocks match.
4. Both editions of Chapters 14 and 20 contain at least two diagrams and at
   least three current-source excerpts.
5. `git diff --check` passed.

No compiler, runtime, GUI, hardware, or pytest gate was run for this book-only
slice. Existing source and test names are not promoted into runtime acceptance
claims by this evidence.

## Residual boundary

The earlier whole-book parity audit remains the route for drift in untouched
chapters and older excerpts. This refresh certifies the zero-libc and GUI
feature chapters plus the ownership cross-references listed above, not every
historical snippet in the entire book.
