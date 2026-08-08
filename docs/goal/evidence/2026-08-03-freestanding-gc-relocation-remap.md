# Freestanding Backend 4 relocation-remap evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4 relocation tag
eligibility, one-slot forwarding healing, and referent remapping through the
shared object-slot visitor.  The managed GC policy module consumes these ABIs
and retains only the still-open relocation payload/copy/selection/ZPage and
one-epoch retirement orchestration.

This slice does not claim that all Backend 4 policy or all production C GC has
been removed.  `LIBC-P2-FREESTANDING-GC` remains `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_relocation_remap.py` exports exactly four raw ABI symbols:

- `pcc_gc_backend4_relocate_copy_supported_tag`;
- `pcc_gc_backend4_remap_heal_slot`;
- `pcc_gc_backend4_remap_slot`;
- `pcc_gc_backend4_remap_referents`.

The migration preserves:

- C-extension types remain non-relocatable;
- ordinary instances and the finite built-in relocatable tag set retain their
  previous eligibility;
- forwarded-slot healing changes pointer bits without double-adjusting the
  count-on-NEW reference accounting;
- referent enumeration comes only from `pcc_gc_visit_object_slots`;
- managed retirement still heals heap and registered roots before setting the
  `RETIRING` flag, and only retires on the following remap epoch.

## Focused gates

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

strict LLVM/self object closure and source contracts
  4 passed, 1 deselected in 1.44s

strict production archive ownership
  5 passed in 64.34s

migrated slot/type/classification source gates
  9 passed in 0.50s

pcc-Python scheduler/list-copy/phase-reset relocation probes
  3 passed in 0.35s

compiled Python step/forward/remap probe
  1 passed in 61.20s

GC3 stale source-route regressions repaired after scheduler extraction
  3 passed in 0.18s
```

An earlier four-probe aggregate was stopped by its 120-second outer watchdog
after three dots and was not counted as evidence because it lacked a pytest
summary.  Process inspection found no surviving pytest, pcc, bootstrap, or
clang child.  The first three probes were rerun with a complete summary above;
the fourth was run separately with its own complete summary.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-relocation-remap-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-relocation-remap-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=33962 \
  output=build/freestanding-gc-relocation-remap-stage1/pcc1
```

The profile records 32.730 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict relocation-remap module with
`--ir-scaffold=on --backend self --python-libpython=off --python-library`.
Clang accepted the emitted LLVM IR; all four exports are definitions and no
`call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
cbf6dfd4ff8abd19b016646a682d11659ffda273a2002450e3d958055bac86cf  pcc/py_runtime/py/freestanding_gc_relocation_remap.py
e1002a5458408c230f311a454d4dc55b4c68ea1337880a255c82162aeb1953cf  pcc/py_runtime/py/py_gc_backend.py
587d163161cd9653e4f713d25a731145c15a7a6a391bb0dbb8692d260e366d8a  pcc/py_frontend/codegen/runtime_abi.py
cef759e4343a5c59db338da2b094fa84ea7bcedec288192946486435bbbdafef  pcc/py_runtime/Makefile
189ccc51ab4a60c320e2548b3518433f5544bf794b136aa5ad7e53726806bc18  tests/python/test_freestanding_gc_relocation_remap.py
760f8a3452dacd72e63bd690ddf1642779522f085efdfd947d4e2dd9d3f9d7f1  tests/python/test_gc_backend_generational.py
335267eb828b9aa59879d91df4c3603c54b0f8a82da5530250659cf714582d98  tests/python/test_gc_update_referents.py
84255682b1f82534513487652f7c9443e65651914bcf97d05fd49d21fd312cde  tests/python/test_gc_backend4_production.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the shared raw payload copier, relocation copy/selector/ZPage handoff, and
one-epoch retirement orchestration into finite strict Backend 4 objects.  Then
move the remaining shared write-barrier/dispatcher policy once it no longer
creates a dependency cycle.  Final closure still requires proof that no
production C GC object is linked, the one-shot five-GC semantic/fixed-point
matrix, and long-running RSS/fragmentation/pause/throughput deltas.
