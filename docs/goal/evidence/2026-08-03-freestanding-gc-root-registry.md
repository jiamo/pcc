# Freestanding pcc-Python GC root registry ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
b75d02ca...  pcc/py_runtime/py/freestanding_gc_root_registry.py
eab16e55...  pcc/py_runtime/py/py_gc_backend.py
d4981ed9...  pcc/py_runtime/Makefile
25ac8b3c...  tests/python/test_freestanding_gc_root_registry.py
fb107d5d...  tests/python/test_gc_coroutine_scheduler_roots_production.py
fd62d567...  pcc/py_frontend/codegen/runtime_abi.py
```

## Claim boundary

One strict freestanding pcc-Python object now uniquely owns these public
registry mutation symbols:

```text
pcc_gc_scheduler_root_register_handle
pcc_gc_scheduler_root_register
pcc_gc_scheduler_root_unregister_handle
pcc_gc_scheduler_root_unregister
pcc_gc_register_continuation_root
pcc_gc_unregister_continuation_root
```

The same object owns five internal helpers for cycle-request publication,
scheduler list link/unlink, and canonical root-map count/borrowed decoding.
The retained C implementation is the differential oracle, not a production
owner in the pcc-Python archive.

This slice does not claim mapped-root trace/rewrite ownership. Those functions
remain with the collector until the shared mapped-root visitor and relocation
resolver can move together without creating a second object-graph contract.

## Semantic and object proof

Registry list mutations use the shared GC graph lock. Collection-cycle
publication now uses a release atomic store, matching the C oracle's
`__ATOMIC_RELEASE` operation instead of the former plain pcc-Python store.
Both emitted LLVM paths contain `store atomic i32 ... release, align 4`.

LLVM and self emitters define exactly the six public plus five internal
symbols. Their exact raw undefined closure is:

```text
free
malloc
memset
pcc_gc_backend
pcc_gc_continuation_root_head
pcc_gc_cycle_requested
pcc_gc_scheduler_root_head
pcc_py_gc_minor_graph_lock
pcc_py_gc_minor_graph_unlock
```

The production archive link-map test proves every migrated symbol has exactly
one definition in `freestanding_gc_root_registry.o`, never
`py_gc_backend.o`.

## Focused results

```text
5 passed in 2.68s
16 passed in 3.65s
3 passed in 0.37s
32 passed in 5.78s
```

These gates cover exact LLVM/self closure, archive ownership, deterministic
GC0..4 C-oracle parity for duplicate scheduler slots plus owned/borrowed and
invalid continuation maps, `PCC_WITH_THREADS=1` four-mutator/one-observer
contention, the existing coroutine/scheduler production suite, GC3 moving-root
rewrites, root introspection, and the strict-module contract.

## Fresh pcc1 proof

The current-source self/no-libpython stage1 completed its publish and exec
smoke barrier:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=43937 \
  output=build/libc-gc-root-registry-stage1/pcc1
link_self_native_object_cache_hits=325
link_self_native_object_cache_misses=0
```

That pcc1 compiled the real strict module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off`, and `--python-library` in 0.90s.
Clang and `nm` confirmed the same eleven definitions and nine raw imports.

## Not proven

Mapped-root trace/rewrite, frame registration, referent traversal,
weakref/finalizer/resurrection, relocation and collector ownership, long-run
GC metrics, and the final pcc1->pcc2->pcc3 five-GC matrix remain open.
