# Investigation: GC4 bootstrap pcc-Python GC config fast path

## Status
resolved for the focused backend #4 full bootstrap gate; five-GC matrix still
above the 200s target.

## Problem Description
The five-GC full bootstrap split now runs one real `pcc1 -> pcc2 -> pcc3` chain
per GC backend under `tests/python/gc/`. Current artifacts show backend #4 is
still the longest wall-clock backend when all five files run in parallel with a
small per-GC worker budget.

This investigation starts the "optimize from 4 to 0" pass for backend #4. The
goal is not to skip any stage, cache pcc2/pcc3, or weaken no-libpython checks;
the gate remains fresh stage2 + fresh stage3 + no libpython + normalized
`pcc2 == pcc3`.

## Current Data
Baseline from current profile artifacts before this patch:

```text
build/bootstrap-pytest-self-gc4/profile/stage2.json
stage2 wall_ms                  174561
stage2 compile_wall_ms          173702
compile_python_total            173429
multi_frontend_codegen_parallel 168468
multi_frontend_codegen_worker   142337
multi_frontend_export_parallel   25976
multi_frontend_jobs                  2
worker parse sum                 54335
worker infer sum                 18516
worker codegen sum              199461
```

The same artifact set shows all backends dominated by
`compile_python_total`/`multi_frontend_codegen_parallel`, not by link or
publish barriers.

## Repro / Probe
Focused probe used a fresh out-dir and rebuilt `pcc1` from the current source,
then ran only backend #4 stage2 with the same small worker budget:

```bash
env -u LC_ALL \
  PCC_GC_BACKEND=4 \
  PCC_BOOTSTRAP_PY_FRONTEND_JOBS=2 \
  PCC_SELF_BACKEND_JOBS=2 \
  PCC_BOOTSTRAP_PROFILE_DIR=/Users/jiamo/my/pcc/build/bootstrap-probe-gc4-fastpath/profile \
  bash scripts/bootstrap.sh \
    --backend self \
    --out-dir build/bootstrap-probe-gc4-fastpath \
    --stage 2
```

Result after the fast-path patch:

```text
stage1 elapsed_ms 27743
stage2 elapsed_ms 151559
stage2 compile_wall_ms 150683
compile_python_total 150625
multi_frontend_codegen_parallel 145154
multi_frontend_codegen_worker_commands 124459
multi_frontend_export_parallel 20637
worker parse sum 41812
worker infer sum 16211
worker codegen sum 180234
```

This was the first stage2-only performance signal. It did not replace the full
GC4 bootstrap gate; the focused full gate below was run afterwards.

## Sampling
Two `sample(1)` probes were run against the GC4 stage2 worker path:

1. Immediate sample after worker spawn mostly captured `parse_and_lift`; it was
   too early for the dominant codegen phase.
2. A delayed sample 45 seconds after worker spawn captured the codegen phase.

The delayed sample put the inclusive stack under:

```text
run_python_multi_codegen_worker
  L1CodeGen.generate
    _generate_impl
      _emit_user_function
        _emit_stmt / _emit_if / _emit_expr
```

The top-of-stack summary still showed GC runtime overhead inside that codegen:

```text
pcc_gc_ptr_index_upsert          258
pcc_gc_object_index_insert       243
pcc_gc_ptr_index_insert_raw      200
pcc_gc_object_index_find         125
pcc_gc_load_ptr                  110
user_py_gc_backend__init_config   93
pcc_gc_backend                    75
```

This denies the "worker startup is the bottleneck" hypothesis for the current
GC4 stage2 shape: the stage2 profile has 142337 ms in worker commands, while
the worker parse+infer+codegen sums total about 272312 ms across two workers,
whose ideal 2-way lower bound is about 136156 ms. The unexplained gap is only a
few seconds, not the dominant 150s-scale cost.

## Proposals

- No.1 optimize worker startup / shell wrapper [DENIED for current GC4 hotspot]
- No.2 reduce pcc-Python GC config/backend double-dispatch [CONFIRMED stage2]
- No.3 use flamegraph for remaining codegen/GC split [DONE]
- No.4 AST-wire reuse for repeated parse/lift [DENIED]
- No.5 table-drive `cli_bootstrap.py` C-API symbol classification
  [CONFIRMED WEAK]

## No.1 optimize worker startup / shell wrapper

### DENIED for current GC4 hotspot
The delayed sample and profile do not support worker startup as the main cost.
The shell wrapper sampled as `wait4`, and the real worker process spent its
time inside frontend codegen plus runtime GC/index helpers. Startup may still
matter after the main codegen/runtime costs drop, but it is not the first GC4
optimization target.

## No.2 reduce pcc-Python GC config/backend double-dispatch

### Code Change
`pcc/py_runtime/py/py_gc_backend.py` now makes `_init_config()` return the
current backend:

- first call parses env and stores config exactly as before, then returns
  `backend`;
- later calls return `pcc_gc_backend_selected` directly;
- exported `pcc_gc_backend()` returns `_init_config()`;
- hot paths that already call `_init_config()` reuse that backend value instead
  of immediately calling exported `pcc_gc_backend()` again.

Updated hot paths include:

- `pcc_gc_backend4_try_zpage_alloc`
- `pcc_gc_step`
- `pcc_gc_note_alloc`
- `pcc_gc_note_object_allocated_sized`
- `pcc_gc_select_relocation_set`
- `pcc_gc_backend4_evacuation_page_drain`
- `pcc_gc_note_frame_leave`

### Correctness Boundary
This does not change backend selection semantics. `_init_config()` still reads
environment config once. After initialization it reloads the selected backend
global, so an explicit `pcc_gc_set_backend()` remains visible to later calls.

The C runtime path already used `pcc_gc_selected_backend` directly in several
hot functions after `pcc_gc_init_config()`. This patch makes the pcc-Python
mirror closer to that shape.

### Test
Focused verification run so far:

```text
env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_gc_backend.py \
  tests/python/gc/test_gc_backend_config_fastpath.py

env -u LC_ALL uv run pytest \
  tests/python/gc/test_gc_backend_config_fastpath.py -q -n0
# 2 passed

env -u LC_ALL uv run pytest \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback \
  -q -n0
# 1 passed
```

Focused full GC4 bootstrap gate after the patch:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py \
  -q -n0 -s

[stage2] PCC_GC_BACKEND=4 frontend_jobs=10 self_backend_jobs=12
[stage3] PCC_GC_BACKEND=4 frontend_jobs=10 self_backend_jobs=12
Bootstrap OK under PCC_GC_BACKEND=4: pcc2 and pcc3 are byte-identical.
1 passed in 106.19s
```

That gate checks:

- shared stage1 was rebuilt because the runtime source changed,
- stage2 and stage3 both compiled fresh,
- `pcc1`, `pcc2`, and `pcc3` do not link libpython,
- `pcc2` and `pcc3` are byte-identical after normalization.

Resulting focused GC4 profile:

```text
stage2 wall_ms 48357, compile_wall_ms 47527
stage3 wall_ms 47505, compile_wall_ms 46653
total wall_ms 95862
total compile_wall_ms 94180
multi_frontend_jobs 10
multi_frontend_codegen_worker_commands total 69990
multi_frontend_export_parallel total 13342
```

### CONFIRMED
GC4 stage2 compile wall improved in the focused probe:

```text
baseline current artifact: 173702 ms compile wall
after fast path probe:     150683 ms compile wall
delta:                    -23019 ms (-13.3%)
```

The full focused GC4 gate then passed in 106.19s with `frontend_jobs=10`.

## No.3 flamegraph for remaining codegen/GC split

### DONE
Process-tree `sample(1)` captures were converted to folded stack text because
no external FlameGraph script was installed on this host. The corrected
codegen-window capture showed the remaining GC4 cost under:

```text
run_python_multi_codegen_worker
parse_and_lift / Parser._parse_*
_native_export_from_wire
L1CodeGen.generate
  emit_stmt / emit_expr / emit_call
  control-flow lowering
  typed-int ABI safety collection
  hoist/nested walk
```

The worker timing counters repeatedly identify module index 1,
`pcc.cli_bootstrap`, as the largest codegen unit. This denies worker startup as
the primary GC4 bottleneck: worker command wall time tracks real parse/lift,
type inference, and codegen work, not shell launch overhead.

## No.4 AST-wire reuse for repeated parse/lift

### DENIED
The flamegraph made duplicated parse/lift look like a high-leverage target, so
an AST sidecar protocol was implemented and correctness-fixed through native
JSON string/control-character/float round-trips. The final correctness-focused
GC4 gate passed:

```text
PCC_PY_FRONTEND_AST_WIRE=1
tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s
# 1 passed in 128.35s, pcc2/pcc3 byte-identical
```

But the same gate with AST wire disabled was faster:

```text
PCC_PY_FRONTEND_AST_WIRE=0
tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s
# 1 passed in 105.95s, pcc2/pcc3 byte-identical
```

AST wire is therefore kept as an explicit diagnostic feature and disabled by
default. JSON sidecar serialization/deserialization costs more than the saved
worker parse/lift in the current pcc1 runtime.

## No.5 table-drive `cli_bootstrap.py` C-API symbol classification

### CONFIRMED WEAK
`pcc.cli_bootstrap` is the largest module in the bootstrap closure and the
module index that dominates worker codegen. Its C-API header/implemented
classification was mechanically converted from long `if symbol == ... or ...`
chains to static tuples plus small lookup loops. This preserves the pcc1-native
package surface and only changes control-flow shape.

Focused tests:

```text
env -u LC_ALL uv run pytest tests/python/test_cli_bootstrap_observability.py -q -n0
# 10 passed in 0.25s

env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py \
  -q -n0 -s
# 1 passed in 105.49s, pcc2/pcc3 byte-identical
```

Focused GC4 compile profile after this change:

```text
stage2 compile_python_total 45111 ms
stage3 compile_python_total 46109 ms
stage2+stage3 compile total 91220 ms
```

This is real but weak. It does not bring the five-GC matrix below 200s.

## No.6 split pcc1-native array-core block out of `cli_bootstrap.py`

### CONFIRMED WEAK
The next measured tail was `pcc.cli_bootstrap`, so the largest pcc1-native
package block was split into a sibling module:

```text
pcc/cli_bootstrap.py              11770 lines -> 7027 lines
pcc/cli_bootstrap_array_core.py                4831 lines
```

The split keeps the pcc1-native array-core implementation compiled. It does
not delegate to host Python, does not remove any package command, and keeps
both `pcc.package.array_core` and `pcc.package array-core` routed through the
same native entrypoint.

Focused coverage:

```text
env -u LC_ALL uv run pytest tests/python/test_cli_bootstrap_observability.py -q -n0
# 12 passed in 0.21s

env -u LC_ALL uv run pytest \
  tests/python/test_package_array_core.py::test_pcc_package_array_core_cli \
  tests/python/test_cli_bootstrap_observability.py::test_bootstrap_array_core_split_module_keeps_native_report_shape \
  tests/python/test_cli_bootstrap_observability.py::test_bootstrap_cli_routes_array_core_to_split_native_module \
  -q -n0
# 3 passed in 9.66s

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  --emit-llvm=/tmp/pcc_cli_array_core_no_lib.ll pcc/cli_bootstrap_array_core.py
# passed

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  --emit-llvm=/tmp/pcc_cli_bootstrap_no_lib.ll pcc/cli_bootstrap.py
# passed
```

Focused GC4 evidence after the split:

```text
env -u LC_ALL uv run pytest tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0
# 1 passed in 106.55s, pcc2/pcc3 byte-identical

stage2 compile_python_total 45465 ms
stage3 compile_python_total 42572 ms
```

The split moved the worker max module from `pcc.cli_bootstrap` to
`pcc.py_frontend.pipeline`:

```text
module index 1: pcc.cli_bootstrap             235189 bytes
module index 2: pcc.py_frontend.pipeline      336368 bytes
module index 3: pcc.cli_bootstrap_array_core  160845 bytes

multi_frontend_worker_codegen_max_index 2
```

This is a real structural cleanup and weak compile-profile improvement, but it
does not solve the matrix target. The new critical path is shared
frontend/pipeline codegen, not the collector and not the array-core shim.

## No.7 export-wire list fast path

### DENIED
The flamegraph showed `_native_export_from_wire` in each worker, so a fast path
was tried that preserved JSON lists where possible instead of recursively
converting every list to a tuple. Host-side focused tests and no-lib compile
passed after preserving tuple form for encoded type descriptors, but the real
bootstrap gate denied the change:

```text
env -u LC_ALL uv run pytest tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0

stage2: passed
stage3: failed
PyPipelineError: codegen[pcc.py_frontend.codegen.user_function_lowering]:
NotImplementedError: class 'LowCallDirect' with kwargs needs __init__ to resolve parameter names
```

That is a pcc2 -> pcc3 boundary failure, so the fast path was reverted. The
existing recursive tuple restoration is part of the current fixed-point
contract until type-infer/codegen can prove list-shaped export metadata is
semantically equivalent under pcc1 and pcc2.

Repair validation after the revert:

```text
env -u LC_ALL uv run pytest \
  tests/python/test_py_frontend_ir_pass_pipeline.py::test_native_export_wire_preserves_expression_defaults \
  tests/python/test_py_frontend_ir_pass_pipeline.py::test_parallel_frontend_codegen_uses_shared_export_context \
  -q -n0
# 2 passed in 0.32s

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  --emit-llvm=/tmp/pipeline_export_wire_revert_no_lib.ll pcc/py_frontend/pipeline.py
# passed

env -u LC_ALL uv run pytest tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0
# 1 passed in 125.05s, pcc2/pcc3 byte-identical

stage2 wall_ms 48252
stage3 wall_ms 48741
stage2+stage3 wall_ms 96993
multi_frontend_worker_codegen_max_index 2
```

## Next
Continue with backend #3/#2/#1/#0 only after preserving the current GC4
evidence. The remaining high-leverage path is now shared frontend work:
`pcc.py_frontend.pipeline` module codegen, repeated worker export
deserialization, and the parse/lift duplication that AST wire failed to
eliminate cheaply. Any export-wire shape change must be proven by a pcc2 ->
pcc3 gate, not host tests alone.
