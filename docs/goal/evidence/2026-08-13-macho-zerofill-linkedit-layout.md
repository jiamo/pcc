# Mach-O zerofill / LINKEDIT layout

Mode: Darwin arm64, pcc-owned Mach-O executable linker, system `cc`/`ld` used
only as a structural oracle.

`__DATA` now has independent compact file and complete VM ends. `__LINKEDIT`
starts at the file-backed DATA end in the file and at the full DATA/BSS end in
VM space; the two offsets remain 16 KiB page-congruent. Large BSS therefore
does not inflate the executable, while a real program reading the final BSS
word still observes zero. The same test builds and parses a system-linked
mixed DATA/BSS oracle and proves the identical invariant. The load-bearing
PAGEOFF12 mask comment now names the actual bit fields and the intentionally
ignored FP/vector V bit.

Gates:

- `tests/python/test_macho_exec_link.py -k 'mixed_data_and_bss or zerofill or linkedit'`: 1 passed.
- `tests/python/test_macho_exec_link.py`: 25 passed.

