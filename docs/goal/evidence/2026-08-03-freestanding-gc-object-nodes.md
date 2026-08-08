# Freestanding GC object-node/young-list evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns the raw 80-byte object-node
layout accessors, bounded node free pool, tracked object-list head/unlink,
trace cursor adjustment, Backend 3 intrusive young-list link/unlink/rebuild,
known-size lookup, and saturating live-byte subtraction.

Per-type oldification payload copying, promotion slot rewriting, remembered
owner drain, and the Backend 3 step dispatcher remain in the managed policy
module.  `LIBC-P2-FREESTANDING-GC` therefore remains `DONE_WEAK`.

## Ownership and preserved invariants

`freestanding_gc_object_nodes.py` exports exactly 30 raw ABI symbols.  The
managed backend consumes them through explicit extern declarations and no
longer defines a second node/list/worklist implementation.  LLVM and self
emission have the same finite undefined closure, and every moved symbol has
one production archive owner in `freestanding_gc_object_nodes.o`.

The migration preserves:

- the 8192-node free-pool cap;
- trace-cursor advance before an object node is unlinked;
- simultaneous removal from the intrusive young list and object list;
- young-list rebuild only for active nodes whose object has the young bit;
- known-size lookup only for indexed nodes not marked freeing;
- live-byte subtraction saturating at zero.

## Focused gates

```text
tests/python/test_freestanding_gc_object_nodes.py
  5 passed in 64.32s

GC3 young-worklist source shape + real pcc-Python oldify + forwarded-source inactivity
  3 passed in 60.89s
```

The TDD observations were:

```text
strict source absent
  1 failed in 0.09s (FileNotFoundError)

strict source and managed extern routing
  1 passed in 0.06s

LLVM/self exact closure and structural invariants
  4 passed, 1 deselected in 1.49s
```

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-object-nodes-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-object-nodes-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=81383 \
  output=build/freestanding-gc-object-nodes-stage1/pcc1
```

The profile records 79.533 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict module with `--ir-scaffold=on --backend self
--python-libpython off --python-library`; clang accepted the emitted IR, all
30 exports are definitions, and no `call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
7b696b02bee46525387c275a7e571945bfa863a0c2a48c3fb19d3f9dfd123e56  pcc/py_runtime/py/freestanding_gc_object_nodes.py
18f9a626001e2a34df18bcf6135c96654eb2334055194bcb74857e7a7694513e  pcc/py_runtime/py/py_gc_backend.py
a972c02c784de49ec232cad61efe3f91b7715b9184767647f3ba4aa0f0811b45  pcc/py_frontend/codegen/runtime_abi.py
edf9526fd109a9fb8e3e4dc6b15103e47a27c4d48b2b513c9f2739e1cd66c23c  pcc/py_runtime/Makefile
887a02fd0422dfc368d458afe01b7d2ad48120e92c9903f932144d79e6a63adf  tests/python/test_freestanding_gc_object_nodes.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move Backend 3 copy-oldification and promotion/remembered-root rewriting on
top of the now-strict forwarding and object-node substrates, then move Backend
4 relocation copy/policy/remap.  Final closure still requires no production C
GC object, the one-shot five-GC semantic/fixed-point matrix, and long-running
RSS/fragmentation/pause/throughput deltas.
