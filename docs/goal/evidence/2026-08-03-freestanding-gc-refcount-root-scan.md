# Freestanding pcc-Python GC refcount external-root scan

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
8084c6e4...  pcc/py_runtime/py/freestanding_gc_refcount_roots.py
c7f8db24...  pcc/py_runtime/py/py_gc_backend.py
dbececfd...  pcc/py_frontend/codegen/runtime_abi.py
03ad3716...  pcc/py_runtime/Makefile
59b938f4...  tests/python/test_freestanding_gc_refcount_roots.py
```

## Claim boundary

`freestanding_gc_refcount_roots.py` now uniquely owns the three-pass tracing
external-root scan and the shared raw object-node activity predicate.  It
snapshots active object refcounts into `gc_refs`, invokes one managed referent
subtraction provider, then grays objects with positive residual references.

`pcc_gc_subtract_referent_refs` remains in `py_gc_backend.o`.  That is an
explicit semantic provider boundary, not a copied list/dict/object traversal;
it will move with the full slot/referent visitor.

## Object and semantic proof

LLVM, self and fresh-pcc1 compilation define exactly
`pcc_gc_object_node_is_active` and `pcc_gc_gray_refcount_external_roots`.
Their raw undefined closure is exactly `pcc_gc_object_head`,
`pcc_gc_mark_root_gray_if_known` and `pcc_gc_subtract_referent_refs`.
The production archive link map gives the two kernel symbols one owner in
`freestanding_gc_refcount_roots.o` and the provider one owner in
`py_gc_backend.o`.

The direct backend-1 probe creates a list parent and list child.  When the child
is held only by the parent, subtraction yields parent gray, child white and one
gray root.  After one separate retain, both are gray and the count is two.
The first diagnostic mistakenly selected backend 0, whose allocation path
intentionally does not populate the tracing object list; its all-zero result
was rejected rather than weakening the assertion.

## Focused and downstream results

```text
3 passed in 1.05s      # split ownership plus exact LLVM/self closure
1 passed in 0.46s      # archive owners and internal-vs-external child semantics
149 passed in 134.57s  # abstraction/generational/referent/relocation downstream
```

## Fresh pcc1 proof

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=31312 \
  output=build/libc-gc-refcount-roots-stage1/pcc1
```

That pcc1 compiled the real strict module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off`, and `--python-library`.  Clang and
nm confirmed the same two definitions and three raw imports.

## Not proven

The managed referent subtraction provider and complete slot visitor,
generational promotion/oldification, relocation providers,
weakref/finalizer/resurrection, full collector ownership, long-run metrics and
the final pcc1->pcc2->pcc3 five-GC matrix remain open.
