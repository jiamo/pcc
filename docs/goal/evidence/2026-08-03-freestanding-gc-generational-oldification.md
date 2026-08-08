# Freestanding Backend 3 oldification evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns Backend 3 scalar-tag
admission and copy-oldification orchestration: copy allocation, old-object
flags, object-node/index registration, forwarding install, rollback, young-list
unlink, source inactivation, and live-byte accounting.

The shared per-type payload copier remains one explicit cross-object ABI in
the managed policy module because it also owns Backend 4 container,
continuation, remembered-set, and zpage behavior.  Promotion slot/root
rewriting and the Backend 3 dispatcher remain open, so
`LIBC-P2-FREESTANDING-GC` remains `DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_generational_oldification.py` exports exactly three raw ABI
symbols.  The managed backend calls the strict oldify function and no longer
defines scalar admission, oldify orchestration, or source inactivation.
Production archive inspection reports one owner for each moved symbol.

The migration preserves:

- oldification only under Backend 3, only for known young unpinned scalar
  objects of known size;
- old-copy malloc residency and old/non-minor flags;
- payload copy before node/index publication;
- live-byte publication before forwarding installation;
- complete rollback of index/list/node/live-bytes/identity/object allocation
  when forwarding installation fails;
- forwarding installation before young-list unlink and source inactivation;
- forwarded source live-byte subtraction and `freeing` marker.

## Focused gates

```text
tests/python/test_freestanding_gc_generational_oldification.py
  5 passed in 66.03s

real pcc-Python remembered-child copy, source inactivity, forwarded-source release
  3 passed in 62.24s
```

The TDD observations were:

```text
strict source absent
  1 failed in 0.09s (FileNotFoundError)

first strict compile
  rejected missing object-index insert/remove cross-object signatures;
  2 failed, 2 passed, 1 deselected in 0.53s

exact signatures added without weakening the validator
  4 passed, 1 deselected in 1.54s
```

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-generational-oldification-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-generational-oldification-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=85823 \
  output=build/freestanding-gc-generational-oldification-stage1/pcc1
```

The profile records 83.907 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict module with `--ir-scaffold=on --backend self
--python-libpython off --python-library`; clang accepted the emitted IR, all
three exports are definitions, and no `call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
0de7695e3fe8435ff4cd799f270ab3a0b393e9fc718df108275f62a02b0e9d8f  pcc/py_runtime/py/freestanding_gc_generational_oldification.py
646e8338e6e3a2fded37407aa8bc36f932d224c1bbdfa40c9db95daef5e22db2  pcc/py_runtime/py/py_gc_backend.py
2e5fbe0ef14efe7a7b037d54a23326786ccb2bd5cd7c58d20b626b54a7da0cb0  pcc/py_frontend/codegen/runtime_abi.py
fe041f2536d8e84c28fdd8e2af7503d3a97dd673f213663ab8245e9335f85b06  pcc/py_runtime/Makefile
2609b8ad6cd45c4fee76e1344d934fb9a0ac4b5419689df4b230d0574833250b  tests/python/test_freestanding_gc_generational_oldification.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move Backend 3 promotion/remembered-root rewriting and dispatcher.  Then move
the shared per-type payload copier together with Backend 4 relocation
copy/policy/remap.  Final closure still requires proof that no production C GC
object is linked, the one-shot five-GC semantic/fixed-point matrix, and
long-running RSS/fragmentation/pause/throughput deltas.
