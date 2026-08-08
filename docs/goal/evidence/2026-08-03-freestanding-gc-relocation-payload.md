# Freestanding Backend 4 relocation-payload evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4's shared
slot-pairing and raw relocation payload copier.  The managed GC policy module
consumes this ABI and retains only the still-open relocation object allocation,
selector/ZPage handoff, evacuation/retirement, and one-epoch forwarding
orchestration.

This slice does not claim that all Backend 4 policy or all production C GC has
been removed.  `LIBC-P2-FREESTANDING-GC` remains `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_relocation_payload.py` exports exactly ten raw ABI symbols:

- `pcc_gc_relocate_copy_payload`;
- slot-pair prepare/dispose/count/from/to/copy helpers;
- payload fail/finish helpers;
- continuation-root retargeting.

The migration preserves:

- one shared `pcc_gc_visit_object_slots` graph contract;
- forwarded source-slot healing before value copying;
- owned-reference incref and remembered-slot retargeting;
- continuation mounted-state and registered-root retargeting;
- weakref-list repair and rejection of unsafe live thread/native-handle moves;
- independent traceback, class, list, tuple, dict, set, task, and instance
  payload storage;
- ZPage payload-span registration for relocated out-of-line storage;
- C-extension relocation rejection.

## Focused gates

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

strict LLVM/self object closure and source contracts
  4 passed, 1 deselected in 1.60s

strict production archive ownership
  5 passed in 65.42s

migrated source-owner and shared-slot contract gates
  13 passed in 0.51s

pcc-Python task/scheduler/list/tuple relocation probes
  3 passed in 60.81s

pcc-Python task/set/dict/instance relocation probes
  4 passed in 0.41s

Python byte compilation and scoped diff hygiene
  exit 0
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-relocation-payload-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-relocation-payload-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=76172 \
  output=build/freestanding-gc-relocation-payload-stage1/pcc1
```

The profile records 74.583 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict relocation-payload module with
`--ir-scaffold=on --backend self --python-libpython=off --python-library` in
1.079 seconds.  Clang accepted the emitted LLVM IR; all ten exports are
definitions and the undefined set contains no `py_cpy_*` symbol.

## Scoped hashes

```text
e4636a979d27a5e4f6815a62d492261b8b78a1a6b8399c8a2a15a2e66847f4c4  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
d36bbeb1b99d20fa2c9408ea182a65dd6820aea30258ebcbe7ad1a487394094f  pcc/py_runtime/py/py_gc_backend.py
b59acaad0172d63744730f107a044011302cc97cd3dce4d17d1991eefcbba7ea  pcc/py_frontend/codegen/runtime_abi.py
829c8543faa4df72dd89de9925b0a7068aec349a91ac546bad74c32736e1bb6c  pcc/py_runtime/Makefile
6483b5083c5d5e443fa8069cb6f0099cf2ad6fbad0858aca93a46b6e7232592f  tests/python/test_freestanding_gc_relocation_payload.py
973445bfdecde677a239a96ed7737692059f6343c0c4df832fcf9cb33600a444  tests/python/test_gc_backend_generational.py
1e331c67178db761904d0a1bc9b9ab191963fe2bdf314a4902712fad0e17f2d4  tests/python/test_gc_update_referents.py
170f0e81fce11f197d863656b3a193c5ca29c517d87f3952b2eea4568d045b03  tests/python/test_gc_backend4_production.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move GC4 relocation object allocation, selector/ZPage handoff, evacuation and
one-epoch forwarding retirement into finite strict objects.  Then move the
remaining shared write-barrier/dispatcher policy once it no longer creates a
dependency cycle.  Final closure still requires proof that no production C GC
object is linked, the one-shot five-GC semantic/fixed-point matrix, and
long-running RSS/fragmentation/pause/throughput deltas.
