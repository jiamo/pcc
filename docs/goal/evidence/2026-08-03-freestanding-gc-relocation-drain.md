# Freestanding Backend 4 relocation-drain evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 4's bounded
object-budget evacuation drain and whole-selected-page evacuation drain,
including page handoff and incomplete-batch accounting.

This slice does not claim ZPage lifecycle or one-epoch forwarding retirement
is migrated.  `LIBC-P2-FREESTANDING-GC` remains `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_relocation_drain.py` exports two public drains and six finite
helper ABIs.  The migration preserves:

- backend and non-positive budget rejection;
- safe next-node capture before a copy removes the current relocation node;
- one safepoint per sixteen successful moves without exception machinery;
- object-budget draining and whole-page draining under the graph lock;
- same-page owner matching through the shared ZPage owner index;
- evacuation-page handoff until the last selected object leaves the page;
- incomplete-batch accounting only when positive progress leaves backlog;
- remap invocation only after the selected set drains and forwarding exists;
- normal `pcc_gc_step` dispatch through the strict page-drain ABI.

The still-managed remap transaction is an explicit
`pcc_gc_backend4_remap_and_retire_unlocked` provider and remains the next
separate ownership boundary.

## Focused gates

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

strict LLVM/self object closure and drain contracts
  4 passed, 1 deselected in 1.27s

strict archive ownership plus C-oracle object/page/step differential
  8 passed in 2.46s (warm content-addressed archive)

adjacent relocation strict suites
  28 passed in 7.18s

existing GC4 handoff/pressure/page-drain/step/source gates
  5 passed, 123 deselected in 0.40s

Python byte compilation and scoped diff hygiene
  exit 0
```

No full five-GC bootstrap matrix was used as a diagnostic loop.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-relocation-drain-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-relocation-drain-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=183220 \
  output=build/freestanding-gc-relocation-drain-stage1/pcc1
```

The profile records 181.715 seconds: zero object-cache hits, 325 misses,
114.942 seconds in native object emission, and 40.904 seconds constructing the
runtime.  The preceding selector profile had 321 hits and four misses, so the
elapsed difference is cache population, not evidence of drain-path slowdown.

`file` reports arm64 Mach-O and `otool -L` reports only
`/usr/lib/libSystem.B.dylib`, not libpython.  That pcc1 compiled the real strict
drain module with `--ir-scaffold=on --backend self --python-libpython=off
--python-library` in 0.326 seconds.  Clang accepted the emitted LLVM IR; all
eight exports are definitions and the undefined set contains no `py_cpy_*`
symbol.

## Scoped hashes

```text
3ee646138420ac6c772b70429dfe5c617a74c778106db09bbf8e61fa51233866  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
7370f89ab59deff1aa8b6ae4d2c17f96ac75c8cee2ae255e83ed7903e2d7b65c  pcc/py_runtime/py/py_gc_backend.py
85c735a7dbff621fed94d6ad753d1cf83be4eba5008accbed56551446b0bdebe  pcc/py_frontend/codegen/runtime_abi.py
564501bbd77cb06a818542e046eaf21b59fef03cdf94fa5b3bb8549614ee8589  pcc/py_runtime/Makefile
d2e6a948b4034af805a9ea875228a57a8fe22b14309b8a72edf23d477a377951  tests/python/test_freestanding_gc_relocation_drain.py
a84f03cb9587c010e023ff67ac2e86e745478c34f036deece4c04c650b1a9a0a  tests/python/test_gc_backend4_production.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the finite ZPage lifecycle/handoff transaction, then the one-epoch
forwarding-retirement transaction and shared write-barrier/dispatcher policy.
Final closure still requires proof that no production GC C object is linked,
the one-shot five-GC semantic/fixed-point matrix, and long-running
RSS/fragmentation/pause/throughput deltas.
