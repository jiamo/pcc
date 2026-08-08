# Freestanding Backend 4 ZPage-mechanics evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4's eleven ZPage
page/node provider mechanics.

This slice does not claim the empty-page cache/removal transaction,
one-epoch forwarding retirement, or shared barrier/dispatcher policy is
migrated. `LIBC-P2-FREESTANDING-GC` remains `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_zpage_mechanics.py` uniquely exports:

- active-page lookup, set, and clear;
- reusable-page lookup by owner generation or explicit generation;
- free-page pop and page reset;
- address-to-page lookup;
- bounded node allocation/release;
- node linking into global/page lists and the owner index.

The migration preserves eight-byte allocation alignment, small/medium/large
capacity classes, young/old active-page separation, evacuation-page
exclusion, span guards and address bounds, the 8192-node pool limit, and the
existing raw page/node layouts.

## Focused gates

```text
strict source absent
  1 failed in 0.11s (FileNotFoundError)

strict LLVM/self object closure and layout contracts
  4 passed, 2 deselected in 2.94s

strict archive ownership plus direct active/free/node state machines
  6 passed in 93.65s (one content-addressed archive rebuild)

existing Backend 4 owner-index and source wiring gates
  2 passed in 12.46s

adjacent ZPage allocation, relocation selector/drain and mechanics suites
  29 passed in 21.08s

Python byte compilation and scoped diff hygiene
  exit 0
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-zpage-mechanics-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-zpage-mechanics-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=89906 \
  output=build/freestanding-gc-zpage-mechanics-stage1/pcc1
```

The profile records 88.097 seconds, 321 self-object cache hits, four misses,
4.401 seconds in native object emission, and 45.846 seconds constructing the
changed runtime archive. `file` reports arm64 Mach-O and `otool -L` reports
only `/usr/lib/libSystem.B.dylib`, not libpython.

That pcc1 compiled the real strict module with `--ir-scaffold=on --backend
self --python-libpython=off --python-library` in 0.563 seconds. Clang accepted
the emitted LLVM IR; exactly eleven exports are definitions and the undefined
set contains no `py_cpy_*` symbol.

## Scoped hashes

```text
a4d1eaeab41eaee84a65ef9c3b08f1eefa50dde0485d5a89db753a8113a5b912  pcc/py_runtime/py/freestanding_gc_zpage_mechanics.py
9c92fe6a7d13c2d74f00e68c1b21c14db0ba77347211d82ef2a7e69057c06f69  pcc/py_runtime/py/py_gc_backend.py
956581b493c41d2d43ca039f6fa6b2ca86ed950eb6f780f41c21339aebf30e7b  pcc/py_frontend/codegen/runtime_abi.py
56a0bcb2c8c2c5c0bbe078375b16157c1a681b0f21314eb482e83c81d3363c45  pcc/py_runtime/Makefile
3bf00a747fbccc39fffaff3ae4fbd363c3732a915ddf5acc81b0a1070c079e93  tests/python/test_freestanding_gc_zpage_mechanics.py
d53a4cbf818390507c9f53638d9219bb16c2c9c9360cff76556992d09d7c1c51  tests/python/test_gc_backend4_production.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the finite empty-page cache/removal lifecycle, then the one-epoch
forwarding-retirement transaction and shared write-barrier/dispatcher policy.
Final closure still requires no-C GC link-map proof, the one-shot five-GC
semantic/fixed-point matrix, and long-running
RSS/fragmentation/pause/throughput deltas.
