# Freestanding Backend 3 remembered-owner evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns the Backend 3
remembered-owner queue, allocation-failure overflow marker, whole-object-list
overflow scan, and budgeted queue drain.  The write barrier, backend reset, and
minor-promotion step consume these operations through exact raw ABIs rather
than carrying duplicate queue policy in `py_gc_backend.py`.

Promotion of the referenced slots and roots still lives in the managed
pcc-Python policy module, as does the Backend 3 step dispatcher.  Backend 4
relocation policy/remap also remains open, so `LIBC-P2-FREESTANDING-GC` stays
`DONE_WEAK`.

## Ownership and preserved contracts

`freestanding_gc_generational_remembered_owners.py` exports exactly six raw
ABI symbols.  Its list-head accessors deliberately use `..._list_head` names
because `pcc_gc_backend3_remembered_owner_head` is the storage global; this
prevents a function/global symbol collision.

The migration preserves:

- duplicate suppression through the owner remembered bit;
- allocation failure setting both the global overflow marker and owner bit;
- queue publication before the owner bit becomes visible;
- clear detaching the whole queue and resetting overflow before freeing nodes;
- overflow drain falling back to an active-object-list scan;
- strict work budgeting for both list scan and queue drain;
- tracing the owner before clearing its remembered bit;
- safepoints after each 16 processed owners without pulling exception machinery
  into the strict object.

## Focused gates

```text
tests/python/test_freestanding_gc_generational_remembered_owners.py
  5 passed in 62.53s

real pcc-Python remembered-child/refill/copy/list-slot gates plus C barrier gates
  5 passed in 133.47s
```

The TDD observations were:

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

first strict compile
  rejected `% 16` because checked modulo emitted py_exc_new;
  2 failed, 2 passed, 1 deselected in 0.51s

equivalent `(processed & 15) == 0` safepoint cadence
  4 passed, 1 deselected in 1.49s
```

The strict LLVM/self closure proves the exact eight-function/two-global raw
dependency set, and production archive inspection proves one owner for every
moved symbol.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-generational-remembered-owners-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-generational-remembered-owners-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=34301 \
  output=build/freestanding-gc-generational-remembered-owners-stage1/pcc1
```

The profile records 33.027 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict module with `--ir-scaffold=on --backend self
--python-libpython=off --python-library`; clang accepted the emitted IR, all
six exports are definitions, and no `call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
63f5041339b6f6d88c69c051cc5453daa110c482d7ff2991480f2e2cf6dd3398  pcc/py_runtime/py/freestanding_gc_generational_remembered_owners.py
d24443c51e943fcfec74178103b6d37ff770157e18209e0589b119f68cc3362a  pcc/py_runtime/py/py_gc_backend.py
d57d92d1446f0d3985892a501160c6c7a4c4cfdc1361cc74864c0f8d71f5e019  pcc/py_frontend/codegen/runtime_abi.py
4a7a0d6efcc22e34048aec290c3d094714f0348b0be6a31ff4238fac5f314900  pcc/py_runtime/Makefile
157a5f90e89edbc732931d931d3a681bb6b9426b669a3fdfad267fcfbe17f929  tests/python/test_freestanding_gc_generational_remembered_owners.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move Backend 3 promotion slot/root traversal, TLS/root rewriting, and the
Backend 3 step dispatcher.  Then move the shared per-type payload copier with
Backend 4 relocation copy/policy/remap.  Final closure still requires proof
that no production C GC object is linked, the one-shot five-GC
semantic/fixed-point matrix, and long-running
RSS/fragmentation/pause/throughput deltas.
