# Freestanding GC forwarding/identity evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns the pointer-only substrate
shared by Backend 3 copy-oldification and Backend 4 colored relocation:
forwarding lookup/list bookkeeping, forwarding installation, stable object
identity, relocation-read resolution, safe slot-candidate classification, and
the small forwarding/identity counters.

Backend 4 zpage selection, relocation copying, remap, and page retirement stay
in their existing policy owner.  Backend 3 promotion/oldification is not yet a
complete strict module.  Therefore `LIBC-P2-FREESTANDING-GC` remains
`DONE_WEAK`.

## Ownership and preserved safety contracts

`freestanding_gc_forwarding_identity.py` exports exactly 28 raw ABI symbols.
The managed `py_gc_backend.py` consumes the shared head/find/unlink,
identity, clear, and unlocked-install operations through explicit extern
signatures and no longer defines the moved state-machine helpers or public
symbols.  The production archive reports every moved symbol exactly once from
`freestanding_gc_forwarding_identity.o`.

The migration preserves these contracts:

- unknown pointers are looked up by pointer value in the forwarding index
  before any object-header read;
- a header is read only after the object index proves the address known-live;
- public install and relocation-read paths retain the graph-lock protocol;
- pinned objects reject forwarding and increment the rejection counter;
- old and new copies share one stable identity;
- replacing a forwarding target retains the new target and releases the old;
- Backend 4 zpage pending-forwarding accounting still records the source page;
- Backend 3 may call the unlocked installer only while its promotion step
  already holds the graph lock.

## Focused gates

```text
tests/python/test_freestanding_gc_forwarding_identity.py
  5 passed in 60.03s

selected GC3 oldification/source-release and GC4 forwarding/identity/runtime cases
  14 passed in 126.56s

updated relocation-read source-route assertion
  1 passed in 0.20s
```

The TDD observations were:

```text
strict source absent
  1 failed in 0.10s (FileNotFoundError)

first strict compile
  rejected every unexported helper; 2 failed, 2 passed, 1 setup error

first helper export naming
  rejected function/global name collision for pcc_gc_forwarding_head;
  2 failed, 2 passed, 1 deselected in 0.63s

distinct raw helper/list-head ABI
  4 passed, 1 deselected in 1.84s
```

The existing semantic subset initially had one additional failure because its
source-shape assertion still read the migrated function from
`py_gc_backend.py`; all 14 runtime cases were green.  Routing the unchanged
fast-path assertion to the strict owner closed that test without weakening the
ordering check.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-forwarding-identity-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-forwarding-identity-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=39062 \
  output=build/freestanding-gc-forwarding-identity-stage1/pcc1
```

The profile records 37.801 seconds.  `file` reports arm64 Mach-O and
`otool -L` reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That
pcc1 compiled the real strict module with `--ir-scaffold=on --backend self
--python-libpython off --python-library`; clang accepted the emitted IR, all
28 exports are definitions, and no `call` or `invoke` targets `py_cpy_*`.

## Scoped hashes

```text
a77eef3158253ec00d90929d80e062a263697f69b2260ccf0f45f68798a968ca  pcc/py_runtime/py/freestanding_gc_forwarding_identity.py
a5c207b7a09d7b955b3701a0d2a7c80debea9b98fa2ed513f9dbba6b40c8befb  pcc/py_runtime/py/py_gc_backend.py
0928c78b7e57058d7f802466f4e158efa480abae30a7eb7e72dfa5bf399f6b74  pcc/py_frontend/codegen/runtime_abi.py
633bec926fed94fddba092d309cca7555cdc4594a869e2a04bb80a0b1bfc88b7  pcc/py_runtime/Makefile
0f55aed7787828c71c1c4fb81aa674ad8c6dad0cdbe2b0f6eeba4544fcdbf6b6  tests/python/test_freestanding_gc_forwarding_identity.py
bffcb97ea9750d2c1388005949786a4b8ed665b656c060bdf59f3841d78ae101  tests/python/test_gc_backend_generational.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move Backend 3 promotion/oldification on top of this strict substrate, then
move Backend 4 relocation copy/policy/remap.  After every production GC symbol
has a strict pcc-Python owner, prove no production GC C object is linked, run
the full five-GC semantic/fixed-point matrix once, and record long-running
RSS/fragmentation/pause/throughput deltas.
