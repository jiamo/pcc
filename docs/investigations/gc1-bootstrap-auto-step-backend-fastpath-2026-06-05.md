# Investigation: GC1 bootstrap auto-step backend fast path

## Status
resolved as a small focused backend #1 bootstrap-performance slice; broader
class/name lookup and object/frame-index hotspots remain pending.

## Problem Description
Backend #1 is pcc's Lua-style incremental tricolor collector. This
investigation continues the "optimize from 4 to 0" bootstrap performance pass,
after the GC4/GC3/GC2 dispatch fast paths.

The target is real `pcc1 -> pcc2 -> pcc3` cost under `PCC_GC_BACKEND=1`.
The gate must remain strict no-libpython, freshly build stage2 and stage3, and
verify byte-identical `pcc2`/`pcc3`. It must not skip GC work, reuse stale
`pcc2`/`pcc3`, or weaken the incremental collector's root/sweep behavior.

## Current Data
Focused GC1 baseline before the GC1-specific follow-up, after the common
`py_obj.py` backend fast path:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  -q -n0 -s

Bootstrap OK under PCC_GC_BACKEND=1: pcc2 and pcc3 are byte-identical.
1 passed in 65.43s
```

Profile before the GC1-specific change:

```text
stage2 total_ms 31624
stage3 total_ms 31010
total stage2+stage3 62634

stage2 worker command phase 23334
stage2 worker codegen sum 145202
stage3 worker command phase 22664
stage3 worker codegen sum 139633
```

The dominant cost is still the pcc-Python frontend/codegen worker phase.

## Reference Material
Read before patching:

- `docs/refs_docs/gc-research/README.md`: backend #1 maps to Lua incremental
  tricolor collection and is the first production-test-green tracing backend.
- `docs/refs_docs/gc-research/lua/lgc.c`: `incstep`, `luaC_step`, pacer debt,
  gray/grayagain queues, and atomic/sweep phase structure.
- `docs/refs_docs/gc-research/lua/lstate.h`: collector debt and pacer state
  shape.
- `docs/investigations/gc-backend1-incremental-tricolor-pacer.md`: current pcc
  pacer/debt model.
- `docs/investigations/gc-backend1-auto-step-sweep-debt.md`: allocation-time
  automatic sweep of existing candidates was denied because it can reclaim
  objects still live in Python locals.
- `docs/investigations/gc-backend1-object-registry-performance.md`: object
  registry overhead is known to matter, but previous shortcuts must preserve
  root precision.
- `docs/investigations/gc-frame-index-entry-pool-perf.md`: the earlier LIFO
  frame-index shortcut was denied after causing GC3 bootstrap timeout.

## Sampling
The GC1 process-tree sample started a short stage2 compile, sampled live
`pcc1 --pcc-python-multi-codegen-worker` children, then killed the stage2
process group.

Pre-patch selected worker samples:

```text
pcc_gc_backend                                  535
user_py_gc_backend__init_config                 535
user_py_obj__gc_backend_fast                    837
pcc_gc_note_alloc                               229
user_py_gc_backend__maybe_auto_step             193
pcc_gc_step                                       0
user_py_gc_backend__step_tracing                  0
user_py_gc_backend__debt_threshold              238
user_py_gc_backend__budget_from_debt              0
pcc_gc_note_slot_write_barrier                 1591
user_py_gc_backend__counter_inc                1560
pcc_gc_object_index_find                       4885
pcc_gc_frame_index_remove                      4386
pcc_gc_note_frame_enter                       11649
user_py_class__strs_eq                         9021
_platform_strlen                               6032
```

This denies the hypothesis that GC1 bootstrap time is currently dominated by
true incremental step work: `pcc_gc_step` and `_step_tracing` sampled zero.
The remaining sampled GC1-specific auto-step cost is only the dispatch/debt
check around `pcc_gc_note_alloc`.

Post-patch selected worker samples:

```text
pcc_gc_backend                                  429
user_py_gc_backend__init_config                 485
user_py_obj__gc_backend_fast                    904
pcc_gc_note_alloc                               250
user_py_gc_backend__maybe_auto_step             171
pcc_gc_step                                       0
user_py_gc_backend__step_tracing                  0
user_py_gc_backend__debt_threshold              209
user_py_gc_backend__budget_from_debt              0
pcc_gc_note_slot_write_barrier                 1389
user_py_gc_backend__counter_inc                1509
pcc_gc_object_index_find                       4800
pcc_gc_object_index_insert                     4351
pcc_gc_ptr_index_insert_raw                    4396
pcc_gc_frame_index_remove                      4393
pcc_gc_note_frame_enter                       11963
pcc_gc_load_ptr                                2316
pcc_gc_store_ptr                                579
py_incref                                     2464
py_decref                                     2458
user_py_class__strs_eq                         9474
user_py_class__class_lookup_in_mro             2196
_platform_strlen                               6390
```

The sample moves in the expected direction for backend dispatch but also shows
the same broad remaining bottlenecks: class/string lookup and object/frame
index work. Those are not GC1 pacer fixes.

## Proposals

- No.1 make allocation-time auto-step sweep existing candidates [DENIED]
- No.2 avoid exported backend lookup inside GC1 auto-step dispatch [CONFIRMED
  WEAK]
- No.3 process-tree folded-stack/flamegraph for remaining broad hotspots
  [pending]

## No.1 make allocation-time auto-step sweep existing candidates

### DENIED
This was already rejected in
`docs/investigations/gc-backend1-auto-step-sweep-debt.md`: allocation-time
automatic sweep can run while Python locals still hold live objects that are
not protected for that sweep boundary. Reopening that direction would require
new root-precision proof, not a bootstrap-performance profile.

The current GC1 sample also denies it as the immediate bottleneck:

```text
pcc_gc_step                         0
user_py_gc_backend__step_tracing    0
```

## No.2 avoid exported backend lookup inside GC1 auto-step dispatch

### Code Change
`pcc/py_runtime/py/py_gc_backend.py::_maybe_auto_step()` now reads
`pcc_gc_backend_selected` directly instead of calling exported
`pcc_gc_backend()`:

```text
if load_i32(global_addr("pcc_gc_backend_selected"), 0) != 1:
    return
```

The caller `pcc_gc_note_alloc()` already calls `_init_config()` first and only
enters `_maybe_auto_step()` for backend #1. The C runtime mirror checks the
selected-backend global in the same steady-state style.

### Correctness Boundary
This change does not alter:

- environment parsing or backend selection initialization,
- explicit backend switching via `pcc_gc_set_backend()`,
- allocation debt accounting,
- step budgets,
- mark/gray/grayagain behavior,
- sweep candidate handling,
- finalizer/resurrection/root behavior,
- no-libpython mode,
- stage freshness, or pcc2/pcc3 byte identity.

It only avoids an exported backend query in a path whose caller already
initialized and classified the backend.

### Test
Focused checks:

```text
env -u LC_ALL uv run black \
  pcc/py_runtime/py/py_gc_backend.py \
  pcc/py_runtime/py/py_obj.py \
  tests/python/gc/test_gc_backend_config_fastpath.py
# passed

env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_gc_backend.py \
  pcc/py_runtime/py/py_obj.py \
  tests/python/gc/test_gc_backend_config_fastpath.py
# passed

env -u LC_ALL uv run pytest \
  tests/python/gc/test_gc_backend_config_fastpath.py -q -n0
# 4 passed

env -u LC_ALL uv run pytest \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback \
  -q -n0
# 1 passed
```

Focused full GC1 bootstrap gate:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  -q -n0 -s

[shared stage1] building one backend-agnostic pcc1
[stage2] PCC_GC_BACKEND=1 frontend_jobs=10 self_backend_jobs=12
[stage3] PCC_GC_BACKEND=1 frontend_jobs=10 self_backend_jobs=12
Bootstrap OK under PCC_GC_BACKEND=1: pcc2 and pcc3 are byte-identical.
1 passed in 91.75s
```

The 91.75s wall time includes shared stage1 rebuild. Stage profiles after the
patch:

```text
stage2 total_ms 31301
stage3 total_ms 30816
total stage2+stage3 62117
```

### CONFIRMED WEAK
Against the focused pre-patch GC1 profile:

```text
before stage2+stage3 total_ms 62634
after stage2+stage3 total_ms  62117
delta                          -517 ms (-0.8%)
```

This is a real but weak micro-optimization. The sample reduction is clearer
than the wall-profile reduction:

```text
pcc_gc_backend                     535 -> 429
user_py_gc_backend__init_config    535 -> 485
_maybe_auto_step                   193 -> 171
_debt_threshold                    238 -> 209
```

Keep the patch because it is simple, local, and semantics-preserving, but do
not describe it as the main GC1 bootstrap bottleneck fix.

## No.3 process-tree folded-stack/flamegraph for remaining broad hotspots

### Pending
The remaining sampled cost is spread across:

```text
user_py_class__strs_eq
user_py_class__class_lookup_in_mro
_platform_strlen
pcc_gc_object_index_find/insert
pcc_gc_ptr_index_insert_raw
pcc_gc_frame_index_remove
pcc_gc_note_frame_enter
pcc_gc_load_ptr / py_incref / py_decref
```

Short `sample(1)` probes are enough to reject GC1 pacer/step changes, but they
are not enough to safely choose a larger object-index or class-lookup rewrite.
The next non-micro optimization should use a process-tree folded-stack or
flamegraph harness that captures all live frontend codegen workers across a
long enough window.

The user explicitly allowed flamegraph after ordinary profiling is
insufficient. Use it as measurement evidence only; it is not a substitute for
the focused no-libpython and `pcc2`/`pcc3` correctness gates.

## Conclusion
The GC1-specific safe change is only the `_maybe_auto_step()` backend-selection
fast path. It is verified by focused algorithm guards, no-libpython bootstrap
shim, and a fresh full backend #1 bootstrap gate with byte-identical
`pcc2`/`pcc3`.

The larger remaining performance work is not collector step semantics. It is
shared runtime/class lookup and object/frame indexing in the compiled
pcc-Python frontend workers; continue with backend #0 and/or a process-tree
flamegraph before attempting those broader changes.
