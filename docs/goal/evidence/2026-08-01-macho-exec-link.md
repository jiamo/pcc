# LINK-P1-MACHO-LINK-DYLD — a pcc-linked executable that dyld loads and runs

Mode: host pcc, Darwin arm64. `pcc/backend/macho_exec.py`. **No `ld`, no
`as`, no `codesign` anywhere in the path** — pcc encodes the instructions,
writes the object, merges and resolves, assigns addresses, applies
relocations, emits the dyld surface, and signs.

## Result

```text
$ ./pcc_linked          # asm -> pcc object -> pcc link -> pcc signature
hi
$ echo $?
42
$ codesign --verify --strict ./pcc_linked   # exit 0
```

The program calls libSystem's `puts` through a pcc-built stub and GOT slot
bound by chained fixups, reads its own `__DATA`, and returns a computed
status. A second case links **two** pcc objects where main calls a helper in
the other unit that itself calls `puts`.

## The dyld surface, chosen and pinned

`LC_DYLD_CHAINED_FIXUPS` with `DYLD_CHAINED_PTR_64_OFFSET`, which requires
macOS 12+ — recorded as the `LC_BUILD_VERSION` minimum, since the row asks
for the binding choice and its OS floor to be explicit. Classic
`LC_DYLD_INFO_ONLY` is deliberately not implemented rather than half-built,
and a test asserts it is absent. Emitted: `__PAGEZERO`, `__TEXT`
(text/stubs/cstring), `__DATA_CONST` (GOT), `__DATA`, `__LINKEDIT`, plus
`LC_MAIN`, `LC_LOAD_DYLIB`(libSystem), `LC_LOAD_DYLINKER`, `LC_UUID`,
`LC_BUILD_VERSION`, `LC_SYMTAB`, `LC_DYSYMTAB`, `LC_CODE_SIGNATURE`.

## Three failures the kernel found, and what each taught

Each was a silent `SIGKILL` or a strict-validation refusal, diagnosed by
bisection rather than guessed:

1. **`LC_BUILD_VERSION` declared 32 bytes but wrote 24** (`ntools = 0` makes
   it 24). The load-command walk then landed mid-`LC_MAIN`. Caught by pcc's
   *own* Mach-O parser refusing `cmdsize 0` — the spec layer paying off.
2. **`__DATA_CONST` without `SG_READ_ONLY`**: dyld says so explicitly. The
   segment is made read-only after fixups; the flag is not optional.
3. **`__LINKEDIT.filesize` must cover the code signature.** ld's does
   (`fileoff + filesize == end of file`); mine stopped before the signature
   and the kernel answered "main executable failed strict validation". The
   blob size is predictable — one SHA-256 per 4096-byte page up to the
   signature offset — so it is now predicted, written into both
   `LC_CODE_SIGNATURE` and `__LINKEDIT`, and signed once.

And the one that produced *no* diagnostic at all, isolated by building the
same program without imports (which ran, proving the base layout was sound):

4. **`S_SYMBOL_STUBS` / `S_NON_LAZY_SYMBOL_POINTERS` index the indirect
   symbol table through `reserved1`**, and a binary claiming those section
   types without emitting one is SIGKILLed silently. Chained fixups bind the
   GOT by walking the chain, not through the indirect table, so pcc's stubs
   and GOT are plain sections and no indirect table is emitted. Same binary,
   same code, only the section types changed: `rc 42`, `stdout "hi"`.

## Evidence (tests/python/test_macho_exec_link.py, 6 passed)

Behavioral, not structural — a structural check alone would have passed
several of the versions above that the kernel killed on sight:

- libSystem call + data read: right stdout and exit status
- import-free binary runs
- two objects linked into one working executable
- `codesign --verify --strict` accepts it
- the load-command and section set is the one chosen (and `LC_DYLD_INFO_ONLY`
  is absent)
- fail-closed on a missing entry symbol

## What is not claimed

One `__TEXT`/`__DATA_CONST`/`__DATA` layout for pcc's own link job. No
archives, no dylib output, no TLS, no unwind info (`__unwind_info` is not
emitted, so C++-style exceptions and backtraces through pcc-linked frames are
out of scope), no lazy binding, no dead stripping. `ld` remains the default
and the oracle; nothing in the compiler routes through this yet.
