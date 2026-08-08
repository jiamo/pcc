# Freestanding generational promotion evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns shared GC3/GC4 young-object
promotion, owned and borrowed slot rewriting, deep/shallow object-slot
visitors, promotion referent traversal, stable-root classification, and cached
frame-root rewriting.  Container/type coverage still comes from the single
strict `pcc_gc_visit_object_slots` contract; no per-type switch was copied.

Backend 4's zpage generation update remains one explicit managed pcc-Python
ABI consumed by the strict promotion object.  TLS exception-root orchestration
and the Backend 3 step dispatcher remain open, as does Backend 4 relocation
policy/remap, so `LIBC-P2-FREESTANDING-GC` stays `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_generational_promotion.py` exports exactly ten raw ABI
symbols, including the callback/helper functions that the fail-closed strict
frontend requires to have explicit names.  The managed backend no longer
defines promotion, promotion traversal, stable-root classification, or cached
frame-slot rewriting.

The migration preserves:

- pointer/header admission before object-header reads;
- forwarding-aware unknown-object handling;
- copy-oldification before in-place old marking;
- owned slots retaining the replacement before store and releasing the source
  after store;
- borrowed slots rewriting without reference-count transfer;
- deep versus shallow recursion behavior;
- role-driven use of the one object-slot walker, including borrowed
  update-only metadata;
- Backend 4 zpage generation notification for in-place promotion;
- stale stable-cache rejection and stable-cache invalidation when a rewritten
  root is not yet stable.

## Focused gates

```text
tests/python/test_freestanding_gc_generational_promotion.py
  5 passed in 63.44s

real pcc-Python TLS/list/frame/generator/continuation/class promotion gates
  6 passed in 63.29s

tests/python/test_freestanding_gc_mapped_roots.py
  5 passed in 18.21s

migrated slot-contract/source-shape gates
  14 passed
```

The TDD observations were:

```text
strict source absent
  1 failed in 0.09s (FileNotFoundError)

first strict compile
  rejected unnamed private helpers in a freestanding module;
  2 failed, 2 passed, 1 deselected in 0.38s

second strict compile
  rejected the unregistered cross-object oldification ABI;
  2 failed, 2 passed, 1 deselected in 0.64s

explicit finite helper ABIs plus exact oldification signatures
  4 passed, 1 deselected in 1.66s
```

The validator was not weakened: helper callbacks received finite explicit
names, and the three already-existing oldification exports received exact
cross-object signatures.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-generational-promotion-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-generational-promotion-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=77356 \
  output=build/freestanding-gc-generational-promotion-stage1/pcc1
```

The profile records 75.659 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict module with `--ir-scaffold=on --backend self
--python-libpython=off --python-library`; clang accepted the emitted IR, all
ten exports are definitions, and no `call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
4a2f0e6b59c88f9b9475f6b5c6c47b927e2e1e4e931977e5d0f2f35ff51885e6  pcc/py_runtime/py/freestanding_gc_generational_promotion.py
0e9590cb4edaca94a5ffed183b178859f90219506990f9991df9dff39e3130f3  pcc/py_runtime/py/py_gc_backend.py
42234dd522b0d87bfe3ea1022eaa88a15172d327c092999d1c232698959afa69  pcc/py_frontend/codegen/runtime_abi.py
e6bc12d5f52f6365f00057b5dd05becc3fe02246d3a2dacffcb45a0597f2d541  pcc/py_runtime/Makefile
a7df2b53421b58d01aa478be7ef7816636830f7f23d7f42c5a26550e1a89571f  tests/python/test_freestanding_gc_generational_promotion.py
005dd6cbf05c2e689ac5f10b69e8c2dab833acdfbd5959ed149ea025e9508d0d  tests/python/test_gc_backend_generational.py
4e711edb74b4637bebbd1f6f2c4fc1898d53659f910bdbcebe78b3dd0677a3ed  tests/python/test_gc_update_referents.py
bf73108c9959c3f1f5ed11869f93d96a1e3e17c6948767e5542e59f8474fbeca  tests/python/test_gc_backend4_production.py
c5c627a88b4a3ccb4d222f0d5f357e0f0b3eb9baec54c7683b57fd6b2f9efa29  tests/python/test_freestanding_gc_mapped_roots.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move Backend 3 TLS/root orchestration and the Backend 3 step dispatcher.  Then
move the shared per-type payload copier with Backend 4 relocation
copy/policy/remap.  Final closure still requires proof that no production C GC
object is linked, the one-shot five-GC semantic/fixed-point matrix, and
long-running RSS/fragmentation/pause/throughput deltas.
