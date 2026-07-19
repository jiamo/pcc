# Investigation: GC3 bootstrap pcc-Python GC dispatch fast path

## Status
resolved for the focused backend #3 full bootstrap gate; five-GC matrix still
pending after the GC3-specific follow-up.

## Problem Description
Backend #3 is the generational minor/major collector. The current split
bootstrap gate runs one real `pcc1 -> pcc2 -> pcc3` chain per GC backend under
`tests/python/gc/`.

This investigation continues the "optimize from 4 to 0" pass for backend #3.
The target is real runtime/codegen cost in the pcc1 worker, not test skips,
cached pcc2/pcc3 reuse, weakened no-libpython checks, or skipped byte identity.

## Current Data
Focused GC3 baseline before the GC3 follow-up:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  -q -n0 -s

Bootstrap OK under PCC_GC_BACKEND=3: pcc2 and pcc3 are byte-identical.
1 passed in 74.87s
```

The profile from that run:

```text
stage2 total_ms 35889
stage3 total_ms 37847
total stage2+stage3 73736

compile_python_total total             71468
compile_python_multi_total total       71193
multi_frontend_codegen_parallel total  62573
multi_frontend_codegen_worker total    53441
multi_frontend_export_parallel total    9017
link_self_backend_ir_texts total        5508
link_self_emit_objects_host total       4915
libpython_scan total                    2275

multi_frontend_jobs 10
stage2 worker parse sum    30440
stage2 worker infer sum    13534
stage2 worker codegen sum 159642
stage3 worker parse sum    30956
stage3 worker infer sum    15223
stage3 worker codegen sum 182508
```

The dominant cost is still frontend/codegen workers, not linking.

## Sampling
The pre-patch delayed GC3 worker sample hit the codegen phase. The inclusive
stack was under:

```text
run_python_multi_codegen_worker
  L1CodeGen.generate
    _generate_impl
      _emit_user_function
        _emit_stmt / _emit_if / _emit_expr
```

Top sampled runtime/codegen helpers included:

```text
_platform_strlen                         489
pcc_gc_object_index_find                 231
user_py_class__strs_eq                   230
pcc_gc_object_index_insert               170
py_class_attrs_dict                      165
pcc_gc_ptr_index_insert_raw              158
pcc_gc_frame_index_remove                126
user_py_obj__ptr_can_have_header         122
pcc_gc_load_ptr                          106
user_py_class__class_lookup_in_mro        92
pcc_gc_note_relocation_read               86
user_py_gc_backend__counter_global        68
pcc_gc_note_frame_enter                   67
user_py_gc_backend__is_known_object       58
user_py_gc_backend__counter_inc           52
pcc_gc_backend                            45
pcc_gc_note_slot_write_barrier            40
pcc_py_gc_minor_graph_lock                40
py_gc_index_insert                        40
```

This supports a small dispatch fast path, but it also shows that the next
larger bottleneck is class/name lookup plus object/frame index work.

Fresh post-patch sampling attempts:

- Immediate 6-worker sample succeeded but captured parse/export, not codegen.
  The top frames were `Parser._parse_*`, so it is not valid codegen evidence.
- 2-worker delayed probes missed the worker lifetime after 12-35s delays.
- 1-worker does not reliably spawn a codegen worker under this harness shape,
  so that probe was terminated and the process group was cleaned up.

Conclusion: further codegen attribution should use a profiler harness that can
attach to all worker children or produce folded stacks from the stage process
tree. Short single-PID `sample(1)` probes are too race-prone for current GC3
worker lifetimes.

## Proposals

- No.1 remove/rework frame-index tracking [DENIED]
- No.2 cache backend dispatch inside GC3 hot paths [CONFIRMED]
- No.3 flamegraph / folded-stack profiler harness for remaining codegen split
  [pending]

## No.1 remove/rework frame-index tracking

### DENIED
`docs/investigations/gc-frame-index-entry-pool-perf.md` already rejected the
LIFO shadow-stack replacement: frame enter/leave is not slot-granularity LIFO in
the compiled runtime path, and the attempted shortcut caused GC3 bootstrap
timeouts. This investigation does not repeat that approach.

## No.2 cache backend dispatch inside GC3 hot paths

### Code Change
`pcc/py_runtime/py/py_gc_backend.py` now reuses `_init_config()`'s returned
backend value in GC3-relevant hot functions:

- `pcc_gc_try_minor_alloc`
- `_promote_young_if_known`
- `pcc_gc_note_slot_write_barrier`

This extends the earlier GC4 config fast path. The exported
`pcc_gc_backend()` still returns `_init_config()`, and `_init_config()` still
parses env once before returning the selected backend. Later calls reload
`pcc_gc_backend_selected`, so explicit `pcc_gc_set_backend()` remains visible.

### Correctness Boundary
The change does not alter:

- minor arena allocation size checks,
- `_set_pending_minor_block(...)` behavior,
- generational old/young promotion decisions,
- backend #3 remembered-owner insertion,
- backend #4 store-buffer insertion,
- relocation/read-barrier checks,
- no-libpython mode, stage freshness, or pcc2/pcc3 byte identity.

It only removes repeated exported `pcc_gc_backend()` calls after the same
function has already initialized config or can safely initialize config once.

### Test
Focused checks:

```text
env -u LC_ALL uv run black \
  pcc/py_runtime/py/py_gc_backend.py \
  tests/python/gc/test_gc_backend_config_fastpath.py
# passed

env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_gc_backend.py \
  tests/python/gc/test_gc_backend_config_fastpath.py
# passed

env -u LC_ALL uv run pytest \
  tests/python/gc/test_gc_backend_config_fastpath.py -q -n0
# 2 passed

env -u LC_ALL uv run pytest \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback \
  -q -n0
# 1 passed
```

Focused full GC3 bootstrap gate:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  -q -n0 -s

[stage2] PCC_GC_BACKEND=3 frontend_jobs=10 self_backend_jobs=12
[stage3] PCC_GC_BACKEND=3 frontend_jobs=10 self_backend_jobs=12
Bootstrap OK under PCC_GC_BACKEND=3: pcc2 and pcc3 are byte-identical.
1 passed in 98.45s
```

The 98.45s wall time includes shared stage1 rebuild. The GC3 stage profiles
after the patch are:

```text
stage2 total_ms 34270
stage3 total_ms 34281
total stage2+stage3 68551

stage2 compile_python_total            34270
stage2 multi_frontend_codegen_parallel 29815
stage2 worker command phase            25311
stage2 worker parse sum                31999
stage2 worker infer sum                13573
stage2 worker codegen sum             159782
stage2 export parallel                  4445

stage3 total_ms 34281
stage3 compile_python_total            34281
stage3 multi_frontend_codegen_parallel 29885
stage3 worker command phase            25195
stage3 worker parse sum                30477
stage3 worker infer sum                13273
stage3 worker codegen sum             156578
stage3 export parallel                  4631
```

### CONFIRMED
Against the focused pre-patch GC3 profile:

```text
before stage2+stage3 total_ms 73736
after stage2+stage3 total_ms  68551
delta                         -5185 ms (-7.0%)
```

The result is a real but limited improvement. It does not remove the remaining
larger class lookup/string compare/object-index cost visible in the codegen
sample.

## No.3 flamegraph / folded-stack profiler harness

### Pending
The next GC3 performance step should not be another dispatch micro-fastpath.
The remaining sample points at:

- `user_py_class__strs_eq` / `_platform_strlen`,
- `user_py_class__class_lookup_in_mro`,
- `pcc_gc_object_index_find` / `pcc_gc_object_index_insert`,
- `pcc_gc_frame_index_remove`,
- `pcc_gc_load_ptr` and relocation-read checks.

A useful next profiler must attach to every live codegen worker or to the
process tree, then emit folded stacks or a flamegraph. The single-PID
`sample(1)` probes are too easy to land in parse/export or miss the short-lived
worker.
