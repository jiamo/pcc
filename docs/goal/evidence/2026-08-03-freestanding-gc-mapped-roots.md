# Freestanding pcc-Python mapped-root visitor ownership

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `127ec488f026556c70aa20cea4e466257f93c597`, dirty
shared worktree. Relevant fingerprints:

```text
16dea641...  pcc/py_runtime/py/freestanding_gc_mapped_roots.py
95068c28...  pcc/py_runtime/py/py_gc_backend.py
32d80853...  pcc/py_frontend/codegen/runtime_abi.py
473728c2...  pcc/py_frontend/pipeline.py
02ad93ef...  pcc/py_runtime/Makefile
9cbd8408...  tests/python/test_freestanding_gc_mapped_roots.py
```

## Claim boundary

One strict freestanding pcc-Python object now uniquely owns the two public
continuation operations and their complete shared root-slot visitor:

```text
pcc_gc_trace_continuation_roots
pcc_gc_rewrite_continuation_roots
pcc_gc_visit_mapped_root_slot
pcc_gc_visit_mapped_root_slots
pcc_gc_visit_scheduler_root_slots
pcc_gc_visit_builtin_exception_cache_slots
pcc_gc_gray_mapped_roots
pcc_gc_rewrite_mapped_roots
```

Frame, continuation, scheduler and builtin-exception roots therefore consume
one mode-based slot walker for gray, promote and rewrite. The managed
collector remains the single provider of mark, promotion and relocation
resolution, and the existing pcc-Python substrate remains the single provider
of builtin-exception cache slots. No object-graph rule was copied.

The retained C implementation is the GC0..4 differential oracle, not a
production owner in the pcc-Python archive.

## Raw closure and semantic proof

LLVM and self emitters define exactly the eight symbols above. The exact raw
undefined closure is:

```text
pcc_gc_backend
pcc_gc_continuation_root_head
pcc_gc_mark_root_gray_if_known
pcc_gc_promote_cached_frame_slot
pcc_gc_resolve_root_slot_unlocked
pcc_gc_root_map_is_borrowed
pcc_gc_root_slot_count_from_map
pcc_gc_scheduler_root_head
pcc_py_gc_minor_graph_lock
pcc_py_gc_minor_graph_unlock
py_subs_exc_cache_slot
```

The production archive link-map gate proves every migrated symbol has exactly
one definition in `freestanding_gc_mapped_roots.o`. For GC0 through GC4 the
strict implementation matches the C oracle's owned/borrowed mapped-root count.
The backend-4 probe also proves a forwarded continuation root is rewritten to
the new address while preserving stable object identity. A threaded gate races
four register/unregister mutators against trace/rewrite and finishes with zero
registered roots under all five backends.

The ownership test was red before implementation (`4 failed, 1 passed`). The
final focused results are:

```text
1 passed in 54.74s   # production archive, GC0..4 and GC4 relocation
1 passed in 58.74s   # PCC_WITH_THREADS=1 contention, GC0..4
56 passed in 7.58s   # combined strict/downstream source and runtime gates
```

## Bootstrap and cache-boundary proof

An intermediate design put raw-only cross-object seams in the global
`RUNTIME_SIGNATURES` table. That changes the declarations and self-object cache
key of every unrelated module. Stage1 timed out once at 360 seconds and again
at 180 seconds; both watchdogs reaped all descendants. A current-source
emit-only profile completed in 13.2 seconds and wrote 270,918,593 bytes of IR,
localizing the delay to cold native object emission rather than frontend
correctness.

The final design keeps the finite exact signatures only in
`FREESTANDING_GC_CROSS_OBJECT_SIGNATURES`; a regression assertion requires the
raw-only symbols to remain absent from `RUNTIME_SIGNATURES`. The current-source
self/no-libpython stage1 then completed its publish and exec-smoke barrier:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=78461 \
  output=build/libc-gc-mapped-roots-raw-abi-stage1/pcc1
link_self_native_object_cache_hits=301
link_self_native_object_cache_misses=24
link_self_emit_objects_native=11.65s
```

That pcc1 compiled the real strict module with `--ir-scaffold=on`,
`--backend self`, `--python-libpython off`, and `--python-library` in 0.56s.
Clang and `nm` confirmed the same eight definitions and eleven raw imports.

## Not proven

Frame registration, the three collector providers, referent traversal,
weakref/finalizer/resurrection, full collector and relocation ownership,
long-run GC metrics, and the final pcc1->pcc2->pcc3 five-GC matrix remain open.
The slow matrix must run once only after the remaining symbol families migrate.
