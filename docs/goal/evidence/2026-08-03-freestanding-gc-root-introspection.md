# Freestanding pcc-Python GC root introspection ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
973e0d69...  pcc/py_runtime/py/freestanding_gc_root_introspection.py
2db5efd4...  pcc/py_runtime/py/py_gc_backend.py
fd62d567...  pcc/py_frontend/codegen/runtime_abi.py
7e25061b...  tests/python/test_freestanding_gc_root_introspection.py
```

## Claim boundary

One strict freestanding pcc-Python object now uniquely owns:

```text
pcc_gc_scheduler_root_count
pcc_gc_frame_root_slot_count
pcc_gc_continuation_root_slot_count
pcc_gc_coroutine_root_score
pcc_gc_slot_is_runtime_root
pcc_gc_root_slot_in_span            # internal shared helper
```

This is read-only introspection ownership, not root registration, tracing,
rewriting, or collection ownership. The retained C implementation remains the
oracle and is not linked into the pcc-Python production archive.

## Semantic correction and object proof

The C oracle locks the object graph while traversing scheduler, frame, and
continuation root lists. The managed pcc-Python count functions did not; only
the membership query was locked. All four traversals now use the same
`pcc_py_gc_minor_graph_lock/unlock` ABI as the root mutators.

LLVM and self emitters define exactly the six symbols above. Their exact raw
undefined closure is:

```text
pcc_gc_backend
pcc_gc_scheduler_root_head
pcc_gc_frame_head
pcc_gc_continuation_root_head
pcc_py_gc_minor_graph_lock
pcc_py_gc_minor_graph_unlock
```

The archive link-map test proves unique ownership by
`freestanding_gc_root_introspection.o`, never `py_gc_backend.o`.

## Focused results

```text
5 passed in 2.54s
42 passed in 10.78s
```

The first command covers exact LLVM/self closure, production link ownership,
deterministic scheduler/frame/continuation counts and slot membership versus
the C oracle under GC0..4, plus a `PCC_WITH_THREADS=1` four-mutator/one-observer
contention harness under GC0..4. The second covers adjacent strict-module,
tracking, telemetry, and coroutine-root regressions.

## Fresh pcc1 proof

The final current-source no-libpython/self stage1 completed its publish and
exec-smoke barrier on a cold self-object cache:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=189623 \
  output=build/libc-gc-root-introspection-stage1/pcc1
link_self_native_object_cache_hits=0
link_self_native_object_cache_misses=325
```

That pcc1 compiled the real strict module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off`, and `--python-library` in 0.37s.
Clang and `nm` confirmed the same exact six definitions and six raw imports.

## Not proven

Root registration/mutation, mapped-root trace/rewrite, referent traversal,
weakref/finalizer/resurrection, relocation, collector closure, long-run GC
metrics, and pcc1->pcc2->pcc3/five-GC acceptance remain open. The full slow
matrix must run once only after the remaining symbol families migrate.
