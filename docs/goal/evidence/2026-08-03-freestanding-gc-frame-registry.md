# Freestanding pcc-Python GC frame-registry ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
14306c75...  pcc/py_runtime/py/freestanding_gc_frame_registry.py
c2bbeab8...  pcc/py_runtime/py/py_gc_backend.py
3486bf70...  pcc/py_frontend/codegen/runtime_abi.py
ee315c9f...  pcc/py_runtime/Makefile
527eef2e...  tests/python/test_freestanding_gc_frame_registry.py
21f1e017...  tests/python/test_gc_backend_generational.py
```

## Claim boundary

One strict freestanding pcc-Python object now uniquely owns the four public
frame-registry mutation entrypoints:

```text
pcc_gc_note_frame_enter
pcc_gc_note_frame_enter_lifo
pcc_gc_note_frame_leave
pcc_gc_note_frame_leave_lifo
```

The same object owns ten internal helpers for the frame-root fast gate, node
creation/unlink, and the size-bucketed node pool. Duplicate slot keys retain a
stack of nodes through `dup_next`; ordinary leave restores the previous index
entry; LIFO leave may unlink a matching non-head node; and nodes with at most
16 roots use the existing 1024-entry-per-bucket pool contract. Frame scanning,
marking, promotion and relocation remain outside this slice.

The retained C implementation is the GC0..4 differential oracle, not a
production owner in the pcc-Python archive.

## Raw closure and semantic proof

LLVM, self and fresh-pcc1 compilation define exactly the four public and ten
internal symbols. Their exact raw undefined closure has 19 symbols: allocator
`free`/`malloc`/`memset`; backend/config/frame/pool globals; graph lock/unlock;
the strict cycle-publication, root-map and frame-index providers; and
`pcc_gc_backend`.

Raw-only cross-object declarations remain solely in
`FREESTANDING_GC_CROSS_OBJECT_SIGNATURES` and are asserted absent from global
`RUNTIME_SIGNATURES`, so this slice does not invalidate unrelated self-object
cache keys.

The production archive link-map gate proves all 14 migrated symbols have one
definition in `freestanding_gc_frame_registry.o`. For GC0 through GC4 the
strict implementation matches the C oracle for duplicate restoration,
non-head LIFO removal, invalid root maps and node-pool reuse. A threaded gate
races four enter/leave mutators against root-count introspection under every
backend and finishes with zero registered frame roots.

## Focused and downstream results

```text
3 passed in 1.58s    # source owner plus exact LLVM/self object closure
1 passed in 0.85s    # production archive ownership and GC0..4 C differential
1 passed in 63.82s   # PCC_WITH_THREADS=1 mutation/observation, GC0..4
169 passed in 29.21s # frame, mapped/root, collector, strict/scaffold downstream
```

The downstream run exposed one stale source-ownership assertion left by the
earlier strict index/tracking migration. It was corrected to require the
strict index object to export find/insert/remove and the managed collector plus
strict tracking object to consume the appropriate operations; the repeated
169-test run is green.

## Fresh pcc1 proof

The current-source self/no-libpython stage1 completed its publish and exec
smoke barrier:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=32314 \
  output=build/libc-gc-frame-registry-stage1/pcc1
link_self_native_object_cache_hits=321
link_self_native_object_cache_misses=4
link_self_emit_objects_native=3.522s
```

That pcc1 compiled the real strict frame module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off`, and `--python-library` in 1.14s.
Clang and `nm` confirmed the same 14 definitions and 19 raw imports.

## Not proven

Collector frame scanning, the mark/promotion/relocation providers, referent
traversal, weakref/finalizer/resurrection, full collector ownership, long-run
GC metrics, and the final pcc1->pcc2->pcc3 five-GC matrix remain open. The slow
matrix must run once only after the remaining symbol families migrate.
