# LINK-P1-MACHO-SPEC — declarations pinned against the SDK and a real clang object

Mode: host pcc, Darwin arm64. This row declares and parses; it emits nothing
(the row's own constraint: "No emission in this row").

## What landed

`pcc/backend/macho_spec.py`:

- Declarations with exact byte layouts for `mach_header_64`, `load_command`,
  `segment_command_64`, `section_64`, `symtab_command`, `dysymtab_command`,
  `nlist_64`, `relocation_info`, `build_version_command`,
  `build_tool_version` — plus the constants pcc needs (magic, cputype,
  filetypes, load-command kinds, section types/attributes, nlist n_type bits,
  and the full arm64 relocation-type enum).
- `unpack_relocation` / `pack_relocation` put relocation_info's packed
  bitfield (symbolnum:24, pcrel:1, length:2, extern:1, type:4) in exactly one
  place, with range validation on pack.
- `parse_object()` is exact and fail-closed (`MachOFormatError` on bad magic,
  byte-swapped input, truncated or overrunning load commands). Unrecognised
  load commands are kept as raw bytes, never skipped, so re-serialising a
  parsed object reproduces the original bytes — completeness is proven, not
  assumed.
- The parsed model exposes segments/sections, the symbol table with names
  resolved through the string table, and per-section relocation lists.

## Why there is no generated declarations file

The row allowed "generated from **or checked against** the SDK headers". The
checked-against form was chosen deliberately: this session alone found three
hand-maintained mirror drifts (AST field-order contract ×2 copies, package
installer ×2 implementations, atomics helpers), and a generated file is one
more mirror to go stale. Instead the SDK stays the single authority and the
test suite compiles a cc probe over `<mach-o/loader.h>`, `<mach-o/nlist.h>`,
`<mach-o/reloc.h>`, `<mach-o/arm64/reloc.h>` and compares **every declared
field offset, struct size, and constant** at test time.

## Acceptance evidence (tests/python/test_macho_spec.py, 5 passed)

1. SDK layout probe: all field offsets, sizes and constants match the SDK
   headers (the one spelling difference, `nlist_64.n_strx` living inside the
   SDK's `n_un` union, is mapped explicitly).
2. Round-trip: a clang-produced `.o` (`-O1 -target arm64-apple-macos12`, a
   shape with an extern call, a mutable global, a cstring and a static
   function) parses and re-serialises **byte for byte** across the header +
   full load-command region; every command accounted for, none skipped.
3. Field values match `otool -lv` (section names, addrs, sizes) and `nm`
   (defined vs undefined symbols) on the same object — "reproduce every field
   value exactly" checked against an independent reader.
4. Relocation bitfield round-trips over every relocation clang emitted for
   the call+global shape, and the expected arm64 types (BRANCH26 for the
   extern call, PAGE21 for the global) are present and correctly decoded.
5. Fail-closed on garbage, byte-swapped magic, and truncated command lists.

## What this does not claim

No byte of Mach-O is emitted anywhere. LINK-P1-MACHO-OBJ-MINIMAL (the first
emission row) now has its foundation, but starts from zero emitted bytes.
