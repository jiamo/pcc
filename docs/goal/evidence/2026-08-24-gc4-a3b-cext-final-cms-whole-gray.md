# GC4 A3b C-extension final/CMS whole-gray evidence — 2026-08-24

## Claim

The final tracing cut in C and strict pcc-Python, and the C runtime CMS RESCAN
ticket, no longer execute C-extension `tp_traverse` while holding the GC graph
lock.  A locked drain slice processes the full built-in gray closure but claims
and retains at most one gray C-extension object with the existing exact
`(object, cycle_epoch, backend)` token.  Its stopped-world owner releases the
graph lock, executes the shared per-slot/final revalidated callback completion,
and repeats until no pending token remains.

The final-cut owner now rescans current roots under its stopped-world boundary,
releases the graph lock for the callback-capable drain, then reacquires and
revalidates the finisher token before publishing sweep candidates.  The pure
finish commit contains no callback-capable drain.  CMS RESCAN calls the same
stopped-world wrapper rather than returning after the first C-extension claim,
preserving the queue overflow ticket's whole-gray-set meaning.  The strict CMS
worker has no raw pointer-ticket/RESCAN queue and remains on its ordinary trace
step; no fictitious mirror route was added.

The source contract was RED before implementation: the focused test failed
because no `pcc_gc_drain_all_gray_locked_slice` existed.  The true-pthread C
and strict probes drive a real final cut: initial seed and ordinary trace run
disarmed; a still-pending tail keeps the trace cursor open; after the ordinary
C-extension trace, the callback is armed; final current-root rescan regrays the
object; and a raw contender acquires the runtime's physical graph lock from
inside `tp_traverse` while the collector still owns the stopped world.

## Focused evidence

Final source, callback behavior, strict source ownership, LLVM/self object
closure and production archive ownership:

```text
gtimeout 180s zsh -o pipefail -c "gtimeout 150s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_gc_threading_substrate.py::test_final_and_cms_whole_gray_cext_callbacks_split_graph_lock \
  tests/python/test_gc_threading_substrate.py::test_cms_wb_queue_publication_is_outermost_and_lifecycle_epoch_guarded \
  tests/python/test_gc_threading_substrate.py::test_final_cut_cext_traverse_runs_outside_graph_lock \
  tests/python/test_freestanding_gc_common_mark_cycle.py \
  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py \
  2>&1 | tee build/gc-final-cms-cext-focused.log"

18 passed in 5.33s
```

Final finisher, single-finisher pthread, production link-map/substrate owner and
CMS overflow/termination neighbors:

```text
9 passed in 2.48s
```

Final-source task-card payload/retirement gate:

```text
gtimeout 270s zsh -o pipefail -c "gtimeout 240s env -u LC_ALL \
  uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_relocation_payload.py \
  tests/python/test_freestanding_gc_forwarding_retirement.py \
  2>&1 | tee build/gc4-relocation-mutator-quiescence.log"

24 passed in 13.92s
```

C syntax with `PCC_WITH_THREADS=0/1`, direct strict self/no-libpython closure
for both touched freestanding modules, Python syntax and `git diff --check`
pass.

Two 120-second cold-archive invocations ended without a pytest summary and had
no surviving pytest/bootstrap/pcc child; neither is evidence.  Their measured
successor packets completed in 143.46s and 146.57s before the final caches were
warm.  A first strict callback probe used high-level
`pcc_gc_object_is_known` as the contender operation and conflated graph-lock
availability with the strict runtime's other STW-aware entry behavior.  It was
rejected as a cross-runtime lock oracle.  The final probe uses the existing C
query on C and the existing strict physical graph lock/unlock on strict; no
diagnostic runtime symbol remains.

An adjacent strict explicit-collect probe was not evidence because compilation
stopped before GC execution at the independent self-backend diagnostic
`frame map 'unsafe.malloc.11.13' is not one direct global`.  The unchanged
runtime-high 4-thread counter probe also remained non-green and is not claimed.
Neither path was modified to make this slice pass.

## Frozen identities

```text
4199b98f6252dfeb68e1ac75937eb166b2facb6be591485a4ac044fbcc498cfe  pcc/py_runtime/src/py_gc_backend.c
a5d2c36bea90c1fc4b484b24cf0aefe11fdac4a6cd4e4ae6406b6799a7996367  pcc/py_runtime/py/freestanding_gc_common_mark_cycle.py
520a36ecd2e9af5f17c6c5af5bed30561f7dc0856508c7b0d64bb1eef60b579e  pcc/py_runtime/py/freestanding_gc_incremental_concurrent_scheduler.py
278a94c5de7c015e02b344cbac6b1671c412e67917801b794db4dfadac7de2c8  pcc/py_frontend/codegen/runtime_abi.py
c2a2cbfe7b5ab2dcfd32172a55284cb71f8d354c27cc661784658500b11b614d  tests/python/test_gc_threading_substrate.py
18b2bd5ebd3038f4d41431ac26e6a711bd569a16943cedf42d6a11a580a07a4c  tests/python/test_freestanding_gc_common_mark_cycle.py
73592b5a6d4de00b6f6477dc405926bb2db42f89d3ed24a32f3bd41cbbc6e9ac  tests/python/test_freestanding_gc_incremental_concurrent_scheduler.py
b55c7387308731e75024e9e28e46febe6a46b17e2e4f6fd48ca93341cedd8680  build/gc-final-cms-cext-focused.log
0e2dd56e50498e65f8abc21cb91ab6d5bf8effc20f958b27cf536b83e7187410  build/gc4-relocation-mutator-quiescence.log
```

## Open boundary

This closes final-cut and CMS RESCAN whole-gray C-extension graph-lock callback
holders.  It does not prove a real CMS worker RESCAN callback end to end; the C
final-cut probe dynamically proves the same stopped-world drain wrapper and the
source/queue tests prove CMS routing and termination neighbors.

Initial refcount-root subtraction still calls C-extension traversal during the
three-pass seed before `mark_active` publication.  It requires collector-owned
STW or an exact snapshot protocol; skipping subtraction would leak C-extension
cycles and is not acceptable.  Backend-4 remap/update C-extension traversal
still requires collector-owned STW plus object/source lifetime and registry
revision.  A3c, raw access, publication, leases, resurrection, stage
performance, fixed point and five-GC parity remain open.
