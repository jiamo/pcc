# LINK-P1-MACHO-OBJ-MINIMAL — first pcc-emitted Mach-O object links and runs

Mode: host pcc, Darwin arm64. `pcc/backend/macho_obj.py` emits the bytes;
no as(1), no clang, no llvmlite anywhere in the emission path.

## What landed

`emit_text_object(text, symbols)` writes an arm64 `MH_OBJECT` with exactly
the as(1) layout for the same input:

```text
mach_header_64
LC_SEGMENT_64   (unnamed segment, one __TEXT,__text section,
                 S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS)
LC_BUILD_VERSION (platform macos, pinned minos)
LC_SYMTAB       (nlist_64 entries, string table with leading NUL)
LC_DYSYMTAB     (all symbols in the extdef partition)
__text payload / nlist entries / string table
```

All byte layouts come from `pcc.backend.macho_spec` (the LINK-P1-MACHO-SPEC
declarations, themselves pinned against the SDK headers); the writer contains
no layout knowledge of its own. Everything outside the proven subset fails
closed with `MachOEmitError`: empty text, non-instruction-multiple payload,
no symbols, out-of-range or unaligned symbol offsets, duplicate names,
over-long section names.

## Acceptance (tests/python/test_macho_obj_minimal.py, 5 passed)

Both required proofs, plus two:

1. **System linker + run**: `cc main.c leaf_pcc.o` links pcc's object against
   a cc-built main; the binary runs and `leaf42()` returns 42. `nm` shows
   `T _leaf42`; `otool -lv` shows the section and attributes.
2. **Field-level diff vs as(1)**: the same machine code assembled by as(1) is
   parsed with `macho_spec` and compared field by field. Every allowed
   divergence is named in the test: as(1) adds an `ltmp0` local label (so
   symbol counts and dysymtab partitions differ), stamps the running OS's
   minos, and file offsets follow from the symbol-count difference. Header
   fields, load-command kinds, all other section fields, the real symbol's
   (n_type, n_sect, n_value), and the `__text` payload bytes must match
   exactly — and do.
3. Round-trip: the emitted object parses with the spec parser and
   re-serialises byte-for-byte; relocation list is empty as required.
4. Fail-closed rejection of the shapes outside the subset.

## What this does not claim

One section, no relocations, no locals, no data — a leaf function only.
Calls, globals, and every relocation type are LINK-P1-MACHO-OBJ-RELOC, one
type at a time. Nothing on the self-backend path uses this writer yet
(LINK-P1-MACHO-OBJ-SWITCH is a separate row).
