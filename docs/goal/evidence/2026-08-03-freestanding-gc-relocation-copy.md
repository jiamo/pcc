# Freestanding Backend 4 relocation-copy evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4's single-use
relocation copy transaction, in both public locked and internal unlocked forms.
It validates the source, allocates and preserves destination residency, invokes
the strict payload copier, installs forwarding, moves count-on-NEW ownership,
consumes the relocation-set entry, and performs ZPage handoff.

This slice does not claim the selector, evacuation scheduler, ZPage structures,
or one-epoch retirement are migrated.  `LIBC-P2-FREESTANDING-GC` remains
`DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_relocation_copy.py` exports exactly:

- `pcc_gc_backend4_relocate_copy_unlocked`;
- `pcc_gc_relocate_copy`.

The migration preserves:

- Backend 4, non-null, non-tagged, minimum-size, non-pinned preconditions;
- a selected source with no existing forwarding entry;
- finite relocation tag eligibility and known-size upper bound;
- destination ZPage/minor-arena/malloc residency bits across header copy;
- payload rollback on failure and single forwarding installation;
- count-on-NEW refcount transfer and immortal source-shell marking;
- relocation candidate consumption and evacuated-byte accounting;
- removal of an exhausted evacuation page before source ZPage removal;
- one graph-lock boundary around the public copy while page drain uses the
  unlocked form under its existing lock.

## Focused gates

```text
strict source absent
  1 failed in 0.11s (FileNotFoundError)

strict LLVM/self object closure and transaction contracts
  4 passed, 1 deselected in 1.33s

strict production archive ownership
  5 passed in 55.79s

pcc-Python oversize/single-use/step relocation probes
  3 passed in 0.31s

Python byte compilation and scoped diff hygiene
  exit 0
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-relocation-copy-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-relocation-copy-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=73288 \
  output=build/freestanding-gc-relocation-copy-stage1/pcc1
```

The profile records 71.705 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict relocation-copy module with
`--ir-scaffold=on --backend self --python-libpython=off --python-library` in
0.303 seconds.  Clang accepted the emitted LLVM IR; both exports are
definitions and the undefined set contains no `py_cpy_*` symbol.

## Scoped hashes

```text
061336c1d979214b44ec103fa40aea01a6422cd77de19617fe885529a6b15f0b  pcc/py_runtime/py/freestanding_gc_relocation_copy.py
4e925cd7af6056a17474304d1f4e27098c8b1d299e7f82c5450c25966b000338  pcc/py_runtime/py/py_gc_backend.py
9e669510137a8f8d584b1f046e8d0b733972fe30ba67c39831c29bb55ead8736  pcc/py_frontend/codegen/runtime_abi.py
ef0589fd0caca84d0ba89361d34581464f6c79f700318106975f040f8b439aa9  pcc/py_runtime/Makefile
0a9be4c5cd4158111364e316148f6867eead1c4a40e1a336f5d2489bf7525aed  tests/python/test_freestanding_gc_relocation_copy.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move GC4 selector and evacuation/ZPage handoff into finite strict objects,
then move one-epoch forwarding retirement and the remaining shared
write-barrier/dispatcher policy.  Final closure still requires proof that no
production C GC object is linked, the one-shot five-GC semantic/fixed-point
matrix, and long-running RSS/fragmentation/pause/throughput deltas.
