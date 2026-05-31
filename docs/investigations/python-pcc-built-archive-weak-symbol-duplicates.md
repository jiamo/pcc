# Investigation: `libpy_runtime_pcc.a` duplicate-symbol `pcc_gc_thread_unregister_buffers`

## Status
resolved

## Problem Description

`tests/python/test_runtime_oracle_diff.py` (13 cases) failed link with:

```
duplicate symbol '_pcc_gc_thread_unregister_buffers' in:
    libpy_runtime_pcc.a[8](pcc_threads.o)
    libpy_runtime_pcc.a[7](py_gc_backend.o)
```

The function exists twice in the pcc-compiled archive
`libpy_runtime_pcc.a`:

- `pcc/py_runtime/src/pcc_threads.c:20` — declared as
  `__attribute__((weak)) void pcc_gc_thread_unregister_buffers(void) {}`,
  guarded by `#if defined(__GNUC__) || defined(__clang__)`.
- `pcc/py_runtime/src/py_gc_backend.c:1886` — the real
  implementation.

The system preprocessor expands `__GNUC__` / `__clang__` for pcc too
(pcc uses system `cpp`), so the weak fallback is emitted into pcc's
IR. pcc's C frontend currently does not lower
`__attribute__((weak))` to LLVM `weak` linkage — both translation
units emit *strong* definitions and the archive ends up with two
strong copies of the same symbol. macOS `ld` then fails the link.

The system-cc archive `libpy_runtime.a` does not exhibit this
because clang honors the `weak` attribute and the strong def in
`py_gc_backend.o` overrides the weak placeholder.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_runtime_oracle_diff.py::test_corpus_cc_vs_pcc_equivalence[class_basics]' \
  -q -n0
```

Pre-fix: `duplicate symbol` linker error.

## Test [CONFIRMED]

`test_runtime_oracle_diff.py` 21 / 21 passing (was 13 failures + 8
passes).

## Proposals

- No.1 Implement `__attribute__((weak))` → LLVM `weak` linkage in
  pcc's C frontend                                              [DENIED]
- No.2 Drop the weak fallback; declare extern only              [CONFIRMED]

## No.1 Implement weak attribute in pcc's C frontend
### Code Change
Pcc's parser would need to recognize `__attribute__((weak))` in
function definitions and propagate the attribute through codegen to
LLVM `weak` linkage on the `ir.Function`.

### DENIED — out of scope for this slice
The pcc C parser is shared with the `--separate-tus` and Lua/SQLite
real-program paths. Adding an attribute-spec lowering touches the
parser grammar, the funcspec / declaration-attribute plumbing, and
all downstream usages. That is a multi-iteration parser change with
high blast radius; it should be a dedicated slice (`__attribute__`
broadly: `weak`, `noreturn`, `aligned`, `packed`, `visibility`…).
This 1-minute slice doesn't need it.

## No.2 Replace weak fallback with extern declaration
### Code Change

`pcc/py_runtime/src/pcc_threads.c`:

```c
/* py_gc_backend.c is in every archive variant (Makefile SRCS
 * always includes both pcc_threads.c and py_gc_backend.c), so an
 * extern declaration is sufficient. The weak placeholder existed
 * only for hypothetical builds that left py_gc_backend.c out;
 * those don't exist today. */
extern void pcc_gc_thread_unregister_buffers(void);
```

### CONFIRMED
- `tests/python/test_runtime_oracle_diff.py` 21 passed, 6 skipped
  (was 13 failures).
- Fallback baselines + corpus + bootstrap baselines: 194 passed, 4
  skipped (unchanged).
- The system-cc `libpy_runtime.a` still builds (the extern
  declaration is satisfied by `py_gc_backend.o` at link time).
- The pcc `libpy_runtime_pcc.a` no longer carries the duplicate
  strong symbol.

### Why this is the correct minimal fix
The original `__attribute__((weak))` placeholder protected against
a build configuration that excluded `py_gc_backend.c`. The current
Makefile's `SRCS` always includes both files, in all archive
variants (`libpy_runtime.a`, `libpy_runtime_pcc.a`,
`libpy_runtime_pcc_py.a`, `libpy_runtime_pcc_py_libpython.a`). The
weak fallback was therefore dead code under cc and broken code
under pcc; an extern declaration captures the *real* invariant
("py_gc_backend.c provides this symbol") while remaining compatible
with both compilers.

If a future build path needs to exclude `py_gc_backend.c`, the
right fix at that point is either to fold a stub directly into the
relevant new archive recipe or to land Proposal No.1 (proper weak
attribute support in pcc's C frontend) — but neither belongs in
this slice.

## Report
Landed via a single 1-line change to `pcc_threads.c`, closing 13
oracle-diff failures with no parser/codegen blast radius. The weak
attribute lowering remains a future enhancement, tracked
informally by this investigation as a candidate dedicated slice.
