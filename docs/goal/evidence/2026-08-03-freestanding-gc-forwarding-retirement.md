# Freestanding Backend 4 forwarding-retirement evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4 forwarding
removal, one-epoch source-object retirement, and delayed source-page
destruction.

This slice does not claim the shared write-barrier/dispatcher policy is
migrated. `LIBC-P2-FREESTANDING-GC` remains `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_forwarding_retirement.py` uniquely exports seven ABIs:

- park and drain retired ZPages;
- decrement per-page pending-forwarding counts and park only empty zombies;
- remove one forwarding by source or all forwardings by target;
- heal live-object and registered-root slots, then retain each old source for
  one epoch before removing identity, object-node, and forwarding state.

The migration preserves target ownership, source-page accounting, forwarding
indexes and population, weakref/root remap order, live-byte accounting, and
the rule that a page cannot be destroyed while any forwarding entry still
references it.

## Focused gates

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

strict LLVM/self object closure and retirement contracts
  4 passed, 2 deselected in 1.23s

strict archive ownership plus three-step C-oracle differential
  6 passed in 63.48s after correcting the test's guessed oracle sequence
  6 passed in 1.60s on the corrected oracle contract

adjacent relocation/ZPage plus weakref and retained-page gates
  37 passed in 8.87s

old remap/root-walker source gates
  5 passed, 1 deselected in 1.03s

Python byte compilation and scoped diff hygiene
  exit 0
```

The retained C oracle and pcc-Python archive both produced:

```text
2
0
0
0,1
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-forwarding-retirement-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-forwarding-retirement-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=71413 \
  output=build/freestanding-gc-forwarding-retirement-stage1/pcc1
```

The profile records 69.960 seconds, 321 self-object cache hits, four misses,
3.917 seconds in native object emission, and 41.000 seconds constructing the
changed runtime archive. `file` reports arm64 Mach-O and `otool -L` reports
only `/usr/lib/libSystem.B.dylib`, not libpython.

That pcc1 compiled the real strict module with `--ir-scaffold=on --backend
self --python-libpython=off --python-library` in 0.381 seconds. Clang accepted
the emitted LLVM IR; exactly seven exports are definitions and the undefined
set contains no `py_cpy_*` symbol.

## Scoped hashes

```text
9367ef91c5c316caebfae891e57ed1a084b0a8e258872cf9a25197ffd2610903  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
cbf6dfd4ff8abd19b016646a682d11659ffda273a2002450e3d958055bac86cf  pcc/py_runtime/py/freestanding_gc_relocation_remap.py
c62af31953e4f048ea61f768447d14834c9953bc558959b74491c152ccd9641e  pcc/py_runtime/py/py_gc_backend.py
3a21da907d67b8856ca3d08a9746d6ee0f8ee3e7b78746a54156edae927a4362  pcc/py_frontend/codegen/runtime_abi.py
a6273d179c0dd5e1564963818ff3d3b29ab2ac74a21d1ec6faf34e0572d06658  pcc/py_runtime/Makefile
f3cd01ccbbe64cadbebecfa4908c95ffac1c97c02e164d3295ffa393d95964a4  tests/python/test_freestanding_gc_forwarding_retirement.py
ae06083a67a1628b2fb5c13f29de5dd598d2ed9d40f7b7e58e09e34eb8e07852  tests/python/test_freestanding_gc_relocation_remap.py
61d001d773ffc8e0bc51e7a671ed98c4e0df138733c8fd07d8742799f26480a2  tests/python/test_gc_backend_generational.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the shared write-barrier/dispatcher policy. Final closure still requires
the full suspended-frame/scheduler/C-extension-root/relocation/synchronization
proof, a no-production-C GC link map, the one-shot five-GC semantic/fixed-point
matrix, and long-running RSS/fragmentation/pause/throughput deltas.
