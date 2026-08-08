# Mach-O unwind/debug-section contract

Mode: Darwin arm64, pcc-owned object writer and executable linker, system
assembler used as a byte/relocation oracle.

Compact-unwind debug sections keep the strict no-symbol input contract. A live
reference into dropped metadata is modeled with the real Mach-O section-target
relocation form and rejected explicitly; unreferenced compact unwind reaches
the executable linker, is visibly dropped, and the executable runs. Defined
function pointers inside compact-unwind rows now use the same section-target
relocations as `as(1)` (including globally visible functions), while undefined
personality/LSDA references remain symbol relocations.

Gates:

- Exact unwind regression: 1 passed.
- `tests/python/test_macho_obj_remaining_sections.py tests/python/test_macho_exec_link.py`: 32 passed.

