# DOC-P1 book / implementation parity audit — 2026-08-12

## Source identity and scope

- Visible repository HEAD: `ed6f1b30ebcc7f60f1eb84fd41fcd2d075d00743`.
- The worktree was already dirty in unrelated frontend/package/stdlib files; this
  audit did not edit or validate those implementation changes.
- Scope: `books/cn/` and `books/en/` against current source, the active goal
  protocol, the structured task board, and linked evidence. This is a static
  documentation audit, not a runtime or bootstrap qualification.

## Verdict

The Chinese and English editions are substantially synchronized with each
other, but they are materially stale relative to the current implementation.
The right description is **synchronized drift**, not a Chinese-versus-English
fork. Foundational C-frontend, lowering, object-layout, LLVM, and self-backend
explanations are still broadly useful. The production-runtime ownership model,
five-GC implementation ownership, package/C-API status, GPU ownership modes,
GPU external-resource integration, and distributed transport chapter are no
longer a reliable description of the visible repository.

`books/README.md` already limits the book to the repository state of June 2026
and says code wins on disagreement. That warning is now operationally material:
the book cannot be read as current implementation documentation.

## Bilingual and mechanical checks

- Both editions contain the same 23 Markdown files (preface, chapters 1–19,
  summary, and two appendices).
- Every corresponding chapter has the same count of level-two sections.
- All 1,232 relative Markdown links across both editions resolve; there are no
  broken link targets.
- A strict check of fenced blocks whose first line names a source path found 21
  annotated excerpts per edition. Only 6 are literal substrings of current
  source; 15 are not. The same 15-excerpt failure pattern appears in both
  editions. Some blocks are pedagogical condensations rather than edits that
  drifted later, but `books/STYLE.md` explicitly promises current verbatim
  excerpts, so either case violates the book contract.

## Material implementation drift

### 1. Runtime ownership contract reversed after the book model

Chapters 1 and 14, Chapter 10's low-level ownership discussion, and the glossary
say the C kernel is permanently kept/minimized and that no-libpython explicitly
does not aim for zero C. The current north star is stronger: allocation, object
headers, atomics, syscalls, threading, dynamic loading, safepoints, stack maps,
all five GCs, and extension ABI entrypoints migrate to freestanding pcc-Python;
hand-written C remains an oracle and is removed from the production link.
Linux additionally has a zero-C/zero-libc closure target, while Darwin retains
only named libSystem ABI calls.

This is not only future prose. Current source contains production-shaped
freestanding owners such as:

- `pcc/py_runtime/py/freestanding_gc_index_table.py`
- `pcc/py_runtime/py/freestanding_gc_external_resource.py`
- `pcc/py_runtime/py/py_substrate.py`

`pcc/py_runtime/Makefile` lists `py_gc_index_table` and
`pcc_gc_external_resource` in `PY_REPLACED_C_MODULES`, directly contradicting
Chapter 10's claim that GC index tables are intentionally C-only with no
pcc-Python mirror. Task `LIBC-P2-FREESTANDING-GC` records all five production
collector policy families as freestanding pcc-Python owners. The final total
runtime closure remains unfinished under
`LIBC-P3-FREESTANDING-RUNTIME-CLOSURE`, so the correct current claim is
"migration materially landed, final zero-C production closure still open."

### 2. Runtime and bootstrap source excerpts are stale

Concrete examples from the 15 non-verbatim annotated excerpts per edition:

- Chapter 8's `py_raise()` excerpt omits current normalization and runtime-log
  steps but still uses `exc_owned`, making the displayed excerpt internally
  incomplete. Current `py_exc_tls.c` is 183 lines, not the stated 172.
- `_emit_post_call_err_check()` now accepts source spans and several ownership
  cleanup tuples. Current codegen contains 231 textual occurrences across 45
  files, far beyond the book's June count of roughly 80 across 20–25 files.
- Chapter 15's displayed `bootstrap_cli_main(argv: list[str])` dispatcher is not
  the current function. `pcc/cli_bootstrap.py` is 10,868 lines, not roughly
  seven thousand.
- Chapter 18 displays a `validate_task_board()` function that does not exist in
  current `scripts/goal_state.py`; current validation is `validate(board, root)`
  over the version-2 milestone/dependency schema.
- The book says `PY_MODULES` compiles 55 ports; the current Makefile list has 72.
- Chapter 6's 56-line facade remains exact, but its mixin count is now 85 rather
  than 86.

The fixed-point thesis and stage meanings remain valid; the concrete CLI,
scheduler, cache, ownership-cleanup, and gate descriptions need a source refresh.

### 3. Five-GC chapters describe transition implementations as owners

Chapters 10 and 11 still explain the large C files as the implementation center
and repeatedly describe C/pcc-Python dual production tracks. The algorithmic
history remains valuable, and dated performance numbers are honestly dated,
but current production ownership has moved into the freestanding pcc-Python
module family. The chapters need to distinguish:

- current production owner;
- retained C differential oracle;
- shared slot/root/frame/native-handle contract;
- historical measurements from the older ownership layout.

Without that split, the reader gets the wrong answer to "which source actually
ships in the production archive?"

### 4. Package and C-API status advanced beyond Chapter 17

Chapter 17 correctly preserves ABI-mode distinctions and CPython-artifact
rejection, but it predates the current vertical evidence:

- `B-P0-PKG` is `DONE_STRONG`: a pinned real simplejson pcc-native extension
  canary builds/imports/runs under pcc1/self/no-libpython and GC0..4.
- the NumPy package/C-API line advanced through real pcc-native artifact,
  PEP-489, L4/L5, and freestanding C-API-port work, while current task rows also
  record a live regression/requalification boundary. The current status is
  neither the book's earlier rejection-only frontier nor an unrestricted
  "NumPy supported" claim.
- production C-API ownership is migrating from monolithic C shim descriptions
  to pcc-Python port owners, with C retained as an oracle.

Chapter 17's mechanisms are useful, but its implementation-status boundary is
not current.

### 5. Chapter 19 contains categorical claims contradicted by source

Chapter 19 is the largest functional drift:

- It says TVM/TileLang are only parser/oracle shapes and are never executed.
  Current `pcc/kernel_ir/tvm_tilelang_owner.py` implements an explicit pinned,
  out-of-process `tvm-tilelang` execution-owner provider, discloses that the
  provider process links libpython, hashes the frozen IR and returned Metal
  source, and fails closed with no backend fallback. This does not create a
  pcc-native ordinary `import tilelang` claim, but it invalidates the book's
  categorical "never execute" statement.
- It says `pcc/dist/` is single-process and opens no sockets. Current
  `pcc/dist/tcp_transport.py` is an explicit real TCP-ring owner and current
  tasks mark localhost transport plus deterministic collectives `DONE_STRONG`.
  Multi-Mac proof remains `BLOCKED`, so localhost transport must not be relabeled
  as multi-host completion.
- It says the GPU external-resource seam is not connected to production GCs.
  Current runtime exports and freestanding owner code implement registration,
  retain, fence-complete, poll, and exactly-once release across GC0..4; task
  `GPU-P0-GC-EXTERNAL-RESOURCE-SEAM` is `DONE_STRONG`.
- It says there is no external framework interop. The repository now has an
  opt-in MLX DLPack hardware round-trip gate and a closed task for that narrow
  claim. This is not broad torch/MLX/MPS compatibility.

The honest rewrite must separate the pcc-owned Metal backend, the optional
pinned TVM/TileLang owner process, ordinary TileLang-import compatibility, MLX
DLPack interop, localhost TCP collectives, and still-unproven multi-Mac work.

## Chapter classification

| Chapters | Classification | Reason |
|---|---|---|
| 3–5, 12–13 | mostly current | Core frontend/LLVM/self-backend mechanisms still match, with local numeric/status drift. |
| 2, 6, 9, 16 | usable but refresh needed | Architecture remains recognizable; worker/mixin/representation/ownership details moved. |
| 7–8, 15, 18 | conceptually current, concretely stale | Layout/fixed-point/method theses hold; production owner, signatures, counts, snippets, and gate machinery drifted. |
| 1, 10–11, 14, 17 | materially stale | Runtime ownership and ecosystem evidence changed at the architectural/status level. |
| 19 | critically stale | Several categorical "does not exist" statements are contradicted by current source and task evidence. |
| Appendix A/B and README snapshot note | stale index/contract | Missing freestanding runtime, optional GPU owner, TCP transport, GUI/stdlib breadth; glossary preserves the superseded permanent-C-kernel model. |

## Commands and observed results

- `gtimeout 30s env -u LC_ALL uv run python scripts/goal_state.py validate`
  before the audit: `OK: 259 tasks validated` after task ingestion.
- Relative-link resolver over `books/{cn,en}/*.md`: `links 1232 broken 0`.
- Annotated-snippet substring audit: each edition `exact=6`, `drift=15`.
- AST parse check for the newly cited owner/transport/freestanding modules: all
  six selected modules parsed successfully.
- No pytest, bootstrap, GC, hardware, package, or integration gate was run.

## Supported and unsupported claims

Supported: both editions are mutually aligned at the structural and claim-shape
level; both are materially behind the visible implementation in the areas named
above; the cited source surfaces exist and parse; the cited task statuses are
the current board state.

Not supported: every sentence in all 9,246 lines was semantically re-proven;
historical `DONE_STRONG` evidence was rerun on the dirty worktree; hardware,
package, NumPy, bootstrap, or five-GC behavior is green today; either book has
been rewritten. A rewrite and a repeatable book-sync checker are separate
follow-up work.
