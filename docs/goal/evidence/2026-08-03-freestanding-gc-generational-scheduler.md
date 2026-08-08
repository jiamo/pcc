# Freestanding Backend 3 scheduler evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 3 frame-root
promotion invocation, TLS exception-root copy/in-place promotion, remembered
owner drain ordering, and budgeted intrusive young-list scheduling.  Both the
minor-refill path and the five-backend public dispatcher call the strict
Backend 3 step ABI.

The five-backend `pcc_gc_step` selector remains in the managed pcc-Python
policy module because its Backend 4 branches still own relocation work.  The
shared per-type payload copier and Backend 4 relocation policy/remap remain
open, so `LIBC-P2-FREESTANDING-GC` stays `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_generational_scheduler.py` exports exactly three raw ABI
symbols.  The managed backend no longer defines frame-root promotion, TLS-root
promotion, or Backend 3 step scheduling.

The migration preserves:

- frame roots before TLS roots before remembered-owner drain;
- TLS replacement retain-before-store and source release-after-store;
- in-place TLS promotion when copy-oldification is unavailable;
- graph lock coverage over root promotion, remembered drain, and young-list
  mutation;
- strict work budgeting and intrusive head unlink;
- retry by re-linking an object that remains young and unforwarded;
- every-16-items and end-of-nonempty-step safepoints without checked modulo
  exception machinery.

## Focused gates

```text
tests/python/test_freestanding_gc_generational_scheduler.py
  5 passed in 63.67s

real pcc-Python budgeted-list/remembered/TLS/frame-root scheduler gates
  4 passed in 118.69s

migrated generational/thread-safepoint source gates
  2 passed in 0.17s
```

The TDD observations were:

```text
strict source absent
  1 failed in 0.09s (FileNotFoundError)

first strict compile
  rejected unregistered native TLS get/set boundaries;
  2 failed, 2 passed, 1 deselected in 0.44s

exact TLS signatures added without weakening the validator
  4 passed, 1 deselected in 1.49s
```

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-generational-scheduler-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-generational-scheduler-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=35263 \
  output=build/freestanding-gc-generational-scheduler-stage1/pcc1
```

The profile records 33.916 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict scheduler with `--ir-scaffold=on --backend self
--python-libpython=off --python-library`; clang accepted the emitted IR, all
three exports are definitions, and no `call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
6cff6df1bf90fb6a2391eef8351a0aa38fdbe9a3a4081dbe29e6e6c6eb617684  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
db4dd5e1e75a0ef411f184cdaa2fdebf7e217cf6e50fa4969cab686eafabf02a  pcc/py_runtime/py/py_gc_backend.py
41478addfb7eefacb4534248ecf964fd658b1b14fc8bc759ed301ff8fe595f5d  pcc/py_frontend/codegen/runtime_abi.py
be0c3e786b2b7a10c522937fd94a52b0d8651eb33a4ba81f1c168ba63469920c  pcc/py_runtime/Makefile
cb8057165360d13677f8caec898293a52e86ae8aae52b2e42db9c68908ec993d  tests/python/test_freestanding_gc_generational_scheduler.py
19cc4a942c27c68407fb03c74923c718dcb48e63db987c1299ef4b6a59989372  tests/python/test_gc_backend_generational.py
5589cec6400fdd51b73ff6eb0a9f6858b2d00d83c806afed2d91d798ca6d94c2  tests/python/test_gc_threading_substrate.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the shared per-type payload copier and Backend 4 relocation
copy/policy/remap, then move the remaining shared write-barrier/dispatcher
policy once it no longer creates a cycle.  Final closure still requires proof
that no production C GC object is linked, the one-shot five-GC
semantic/fixed-point matrix, and long-running
RSS/fragmentation/pause/throughput deltas.
