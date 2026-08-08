# Freestanding backend-0 collector orchestration

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC` (partial slice; task remains `DONE_WEAK`)

Source identity: Git `6219a61f8f1ea84b13d9448ad66898d5ebf24a7c`, dirty
shared worktree. Relevant fingerprints:

```text
freestanding_gc_backend0_collector.py  3a080971c2368617ef62754e40eca0ddf99fb49251fd95fa677460cb6f4f2177
py_obj_gc.py                           b207eb15c88698b99cd682d27361082759507de37e22d99062c0470453adbfcf
runtime_abi.py                         63677a76d54bfe31426d4b4d0671983d7696346abc66e935324fca6b818a293f
py_runtime/Makefile                    bca2fddc15aa7b860e1b9789fd43f38e5a59e843bfae56c1782ec4b330b5ae5c
collector tests                        e4d409d42b4920ceed2a42ab5b1954c6119a06dd8cf0a694b21493c3a7376020
```

## Claim boundary

`freestanding_gc_backend0_collector.o` is now the unique production owner of
`py_gc_collect` and nine named raw phases covering mapped/scheduler root
visitation, reachability recomputation, post-finalizer recheck and unreachable
deallocation. The algorithm is an ownership move of the already-green
pcc-Python implementation: STW acquisition, table lock, candidate selection,
finalizer pass, reachability recheck, weakref invalidation, shared-slot clearing,
unlink/index removal, object-free notification and cleanup order are unchanged.

The collector consumes the existing strict backend-0 subtract/mark/clear ABI
and typed GC globals. It does not copy object geometry: the one slot graph
remains `pcc_gc_visit_object_slots`. Every cross-object call is admitted by an
explicit source-level name/signature pair in
`FREESTANDING_GC_CROSS_OBJECT_SIGNATURES`; LLVM and self objects have the same
exact 48-symbol undefined closure, including allocator boundaries, typed GC
globals and pcc-Python finalizer/deallocator ABIs.

Managed `py_obj_gc.o` now owns only `py_gc_get_objects`,
`py_gc_get_referents` and `py_gc_get_referrers`, because those APIs construct
Python lists. No C GC source was changed in this slice; `py_obj_gc.c` remains
the host oracle.

This does not claim that the tracing, incremental/concurrent, generational or
relocating collector state machines are freestanding, nor that production is
free of all GC C objects.

## Red and implementation evidence

The new focused gate first produced five failures: the strict source was
absent and archive symbol ownership named `py_obj_gc.o`. The first compile
attempt then correctly failed closed because strict modules require every
function to have a named C-ABI export. The nine raw phases were therefore
given explicit `pcc_gc_backend0_*` exports; the validator was not weakened.

The collector's cext deallocation binding also initially failed the exact
extern parser because its declaration was split over multiple lines. Keeping
the existing fail-closed parser, the binding was expressed in its canonical
single-statement form. Both emitters then passed.

## Focused proof

```text
5 passed in 1.66s
  strict source ownership, ordering, LLVM/self exact closure and archive owner

31 passed in 73.55s; the sole stale managed-owner assertion was corrected
5 passed in 2.39s
  backend-0 slot actions, tracking and threading substrate; clean tracking rerun

8 passed in 5.01s
  backend 0 resurrection, weakref/finalizer, reentrant collect and
  suspended-frame/container/set root contracts

31 passed in 0.51s
  shared slot, root and C-extension traversal/deallocation source contracts
```

The production runtime archive rebuilt from current source in 52.64 seconds.
`nm -A -g` reported exactly one `py_gc_collect` definition, in
`freestanding_gc_backend0_collector.o`, and none in `py_obj_gc.o`. The existing
archive-linked C-oracle differential still reports:

```text
before:2
collected:2
after:0
```

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-backend0-collector-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-backend0-collector-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=32131 \
  output=build/freestanding-gc-backend0-collector-stage1/pcc1
```

`file` reports an arm64 Mach-O executable and `otool -L` reports only
`/usr/lib/libSystem.B.dylib`, not libpython. That pcc1 then compiled the real
collector with `--python-libpython=off --python-library` in 0.6 seconds.
Its IR defines all ten strict exports and has no `call` or `invoke` of any
`py_cpy_*` symbol. The module-wide runtime ABI declaration table still contains
unused `py_cpy_*` declarations; those are not calls or fallback evidence.

## Remaining task boundary

Migrate the other collector state machines (mark/sweep,
incremental/concurrent, generational promotion/oldification and relocating
policy/remap) without duplicating the slot graph. Then complete the all-backend
weakref/finalizer/resurrection, suspended-frame/scheduler/C-extension-root,
relocation, synchronization and no-production-GC-C-object proofs. Run the
five-GC semantic/fixed-point matrix and long-running
RSS/fragmentation/pause/throughput measurements once after those migrations
stabilize.
