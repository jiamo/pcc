# Freestanding Backend 4 ZPage-lifecycle evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4's finite ZPage
cache, unlink, owner removal, and payload-span cleanup lifecycle.

This slice does not claim one-epoch forwarding retirement or shared
write-barrier/dispatcher policy is migrated. `LIBC-P2-FREESTANDING-GC`
remains `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_zpage_lifecycle.py` uniquely exports fifteen ABIs covering:

- per-class cache counts and the small=8/medium=4 limits;
- clear/cache/recycle/correctness-first retained-page retirement;
- page-local and global node unlinking plus owner-index removal;
- object-node/index-assisted owner lookup;
- page-list unlink and current owner selection;
- per-owner payload-span chain cleanup and base-specific unregister;
- the public owner-removal transaction, including forwarding-safe zombie
  pages.

The migration preserves raw page/node/payload layouts, bounded free-list
scans, large-page non-reuse, per-owner `O(own spans)` cleanup, pending
allocation handling, and the rule that a page with live forwardings stays
mapped and non-reusable.

## Focused gates

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

strict LLVM/self object closure and lifecycle contracts
  4 passed, 2 deselected in 1.52s

strict archive ownership plus C-oracle cache/reuse/large-retire differential
  6 passed in 65.57s (one content-addressed archive rebuild)

old Backend 4 owner-index and source wiring gates
  2 passed alongside strict source gates

adjacent ZPage lifecycle/mechanics/allocation/relocation suites
  36 passed in 10.59s

Python byte compilation and scoped diff hygiene
  exit 0
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-zpage-lifecycle-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-zpage-lifecycle-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=78630 \
  output=build/freestanding-gc-zpage-lifecycle-stage1/pcc1
```

The profile records 77.012 seconds, 321 self-object cache hits, four misses,
4.262 seconds in native object emission, and 45.352 seconds constructing the
changed runtime archive. `file` reports arm64 Mach-O and `otool -L` reports
only `/usr/lib/libSystem.B.dylib`, not libpython.

That pcc1 compiled the real strict module with `--ir-scaffold=on --backend
self --python-libpython=off --python-library` in 0.580 seconds. Clang accepted
the emitted LLVM IR; exactly fifteen exports are definitions and the
undefined set contains no `py_cpy_*` symbol.

## Scoped hashes

```text
870e8d9f9a53d9746df0e2bddcbf003ae5be6e04bce159e4826a1f66ab07fe18  pcc/py_runtime/py/freestanding_gc_zpage_lifecycle.py
a4d1eaeab41eaee84a65ef9c3b08f1eefa50dde0485d5a89db753a8113a5b912  pcc/py_runtime/py/freestanding_gc_zpage_mechanics.py
5ef84f106fe30390d0ff6067226d79751b75f22b672e071949f3e9e6c5b10786  pcc/py_runtime/py/py_gc_backend.py
3869c293b1454724c4ab30db48debff2f3f15c56233b177e007562e24bb570a0  pcc/py_frontend/codegen/runtime_abi.py
aac2c7ebd76d1a164e78d6792e5732c15ab1ffa71c06636effd44eb683d83814  pcc/py_runtime/Makefile
59f56a71b27dda219b4baf441a65a037b89318f11e793c0e203cb2d9d5e42a0d  tests/python/test_freestanding_gc_zpage_lifecycle.py
3bf00a747fbccc39fffaff3ae4fbd363c3732a915ddf5acc81b0a1070c079e93  tests/python/test_freestanding_gc_zpage_mechanics.py
bab66dfc1f3d839c7849293d71cc88f3d3b01474a5392e0fbaee48318a3b8796  tests/python/test_gc_backend4_production.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the one-epoch forwarding-retirement transaction and shared
write-barrier/dispatcher policy. Final closure still requires no-C GC link-map
proof, the one-shot five-GC semantic/fixed-point matrix, and long-running
RSS/fragmentation/pause/throughput deltas.
