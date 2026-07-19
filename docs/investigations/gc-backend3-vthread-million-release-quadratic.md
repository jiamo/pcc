# Investigation: GC3 million-vthread release is quadratic

## Status
resolved

## Problem Description

The production one-million virtual-thread gate completes promptly under GC0,
GC1, and GC2, but GC3 does not reach the first 100,000 completion marker after
more than a minute at roughly 100% CPU. The reduced question is whether GC3's
release of copy-oldified virtual-thread objects performs a heap-size-dependent
ownership lookup instead of an O(1) allocation-origin decision.

## Repro

```bash
gtimeout 600s env -u LC_ALL PCC_VTHREAD_1M=1 uv run python \
  scripts/run_vthread_1m_gate.py --backend-timeout 300 \
  --output /tmp/pcc-vthread-1m.json
```

Observed on 2026-07-16 / macOS arm64:

- GC0, GC1, and GC2 each scheduled and completed `1,000,000/1,000,000`.
- GC3 scheduled `1,000,000/1,000,000`, entered completion, then remained below
  the first 100,000 completion marker for more than 80 seconds.
- The GC3 child remained runnable at 98.5% CPU with about 446 MB RSS.
- A one-second `sample` placed all 844 sampled stacks under
  `py_decref -> pcc_gc_free_object_memory`.
- The run was terminated deliberately rather than spending the full timeout;
  no benchmark or runner child remained.

Expected after the fix: the same command completes all five backends, emits a
source-bound JSON manifest, and every result reports one million completions
with scheduler roots and all three queues at zero.

## Test [CONFIRMED]

The failing production benchmark and native sample above confirm the regression.
Focused regression coverage is
`tests/benchmarks/vthread/test_vthread_real_runtime.py`; the manual 1M command is
the completion gate because the failure only becomes dominant at large live-set
size.

## Proposals

- No.1 Give GC3 malloc objects a new explicit allocation-origin bit [DENIED]
- No.2 Use the existing GC3 arena-ownership bit as the physical split [CONFIRMED]

## No.1 Give GC3 malloc objects a new explicit allocation-origin bit

### Code Change

Mark GC3 fallback `calloc` allocations and copy-oldification destinations with
`PY_FLAG_GC_MALLOC_ALLOC`, as GC4 already does for its malloc fallback. In
`pcc_gc_free_object_memory`, handle that ownership class with an O(1) object
index lookup/removal if the caller has not already performed freeing cleanup,
then call `free()` directly. Restrict the object-node/minor-block release route
to `PY_FLAG_GC_MINOR_ARENA` objects. Mirror the oldification flag and free
decision in the pcc-Python GC runtime.

This preserves the minor-arena no-`free()` law. It removes the normal old-copy
path from both the full object-list fallback and the linear
`pcc_gc_minor_block_containing_unlocked()` ownership scan.

### DENIED

Adding `PY_FLAG_GC_MALLOC_ALLOC` to GC3 fallback/oldified objects made the C
runtime million-object path fast, but the corresponding pcc-Python runtime
change caused its existing oldify/forwarding probes to promote in place instead
of producing a forwarded old copy. Splitting the bitmask expression did not
change that result. The proposal widened the allocation contract unnecessarily
and was removed.

## No.2 Use the existing GC3 arena-ownership bit as the physical split

### Code Change

Keep the existing object flags unchanged. In the C
`pcc_gc_free_object_memory()` helper, a live GC3 object with nonzero flags and
without `PY_FLAG_GC_MINOR_ARENA` is necessarily a normal heap object: minor
arena objects carry the bit, while fallback and copy-oldified destinations are
malloc/calloc owned. Perform one O(1) object-index cleanup if the earlier
freeing hook has not already done so, then call `free()` directly. Preserve the
historical index/minor-block route for arena objects and the conservative zero-
flag stale-shell rule.

The pcc-Python GC implementation was restored unchanged after substitution
showed that its separate oldify failures still reproduce without any mirror
edit. That independent current baseline is tracked in
`gc-backend3-pcc-py-oldify-current-regression.md`.

### CONFIRMED

The final C ownership and production-runtime group passed:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/benchmarks/vthread/test_vthread_real_runtime.py \
  tests/python/test_gc_backend_generational.py::test_generational_backend_c_runtime_uses_minor_bump_arena \
  tests/python/test_gc_backend_generational.py::test_generational_backend_c_runtime_frees_minor_object_by_index_when_flag_clobbered \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_oldifies_copy_for_remembered_child \
  tests/python/test_gc_backend_generational.py::test_generational_backend_release_of_forwarded_source_consumes_source_ref \
  tests/python/test_gc_backend4_production.py::test_backend4_skips_zpage_and_graph_for_leaf_objects

8 passed in 41.73s
```

The focused final-source GC3 million run completed in 28.47 seconds, including
27.79 seconds in the explicit live collect. Completion reached every 100,000
marker promptly and reported a 49 ns mean dequeue/complete release-side resume
cost, rather than failing to reach the first marker after 80+ seconds.

The final source-bound five-backend manifest is
`docs/goal/evidence/2026-07-16-vthread-1m-results.json`. Every backend completed
one million objects and ended with zero scheduler roots and zero ready, timer,
and IO entries.

## Report

No.2 landed. The regression was a redundant ownership search after
`pcc_gc_note_object_freeing()` had already removed a copy-oldified heap object
from the index: the deallocator then performed a failed index lookup, a full
object-list fallback, and a linear minor-block address scan for each object.
Using the already-authoritative `MINOR_ARENA` physical split restores O(1)
normal-heap release without changing flags or weakening arena safety. The 1M
gate also records, rather than hides, GC3's separate 27.5-second aggregate
collection-pause cost.
