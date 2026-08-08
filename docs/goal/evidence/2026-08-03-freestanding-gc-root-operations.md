# Freestanding pcc-Python GC root-operation ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
660e559b...  pcc/py_runtime/py/freestanding_gc_root_operations.py
5c2fa14e...  pcc/py_runtime/py/py_gc_backend.py
de7d9387...  pcc/py_frontend/codegen/assignment_statement_lowering.py
b0e1997b...  pcc/py_frontend/codegen/unsafe_lowering.py
a959ab64...  pcc/py_frontend/codegen/runtime_abi.py
462e2328...  tests/python/test_freestanding_gc_root_operations.py
69069188...  tests/python/test_py_cross_module_class_inference.py
```

## Claim boundary

`freestanding_gc_root_operations.py` is now the sole production owner of the
four gray-counter atomic operations, the no-lock known-object predicate, root
gray marking and root-slot forwarding resolution.  The managed collector calls
those raw ABIs and no longer owns duplicate implementations.  Gray count uses
acquire/release/acq_rel operations, including a CAS decrement that cannot fall
below zero.

Promotion is deliberately outside this slice: generational oldification,
referent recursion and ownership transfer remain in the managed collector.

## Object and semantic proof

LLVM and self compilation define exactly seven symbols.  Their exact undefined
closure is six symbols: `pcc_gc_forwarding_index_find`,
`pcc_gc_object_index_find`, `py_incref`, `py_decref`,
`pcc_gc_backend_selected` and `pcc_gc_gray_count`.  The two raw index queries
remain outside global `RUNTIME_SIGNATURES`; only the strict cross-object ABI
table knows them.

The production archive link map gives all seven symbols one owner in
`freestanding_gc_root_operations.o`.  A direct backend-4 probe proves a known
object is grayed exactly once, relocation rewrites its root to the forwarding
target, and repeated decrements clamp the counter at zero.  Four pthreads each
perform 4096 increment/decrement pairs and finish at zero.

## Stacked frontend regressions closed

Fresh pcc1 compilation exposed two independent frontend defects before the
new strict object could compile:

1. Typed `self.field += value` in a mixin used dynamic getattr rather than the
   concrete receiver field layout.  A cross-module executable regression now
   proves fixed-slot load/store, output `1\n2\n`, and no `py_cpy_*` dispatch.
2. CAS lowering called `extract_value(pair, 0)`.  The self-host scaffold's
   opaque-pointer ABI represented the literal zero as NULL, so the compiled
   builder tried to iterate it and raised `TypeError`.  CAS now supplies the
   explicit index sequence `[0]`, accepted by both llvmlite and the in-repo
   builder.

The failures and LLDB evidence are recorded separately in
`docs/investigations/freestanding-gc-root-operations-ownership.md`.

## Focused and downstream results

```text
5 passed in 57.67s    # exact LLVM/self closure, archive semantics, pthread CAS
10 passed in 117.96s  # mapped-root and frame-registry production regressions
154 passed in 18.13s  # generational/abstraction/referent/relocating downstream
15 passed in 61.55s   # atomic LLVM/self semantics, shapes and fail-closed cases
7 passed in 4.92s     # complete cross-module class inference file
6 passed in 1.28s     # focused cmpxchg/extract_value scaffold matrix
```

An attempted full 165-case scaffold parameter matrix exceeded its 120-second
diagnostic budget without a final summary; it is not counted as evidence.  The
watchdog left no pytest/pcc child processes.

## Fresh pcc1 proof

The current-source self/no-libpython stage1 completed its publish and exec
smoke barrier:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=38891 \
  output=build/libc-gc-root-operations-stage1-v3/pcc1
```

That pcc1 compiled the real strict root-operation module with
`--ir-scaffold=on`, `--backend self`, `--python-libpython off`, and
`--python-library` in 0.15s.  Clang and nm confirmed the same seven definitions
and six raw imports.

## Not proven

Object-list root seeding, generational promotion, referent traversal,
weakref/finalizer/resurrection, full collector ownership, long-run GC metrics
and the final pcc1->pcc2->pcc3 five-GC matrix remain open.
