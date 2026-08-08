# Freestanding Backend 4 ZPage-allocation evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4's raw ZPage
allocation and allocation-to-owner registration transactions.

This slice does not claim the page/node providers, empty-page cache/removal,
or forwarding retirement are migrated.  `LIBC-P2-FREESTANDING-GC` remains
`DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_zpage_allocation.py` uniquely exports:

- `pcc_gc_backend4_try_zpage_alloc`;
- `pcc_gc_backend4_zpage_track_alloc`.

The migration preserves:

- backend/config and minimum-object-size rejection;
- eight-byte allocation alignment and small/medium/large class selection;
- young/old active-page separation;
- refusal to allocate from an evacuation page;
- active, free-cache, and new-page selection order;
- span capacity/allocated validation before carving;
- pending-owner handoff from raw allocation to owner registration;
- physical owner offsets for small/medium shared spans;
- dedicated large-page spans;
- owner-index node linking and page count/used/capacity accounting.

Eleven existing page/node helpers are explicit signature-checked providers;
they are the next migration boundary rather than hidden calls.

## Focused gates

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

strict LLVM/self object closure and allocation contracts
  4 passed, 1 deselected in 1.37s

strict archive ownership plus small/medium/large C-oracle differential
  8 passed in 72.40s (one content-addressed archive rebuild)

existing ZPage allocation/source gates
  8 passed, 120 deselected in 0.86s

adjacent strict relocation/allocation suites
  26 passed in 8.07s

Python byte compilation and scoped diff hygiene
  exit 0
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-zpage-allocation-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-zpage-allocation-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=79983 \
  output=build/freestanding-gc-zpage-allocation-stage1/pcc1
```

The profile records 78.343 seconds, 321 self-object cache hits, four misses,
4.623 seconds in native object emission, and 45.480 seconds constructing the
changed runtime archive.  `file` reports arm64 Mach-O and `otool -L` reports
only `/usr/lib/libSystem.B.dylib`, not libpython.

That pcc1 compiled the real strict module with `--ir-scaffold=on --backend
self --python-libpython=off --python-library` in 0.715 seconds.  Clang accepted
the emitted LLVM IR; both exports are definitions and the undefined set
contains no `py_cpy_*` symbol.

## Scoped hashes

```text
38af12ad139a04d43c9712cc4232723dabd7ef78a83253f12f885b7132ed3f2d  pcc/py_runtime/py/freestanding_gc_zpage_allocation.py
ca8b163012215128e13ce212bef7b14303c67d1f805b88a4e2034c3109fd2c04  pcc/py_runtime/py/py_gc_backend.py
824f370bdb043af96b0303cd48daf096ad41c9bd3f1dc23a70f096b60c9cd737  pcc/py_frontend/codegen/runtime_abi.py
8c2fea87a7653599bd486e3484c87886ef55e899e26952f2377a51c340d5a28f  pcc/py_runtime/Makefile
1d87948d38681c9710c348dda4bc134b1f49a01710beae3f25d946dc3ee1d984  tests/python/test_freestanding_gc_zpage_allocation.py
5ed41dad4a148ff3e5e428b609e4777d646d93fea9c1c1600edda7710d339d8f  tests/python/test_gc_backend4_production.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the eleven page/node provider mechanics and the finite empty-page
cache/removal lifecycle, then the one-epoch forwarding-retirement transaction
and shared write-barrier/dispatcher policy.  Final closure still requires no-C
GC link-map proof, the one-shot five-GC semantic/fixed-point matrix, and
long-running RSS/fragmentation/pause/throughput deltas.
