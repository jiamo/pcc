# LINK-P1-MACHO-OBJ-RELOC — first slice: BRANCH26, PAGE21, PAGEOFF12 proven

Mode: host pcc, Darwin arm64. Emission is `pcc/backend/macho_obj.py` only —
no as(1), no clang in the emission path.

## What landed

`emit_text_object` now takes `undefined=` (external symbols, emitted into the
undef partition as `N_UNDF|N_EXT`, sorted by name to match as(1)) and
`relocations=` (extern instruction fixups, written in descending r_address
order to match as(1)).

Three relocation types are differentially proven — exactly the set a compiled
extern call and a direct extern address need:

```text
ARM64_RELOC_BRANCH26   (bl _ext_fn)              pcrel, length 2
ARM64_RELOC_PAGE21     (adrp x0, _ext@PAGE)      pcrel, length 2
ARM64_RELOC_PAGEOFF12  (add x0, x0, _ext@PAGEOFF) non-pcrel, length 2
```

The writer holds these in `_PROVEN_RELOCATION_SHAPES` with the pcrel/length
shape each type requires; any other type, or a proven type with the wrong
shape (e.g. non-pcrel BRANCH26 — a corrupt fixup, not a variant), raises
`MachOEmitError`.

## Differential verification (tests/python/test_macho_obj_reloc.py; 15 passed across the three Mach-O suites)

The pinned shape is two functions: `_call_ext` (frame push, `bl _ext_fn`,
frame pop) and `_load_ext` (`adrp`/`add` addressing of `_ext_data`, load),
with fixup fields zero-filled in pcc's machine code.

- **Table equality**: pcc's relocation table equals as(1)'s **entry by
  entry** — (r_address, target symbol by name, type, pcrel, length, extern) —
  on the same machine code.
- **Behavioral equality**: both objects link against the same main
  (providing `ext_fn` returning 42 and `ext_data = 1234`) and both binaries
  return 0 — the BRANCH26 call really lands in `ext_fn` and the PAGE21/
  PAGEOFF12 pair really addresses `ext_data`.
- Undefined symbols land in the dysymtab undef partition with the right
  counts; the emitted object still round-trips byte-for-byte through the
  spec parser.
- Fail-closed on unproven types, wrong shapes, unknown targets, and a symbol
  listed as both defined and undefined.

## What remains on this row

The other arm64 types, each needing its own differential slice when pcc's
emission actually needs it: UNSIGNED (data pointers), ADDEND (offsets into a
target), SUBTRACTOR (label differences), GOT_LOAD_PAGE21/GOT_LOAD_PAGEOFF12 +
POINTER_TO_GOT (GOT-indirect extern data), TLVP_* (thread-locals). Local
(non-extern) relocations and multi-section objects are also outside the
proven subset. The writer fails closed on all of them.

## Update, same day: second slice — GOT_LOAD pair and ADDEND

Three more types differentially proven the same way (18 passed across the
Mach-O suites):

```text
ARM64_RELOC_GOT_LOAD_PAGE21    (adrp x0, _ext@GOTPAGE)         pcrel
ARM64_RELOC_GOT_LOAD_PAGEOFF12 (ldr x0, [x0, _ext@GOTPAGEOFF]) non-pcrel
ARM64_RELOC_ADDEND             (_sym+8@PAGE / @PAGEOFF)        companion entry
```

ADDEND is modeled the way as(1) encodes it: arm64 instruction relocations
carry no addend bits, so a non-zero `Relocation.addend` emits an ADDEND
companion entry (non-extern, value in the 24-bit symbolnum field) immediately
before the entry it modifies, at the same address, in the same descending
order as(1) uses. The runtime proof reads `ext_arr[2]` through `(_ext_arr+8)`
addressing and gets 12, and the GOT path loads `ext_data = 777` through the
linker-materialized GOT slot.

Fail-closed extends to the new surface: an addend on a type it is not proven
for (BRANCH26), and an addend outside the positive 24-bit range, both raise
instead of truncating.

With this, the instruction-fixup family within a single `__text` section is
complete: BRANCH26, PAGE21, PAGEOFF12, GOT_LOAD_PAGE21, GOT_LOAD_PAGEOFF12,
ADDEND. The remaining arm64 types — UNSIGNED, SUBTRACTOR, POINTER_TO_GOT,
TLVP_* — are data-section relocations (`.quad _sym`, pointer tables,
thread-locals) and belong with multi-section support in
LINK-P1-MACHO-OBJ-FULL.

## Update, same day: third slice — multi-section objects and UNSIGNED

The writer generalized from one hardcoded `__text` to a `Section` list
(`emit_object`); `emit_text_object` is now a thin wrapper over it, and all 18
prior tests pass unchanged over the refactor. New differential suite
(`tests/python/test_macho_obj_data_section.py`, 5 passed) pins a
`__TEXT,__text` + `__DATA,__data` object whose table holds two pointers into
an external symbol and one plain integer:

- **UNSIGNED proven**: length 3 (8-byte pointer), non-pcrel, extern; the
  addend lives IN the pointer bytes (`.quad _ext+16` stores 16 in the data),
  NOT in an ADDEND companion — the writer rejects an explicit addend on
  UNSIGNED and the test pins the payload bytes and the entry-by-entry table
  equality with as(1).
- **Cross-section layout matches as(1) exactly**: per-section addr (vmaddr
  accumulates with per-section alignment), size, align, flags, nreloc, and
  the defined symbols' vm addresses (`_table` at 0x10 with `n_sect` 2).
- **PAGE21/PAGEOFF12 against a defined symbol** (the function addressing its
  own module's table) proven in the same shape.
- Runtime: both objects link against the same main; the table's first slot
  relocates to `ext_target`, the second to `ext_target+16` (element 2), the
  plain 42 stays untouched.

Instruction-fixup family + UNSIGNED = 7 arm64 relocation types proven.
Remaining: SUBTRACTOR, POINTER_TO_GOT, TLVP_*, and local (non-extern)
relocations.

## Update, same day: fourth slice — cstring/const/zerofill and local symbols

`tests/python/test_macho_obj_full_sections.py` (5 passed; 28 across the five
Mach-O suites) pins a four-section shape — `__text`, `__TEXT,__cstring`
(S_CSTRING_LITERALS, align 0), `__TEXT,__const`, and a zerofill
`__DATA,__bss` — against as(1) on the same input:

- **Local symbols**: `l_.str` is emitted into the symtab locals partition
  (N_SECT without N_EXT, dysymtab nlocalsym=1) and is still the target of
  PAGE21/PAGEOFF12 relocations — pinning that r_extern=1 means "symbolnum is
  a symtab index", not "the symbol is exported".
- **Zerofill**: `__bss` carries no file payload (offset 0), occupies vm space
  only, and the writer refuses payloads on zerofill, zerofill_size on
  regular sections, and any content section ordered after a zerofill
  (ld would reject the object later; failing at emit time beats that).
- Layout equality with as(1) on all four sections (addr accumulation across
  mixed alignments 2/0/3/3, size, flags, nreloc) and symbol equality
  including the locals split; runtime behavior identical (strcmp of the
  cstring through adrp/add, const table reads, bss write/read).

Remaining on OBJ-FULL after this: mod-init pointers, unwind info
(__compact_unwind / __eh_frame), and data-in-code — plus whatever additional
sections real pcc programs prove to need when OBJ-SWITCH routes actual
compiler output through this writer.
