# LINK-P1-MACHO-OBJ-RELOC — complete finite relocation inventory

Mode: host pcc object writer on Darwin arm64, compared with the system
assembler/linker.  This is not yet pcc1/bootstrap evidence.

## Current-source gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_macho_obj_reloc_remaining.py
5 passed in 0.90s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_macho_obj_reloc.py \
  tests/python/test_macho_obj_data_section.py
13 passed in 0.59s
```

The remaining inventory now has explicit oracle semantics:

- SUBTRACTOR/UNSIGNED pairs preserve their ordered pair and run after both
  direct and relocatable linking.
- both POINTER_TO_GOT encodings match `as(1)`.  The four-byte PC-relative
  form final-links and runs.  The eight-byte absolute form is preserved by
  relocatable linking, while final `ld64` rejects both the pcc and `as(1)`
  objects with the same unsupported-relocation diagnostic.
- TLVP page/pageoff pairs match `as(1)` and execute against real TLS.
- pcc's `r_extern=0` section-target form is behaviorally equivalent to the
  local-temporary-symbol form selected by `as(1)`, including the embedded
  target addend, and survives pcc's relocatable merge.
- malformed half-pairs and unsupported shapes fail before publication.

## Remaining boundary

The row remains `DONE_WEAK`: the current-source bootstrap baseline/pcc1 gate
has not run after this source freeze.  Object-level and system-link evidence
must not be described as self-host evidence.
