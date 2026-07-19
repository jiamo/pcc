# Investigation: GC2 bootstrap pcc-Python object backend fast path

## Status
resolved for the focused backend #2 full bootstrap gate; five-GC matrix still
pending after the GC2 follow-up.

## Problem Description
Backend #2 is pcc's Go-style concurrent mark-sweep direction. The current
implementation is a correctness-green threaded prototype with a conservative
CMS worker/queue and buffered write barrier, not a full Go work-buffer/drain or
concurrent span-sweep port.

This investigation continues the "optimize from 4 to 0" bootstrap performance
pass. The target is measured GC2 cost in the real `pcc1 -> pcc2 -> pcc3`
chain. The gate must remain fresh stage2 + fresh stage3, strict no-libpython,
and byte-identical `pcc2`/`pcc3`.

## Current Data
Old GC2 profile artifacts from the earlier 2-worker small-budget run:

```text
stage2 total_ms 120839
stage3 total_ms  93510
multi_frontend_jobs 2
stage2 multi_frontend_codegen_parallel 115849
stage3 multi_frontend_codegen_parallel  88968
```

That profile is useful historical context, but it is not the current focused
single-backend harness shape. The current focused GC2 gate uses 10 frontend
workers and 12 self-backend jobs.

Current pre-patch focused GC2 baseline:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  -q -n0 -s

Bootstrap OK under PCC_GC_BACKEND=2: pcc2 and pcc3 are byte-identical.
1 passed in 71.46s
```

Profile before the pcc-Python object fast path:

```text
stage2 total_ms 33545
stage3 total_ms 31602
total stage2+stage3 65147

stage2 compile_python_total            33545
stage2 multi_frontend_codegen_parallel 27307
stage2 worker command phase            23288
stage2 worker parse sum                30228
stage2 worker infer sum                12715
stage2 worker codegen sum             142174

stage3 compile_python_total            31602
stage3 multi_frontend_codegen_parallel 27206
stage3 worker command phase            22904
stage3 worker parse sum                27472
stage3 worker infer sum                12543
stage3 worker codegen sum             139566
```

The dominant cost is the frontend worker phase. Link/object emission is only a
few seconds and the self-backend object cache is hot.

## Reference Material
Read before patching:

- `docs/refs_docs/gc-research/README.md`: backend #2 maps to
  `go-greentea/` and remains a prototype without full Go work-buffer/drain or
  concurrent span/object sweep.
- `docs/refs_docs/gc-research/go-greentea/mwbbuf.go`: Go's write barrier buffer
  uses a per-P fast buffer with a slow flush into GC work queues.
- `docs/refs_docs/gc-research/go-greentea/mgcmark.go`: concurrent mark workers,
  root jobs, and preemptible drain shape.
- `docs/investigations/gc-backend-selection-matrix.md`: backend #2 is not the
  default and should not be treated as production-equivalent to Go CMS.
- `docs/investigations/gc-backend2-buffered-write-barrier.md`: pcc already has
  a first buffered write-barrier slice; remaining work is a fuller work-buffer
  model and concurrent sweep decision.
- `docs/investigations/gc-backend2-cms-worker-instability.md`: worker/object
  graph synchronization is a correctness boundary; do not make worker tracing
  more aggressive without TSan/lifecycle proof.

## Sampling
The process-tree sampling probe starts a short GC2 stage2 compile, waits for
worker children, samples all live `pcc1 --pcc-python-multi-codegen-worker`
processes for six seconds, then terminates the stage2 process group.

Pre-patch multi-PID worker sample:

```text
live_workers_after_delay=[74949..74959]

top frames:
run_python_multi_codegen_worker                  47091
StmtDispatchLoweringMixin._emit_stmt_impl        35781
ExprDispatchLoweringMixin._emit_expr_impl        34773
Hoist lowering nested walk                       31082
Parser._parse_stmt                               30147

selected runtime frames:
pcc_gc_backend                                    3061
user_py_gc_backend__init_config                   1308
pcc_gc_note_alloc                                  292
user_py_gc_backend__note_cms_alloc                 193
pcc_gc_step                                          0
user_py_gc_backend__step_tracing                     0
pcc_gc_note_slot_write_barrier                    1190
pcc_gc_object_index_find                          4703
pcc_gc_object_index_insert                        4016
pcc_gc_ptr_index_insert_raw                       4221
pcc_gc_frame_index_remove                         4599
pcc_gc_note_frame_enter                          11816
pcc_gc_load_ptr                                   2077
user_py_class__strs_eq                            8059
_platform_strlen                                  5426
```

This denies the "CMS worker/step is the bootstrap hotspot" hypothesis:
`pcc_gc_step` and `_step_tracing` were not sampled, while the frontend worker
and generic pcc-Python runtime object helpers were.

## Proposals

- No.1 optimize CMS worker startup / queue / tracing step [DENIED]
- No.2 avoid exported backend lookup in hot pcc-Python object ops [CONFIRMED]
- No.3 object/frame index and class-name lookup flamegraph [pending]

## No.1 optimize CMS worker startup / queue / tracing step

### DENIED
The GC2 bootstrap sample did not show CMS worker/step cost:

```text
pcc_gc_step                         0
user_py_gc_backend__step_tracing    0
```

The focused profile also shows the stage is dominated by frontend workers, not
link or CMS worker lifecycle. Backend #2 worker/queue work remains important
for the research program, but it is not the first bootstrap-performance
optimization target for this slice.

## No.2 avoid exported backend lookup in hot pcc-Python object ops

### Code Change
`pcc/py_runtime/py/py_obj.py` now has `_gc_backend_fast()`:

```text
if pcc_gc_config_initialized == 0:
    return pcc_gc_backend()
return pcc_gc_backend_selected
```

The first call still goes through exported `pcc_gc_backend()` so environment
configuration is parsed exactly once. Once initialized, hot pcc-Python object
ops read `pcc_gc_backend_selected` directly. This remains visible to explicit
`pcc_gc_set_backend()` because that API updates the selected-backend global.

Updated hot paths:

- `pcc_gc_release`
- `pcc_gc_load_ptr`
- `pcc_gc_load_borrowed_ptr`
- `pcc_gc_resolve_owned_ptr`
- `pcc_gc_store_ptr`
- `pcc_gc_store_root`
- `pcc_gc_collect`
- `py_incref`
- `py_decref`

### Correctness Boundary
This change does not alter:

- backend selection or environment parsing,
- explicit backend switching,
- relocation read barriers,
- write-barrier calls,
- root-store locking,
- refcount ownership behavior,
- no-libpython mode,
- pcc2/pcc3 byte identity.

It only removes repeated exported function calls after GC config has already
been initialized.

### Test
Focused checks:

```text
env -u LC_ALL uv run black \
  pcc/py_runtime/py/py_obj.py \
  pcc/py_runtime/py/py_gc_backend.py \
  tests/python/gc/test_gc_backend_config_fastpath.py
# passed

env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_obj.py \
  pcc/py_runtime/py/py_gc_backend.py \
  tests/python/gc/test_gc_backend_config_fastpath.py
# passed

env -u LC_ALL uv run pytest \
  tests/python/gc/test_gc_backend_config_fastpath.py -q -n0
# 3 passed

env -u LC_ALL uv run pytest \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_obj_runtime_refcount_primitives_do_not_self_root \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback \
  -q -n0
# 2 passed
```

Focused full GC2 bootstrap gate:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  -q -n0 -s

[shared stage1] building one backend-agnostic pcc1
[stage2] PCC_GC_BACKEND=2 frontend_jobs=10 self_backend_jobs=12
[stage3] PCC_GC_BACKEND=2 frontend_jobs=10 self_backend_jobs=12
Bootstrap OK under PCC_GC_BACKEND=2: pcc2 and pcc3 are byte-identical.
1 passed in 92.33s
```

The 92.33s includes shared stage1 rebuild. The stage2/stage3 profiles after
the patch are:

```text
stage2 total_ms 31305
stage3 total_ms 31474
total stage2+stage3 62779

stage2 multi_frontend_codegen_parallel 27052
stage2 worker command phase            23116
stage2 worker parse sum                26507
stage2 worker infer sum                12350
stage2 worker codegen sum             143392

stage3 multi_frontend_codegen_parallel 27197
stage3 worker command phase            23245
stage3 worker parse sum                31193
stage3 worker infer sum                12760
stage3 worker codegen sum             138799
```

Post-patch multi-PID sample:

```text
pcc_gc_backend                          424
user_py_gc_backend__init_config         459
user_py_obj__gc_backend_fast            911
pcc_gc_step                               0
user_py_gc_backend__step_tracing          0
pcc_gc_object_index_find               4998
pcc_gc_object_index_insert             4254
pcc_gc_ptr_index_insert_raw            4461
pcc_gc_frame_index_remove              4273
pcc_gc_note_frame_enter               11579
pcc_gc_load_ptr                        2225
pcc_gc_store_ptr                        669
user_py_class__strs_eq                 9035
_platform_strlen                       6097
```

### CONFIRMED
The pcc-Python object-op backend fast path reduced backend-dispatch sampling
substantially:

```text
pcc_gc_backend                  3061 -> 424
user_py_gc_backend__init_config 1308 -> 459
```

The focused stage profiles moved:

```text
before stage2+stage3 total_ms 65147
after stage2+stage3 total_ms  62779
delta                         -2368 ms (-3.6%)
```

The performance gain is modest but real enough to keep because it is validated
by both profile movement and direct sampling. It also preserves the full GC2
fixed-point gate.

## No.3 object/frame index and class-name lookup flamegraph

### Pending
The remaining sampled cost is not CMS-specific. The next GC-bootstrap
performance work should use a persistent folded-stack/flamegraph harness over
all worker children and focus on:

- `pcc_gc_note_frame_enter` / `pcc_gc_frame_index_remove`,
- `pcc_gc_object_index_find` / `pcc_gc_object_index_insert`,
- `pcc_gc_ptr_index_insert_raw`,
- `user_py_class__strs_eq` and `_platform_strlen`,
- `class_lookup_in_mro`.

Do not revive the previously denied LIFO frame-index replacement from
`gc-frame-index-entry-pool-perf.md`; it broke GC3 because frame enter/leave is
not slot-granularity LIFO in the compiled path.
