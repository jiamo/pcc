# LINK-P1-MACHO-OBJ-FULL — current-source focused object evidence

Mode: host pcc object writer on Darwin arm64.  This evidence is structural,
differential and local-runtime only; it is not a pcc1/runtime-archive claim.

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_macho_obj_full_sections.py \
  tests/python/test_macho_obj_remaining_sections.py
12 passed in 0.86s

gtimeout 120s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_macho_obj_minimal.py \
  tests/python/test_macho_spec.py
10 passed in 0.49s
```

The focused suite covers DATA_IN_CODE, constructors/mod-init pointers,
compact-unwind emission, private extern symbols, full section/string/symbol
layout and malformed-shape rejection, alongside the minimal/spec baseline.

The remaining acceptance boundary is deliberate: prove that the current
runtime archive and a working current-source pcc1 are built entirely from
pcc-emitted objects, then run the bootstrap baseline.  Until then this row is
`DONE_WEAK`, not `DONE_STRONG`.
