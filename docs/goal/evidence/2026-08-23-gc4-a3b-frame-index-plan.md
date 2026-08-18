# GC4 A3b frame-index capacity plan

Date: 2026-08-23

## Claim

C and strict pcc-Python frame entry no longer allocate, rehash, or free the
frame pointer index while holding the GC graph lock.  Both runtimes prepare a
candidate table outside the lock, revalidate and atomically commit capacity
under the lock, and retire unused or replaced table storage only after unlock.
Duplicate-frame replacement and the allocation-failure path retain their
previous semantics; frame leave uses the same allocation-free replacement
primitive for duplicate restoration and stale-index repair.

This closes only the frame-index holder identified by the A3b audit.  It does
not make every graph-lock holder safe and does not authorize A3c no-park
integration.

## RED chronology

The new source/ABI contract was genuinely RED because the capacity-plan API
did not exist and frame entry still called the allocation-capable index
replacement while locked.  After the first implementation, strict exact raw
closure then caught two real integration defects in sequence:

- the module-scope extern scanner could not see a multiline
  `pcc_gc_frame_index_plan_commit` declaration;
- obsolete frame-index insert/replace imports remained in the strict source
  and expected-import contract even though the frame registry no longer used
  them.

The extern was made scanner-visible and the genuinely unused imports were
removed.  Neither failure was suppressed by widening the strict closure.

## Implementation boundary

The shared C index-table owner now exposes generic capacity planning internally
and three frame-specific cross-object operations:

- `pcc_gc_frame_index_plan_capacity`
- `pcc_gc_frame_index_plan_commit`
- `pcc_gc_frame_index_replace_preallocated`

The strict index-table mirror, runtime ABI registry and internal C header carry
the same signatures.  Frame entry prepares its frame node before the graph
lock, allocates any required 24-byte-slot table after unlocking, retries after
capacity races, commits only a still-sufficient plan, links/replaces without
allocation, then frees unused or replaced storage after unlock.  Allocation
failure releases the prepared frame node outside the lock.  Frame leave is
allocation-free at the index boundary.

## Frozen source identity

```text
edb6e9b9e79301f9de9e60a32a28d399a92dcdeb7196f6ef6c8e050cbbbf5310  pcc/py_runtime/src/py_gc_index_table.c
ab9665b944c029b43029eadd9c6305c16382361ddf41c4f6d9fd994994e1f96d  pcc/py_runtime/src/py_gc_backend.c
dcdbb0ac74a14a778cf8b4a0b9094c530a47323fc1279f2e63bb7d390b69681c  pcc/py_runtime/src/py_internal.h
f84edc066839f22b9fad846cb59456cca1fe9f892d3846e74683ce0ffad570db  pcc/py_runtime/py/freestanding_gc_index_table.py
9a08e564e3309ac80220a0a76cb6a295fbd7d7c30ee192ba75f2061975e10fe9  pcc/py_runtime/py/freestanding_gc_frame_registry.py
198788b13b5b80a74b254a3619c84c75b503384bdec425a588b10a133d960160  pcc/py_frontend/codegen/runtime_abi.py
def91c488e663f80380d123ca1bceaf5080836f2de6ee7979ae7e1d0bce4c361  tests/python/test_freestanding_gc_index_table.py
f575e6c027eac65a2e7720beb0502e4a6d4a60674238c267d617105ce5977985  tests/python/test_freestanding_gc_frame_registry.py
5d65c0cc9cbbec305479c93673aa9456d32330bd5902c6375a0e2318096ea11a  tests/python/test_gc_backend_generational.py
ce213879935c6e0d25ebf09c88674fcc8c566e52950323af0731da4d56944e99  pcc/py_runtime/libpy_runtime_pcc_py.a.provenance.json
```

## Focused gates

The final combined packet exercised C/strict index-table differential oracles,
strict LLVM/self raw closure and archive ownership, GC0 through GC4 frame
behavior, duplicate restoration, allocation failure and true pthread entry:

```text
11 passed in 263.74s
```

Log: `build/gc4-a3b-frame-index-plan.log`, SHA-256
`c300274a04544512e36384f821f04e7d9b94cdce6f3a24c94c6b3c87152ca5df`.

The final source-owner/ABI assertion was rerun separately and passed.  Python
byte compilation, C syntax with `PCC_WITH_THREADS=0` and `=1`, and
`git diff --check` are green.

## Open boundary

A3c remains blocked.  The next finite holder slice is ZPage metadata/backing
span allocation in C and strict pcc-Python.  It must move allocation out of
graph-lock ownership without publishing a partial page, losing active/free/
reusable-page races, or weakening allocator failure.  Relocation-reset list
retirement, GC3 promotion/remembered-owner safepoints and decrefs, extension
and caller root callbacks, tripwire/log paths and the remaining bounded-scan
audit remain open after that.  Raw container transactions, collector-owned
Backend 4 STW, raw leases, page/source lifetime and ABA, broad parity,
performance and fixed point are not claimed.
