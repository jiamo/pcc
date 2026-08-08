# Freestanding pcc-Python GC object-root seeding ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
d4633c68...  pcc/py_runtime/py/freestanding_gc_object_root_seeding.py
67659af1...  pcc/py_runtime/py/py_gc_backend.py
e59a33c0...  pcc/py_frontend/codegen/runtime_abi.py
2c9edf2a...  pcc/py_runtime/Makefile
70cc8323...  tests/python/test_freestanding_gc_object_root_seeding.py
c6b32d29...  tests/python/test_gc_abstraction_surface.py
9a57d7df...  tests/python/test_gc_backend_generational.py
fec69ef6...  tests/python/test_gc_update_referents.py
```

## Claim boundary

`freestanding_gc_object_root_seeding.py` now uniquely owns object-list mark
preparation and current-root graying.  Mark preparation resets the atomic gray
counter, whitens active objects, preserves a fresh allocation as black during
automatic collection, and clears its FRESH_ALLOC bit; explicit collection
whitens it.  Current-root scanning grays pinned objects and delegates all frame,
continuation, scheduler and builtin-exception roots to the existing strict
registered-root scanner.

The managed collector preserves the original ordering:

```text
prepare object list -> subtract refcount/referent edges -> gray current roots
```

Refcount-external-root calculation remains managed because it depends on the
referent visitor; it belongs to that later slice rather than this raw loop
migration.

## Object and semantic proof

LLVM, self and fresh-pcc1 compilation define exactly two symbols.  Their exact
raw undefined closure is `pcc_gc_object_head`,
`pcc_gc_gray_count_store_release`, `pcc_gc_mark_root_gray_if_known` and
`pcc_gc_visit_registered_root_slots`.  None enters global
`RUNTIME_SIGNATURES`.

The production archive link map gives both symbols one owner in
`freestanding_gc_object_root_seeding.o`.  A direct backend-1 probe covers a
pinned object, an unpinned registered frame root, FRESH_ALLOC automatic and
explicit collection behavior, and proves exactly two gray roots.

## Focused and downstream results

```text
3 passed in 1.26s     # source ownership plus exact LLVM/self closure
1 passed in 57.80s    # production archive owner and direct color/root semantics
126 passed in 97.44s  # abstraction, generational and referent differential files
37 passed in 10.90s   # seeding/mapped/root-ops/backend-4 relocation combination
```

The first 126-test attempt used a 120-second cold diagnostic budget and ended
without a summary after 27 progress items; it is not evidence.  No pytest/pcc
children survived.  The measured retry used 180 seconds and completed in
97.44 seconds.

## Fresh pcc1 proof

The current-source self/no-libpython stage1 completed its publish and exec
smoke barrier:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=39425 \
  output=build/libc-gc-object-root-seeding-stage1/pcc1
```

That pcc1 compiled the real strict seeding module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off`, and `--python-library`.  Clang and
nm confirmed the same two definitions and four raw imports.

## Not proven

Refcount external-root/referent traversal, generational promotion/oldification,
relocation providers, weakref/finalizer/resurrection, full collector ownership,
long-run metrics and the final pcc1->pcc2->pcc3 five-GC matrix remain open.
