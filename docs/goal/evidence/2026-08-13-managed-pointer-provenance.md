# Managed-pointer provenance focused evidence (2026-08-13)

Claim level: source-current Darwin arm64 host compiler, C oracle runtime and
pcc-Python production runtime; LLVM and self object emitters; GC0 through GC4.
This evidence does not claim Linux x86_64 execution or the sequential
pcc1 -> pcc2 -> pcc3 fixed point.

## Closed focused boundary

- `pcc_gc_pointer_is_managed` is the single public provenance decision.
  It compares pointer values against immortal singletons, C-API type objects,
  an exact key-only managed set, the object index, and forwarding source/target
  indexes before any candidate header load.
- Backend-0 objects, graph leaves, stable runtime roots and malloc-owned public
  big integers use the key-only set; ordinary tracked objects transfer to the
  object index. Backend switching publishes exact keys before discarding the
  object index.
- allocation, C-API allocation/free, ordinary deallocation, forwarding-source
  retirement, delayed zpage teardown and stable object-root publication keep
  provenance registration and removal symmetric.
- previous low-address, alignment, 2^47/2^48 and Darwin address-band guesses
  were removed from semantic/runtime ports. A raw C string, function pointer,
  tagged integer and an unreadable `PROT_NONE` page are rejected without a
  header read.
- the key-only set has direct collision, growth, duplicate, middle-removal and
  post-removal lookup parity coverage in the C oracle, LLVM port object and
  self port object.
- all 75 Makefile-listed strict freestanding pcc-Python modules emitted
  no-libpython library IR. During that sweep a missing finite raw ABI entry for
  `py_dealloc_vthread_channel(c_ptr) -> c_void` was found and fixed without
  weakening the freestanding verifier; tracing and backend-0 collector exact
  `nm -u` closures then passed.

## Focused gates

- `tests/python/test_runtime_pointer_provenance.py` plus
  `tests/python/test_runtime_layout_contract.py`: **5 passed**.
- pcc-Python runtime provenance GC0..4 node: **1 passed in 330.89s** on a cold
  content-addressed archive build; the full focused rerun used the completed
  immutable cache.
- forwarding retirement C-vs-pcc-Python three-remap-epoch differential:
  **1 passed**.
- freestanding index-table IR/C-oracle LLVM/self nodes: **3 passed**, including
  the managed-set collision/growth/removal matrix.
- tracing-sweep raw closure LLVM/self and backend-0 raw closure LLVM/self:
  **8 focused checks passed** across their source/ordering/object nodes.
- generated port ABI check and the affected 21 semantic/GC port-module
  emit-only sweep were clean before the complete 75-module strict sweep.

## Remaining strong-proof boundary

- run the raw-pointer/function-pointer/managed-object differential on Linux
  x86_64 rather than inferring target parity from the absence of address
  ceilings;
- run the listed bootstrap baseline and the repository-wide strict sequential
  pcc1 -> pcc2 -> pcc3 fixed-point gates after the remaining source queue is
  stable, so later compiler/runtime edits do not invalidate the proof.
