# Investigation: GC0 bootstrap runtime hot paths

## Status
open. Backend #0 focused bootstrap improved, but the full parallel
`tests/python/gc` matrix is not yet under 200 seconds on this host.

## Problem Description
Backend #0 is the refcount/cycle reference backend. It has no tracing step,
relocation, or generational algorithm to tune for bootstrap. After the GC4,
GC3, GC2, and GC1 dispatch slices, the remaining GC0 evidence points at shared
pcc-Python runtime and frontend/codegen worker costs.

The target remains strict: fresh `pcc1 -> pcc2 -> pcc3` for every GC backend,
no libpython linkage, and byte-identical `pcc2`/`pcc3` after normalization.

## Current Data
Focused GC0 baseline after the common `py_obj.py` backend fast path:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  -q -n0 -s

Bootstrap OK under PCC_GC_BACKEND=0: pcc2 and pcc3 are byte-identical.
1 passed in 43.53s
```

Profile:

```text
stage2 total_ms 20623
stage3 total_ms 20087
total stage2+stage3 40710
```

After inlining the hottest `py_obj.py` backend fast path sites:

```text
stage2 total_ms 20455
stage3 total_ms 20084
total stage2+stage3 40539
delta -171 ms (-0.4%)
```

After adding the two-byte `_strs_eq` prefix rejection in `py_class.py`:

```text
stage2 total_ms 19306
stage3 total_ms 19174
total stage2+stage3 38480
delta vs 40539 -2059 ms (-5.1%)
delta vs 40710 -2230 ms (-5.5%)
```

The focused GC0 gate passed with fresh stage2/stage3 and byte-identical
`pcc2`/`pcc3`.

## Sampling
The delayed GC0 codegen-worker sample hit the real codegen window:

```text
L1CodeGenEntrypointMixin_generate                         7
GenerationLoweringMixin__generate_impl                   93
UserFunctionLoweringMixin__emit_user_function            48
StmtDispatchLoweringMixin__emit_stmt_impl              1883
ControlFlowLoweringMixin__emit_if                      1263
ExprDispatchLoweringMixin__emit_expr_impl              8396
CallExpressionLoweringMixin__emit_call                 3030
LiteralLoweringMixin__emit_tuple_literal                452
OwnershipLoweringMixin__emit_current_gc_frame_enter     478
OwnershipLoweringMixin__emit_gc_frame_leave_for_slot    415
OwnershipLoweringMixin__gc_release                      322

pcc_gc_backend                                           196
user_py_gc_backend__init_config                          217
user_py_obj__gc_backend_fast                             379
pcc_gc_step                                                0
pcc_gc_load_ptr                                          945
pcc_gc_store_ptr                                         268
py_incref                                               1060
py_decref                                               1017
user_py_class__strs_eq                                  4701
user_py_class__class_lookup_in_mro                      1223
_platform_strlen                                        3091
```

This denies a backend #0 collector-step hypothesis: `pcc_gc_step` sampled zero.
The hot path is shared pcc-Python runtime and frontend codegen.

## Proposals

- No.1 inline the hottest `py_obj.py` backend fast path sites [CONFIRMED WEAK]
- No.2 add prefix rejection before `_strs_eq` calls `strlen` [CONFIRMED]
- No.3 four-byte `_strs_eq` prefix rejection [DENIED]
- No.4 active-GC lease / matrix scheduling [CONFIRMED WEAK]
- No.5 process-tree folded-stack/flamegraph for remaining shared hotspots
  [DONE]
- No.6 AST wire reuse between export/codegen workers [DENIED]
- No.7 AST wire reuse after native JSON escape fix [DENIED]
- No.8 table-drive pcc1 C-API symbol classification in `cli_bootstrap.py`
  [CONFIRMED WEAK]

## No.1 inline hottest `py_obj.py` backend fast path sites

### Code Change
`pcc_gc_load_ptr`, `pcc_gc_store_ptr`, `py_incref`, and `py_decref` now inline
the steady-state backend selection:

```text
if pcc_gc_config_initialized == 0:
    backend = pcc_gc_backend()
else:
    backend = pcc_gc_backend_selected
```

This is the same logic as `_gc_backend_fast()`, only without an extra
pcc-Python function call in the hottest reference/load/store paths.

### Correctness Boundary
The first call still initializes backend config through `pcc_gc_backend()`.
Once initialized, explicit backend switching remains visible through
`pcc_gc_backend_selected`. The change does not alter refcount ownership,
relocation read barriers, write barriers, or no-libpython mode.

### Evidence
Focused checks passed:

```text
tests/python/gc/test_gc_backend_config_fastpath.py -q -n0
# 4 passed before the class hotpath guard, 5 passed after it

test_py_obj_runtime_refcount_primitives_do_not_self_root
test_py_gc_backend_runtime_file_compiles_without_libpython_fallback
# 2 passed
```

Focused full bootstrap gates after the inline change:

```text
GC0: tests/python/gc/test_pcc_bootstrap_full_gc0.py -q -n0 -s
# 1 passed, pcc2/pcc3 byte-identical

GC4: tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s
# 1 passed, pcc2/pcc3 byte-identical

GC3: tests/python/gc/test_pcc_bootstrap_full_gc3.py -q -n0 -s
# 1 passed, pcc2/pcc3 byte-identical
```

Sample effect:

```text
user_py_obj__gc_backend_fast 379 -> 100
```

Profile effect was weak:

```text
40539 ms -> 38480 ms only after the later `_strs_eq` patch.
The inline-only slice was 40710 ms -> 40539 ms (-0.4%).
```

## No.2 add prefix rejection before `_strs_eq` calls `strlen`

### Code Change
`pcc/py_runtime/py/py_class.py::_strs_eq()` now compares the first two bytes
before calling `strlen(a)` and `strlen(b)`. Each byte comparison checks for
NUL before reading the next byte, preserving C-string equality semantics.

### Correctness Boundary
The function still returns true exactly when the two NUL-terminated C strings
are equal. It only avoids `strlen` on common unequal-name cases in class and
field lookup.

### Evidence
Focused GC0 profile:

```text
before `_strs_eq` prefix fast path total_ms 40539
after  `_strs_eq` prefix fast path total_ms 38480
delta -2059 ms (-5.1%)
```

The strict focused bootstrap gate passed:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  -q -n0 -s

Bootstrap OK under PCC_GC_BACKEND=0: pcc2 and pcc3 are byte-identical.
1 passed in 68.42s
```

The wall time includes shared stage1 rebuild.

## No.3 four-byte `_strs_eq` prefix rejection

### DENIED
Extending the prefix rejection from two bytes to four bytes preserved semantics
but regressed the focused GC0 profile:

```text
two-byte prefix total_ms 38480
four-byte prefix total_ms 39461
```

The four-byte attempt was removed. Keep the two-byte version.

## No.4 active-GC lease / matrix scheduling

### CONFIRMED WEAK
The split GC files are xdist-parallel, but running all five full bootstrap
chains at once starves heavy backends. `tests/python/test_pcc_bootstrap_full.py`
now has a file-lock active-GC lease:

- xdist can still schedule all files,
- only a bounded number of full GC chains actively compile,
- waiting files publish `waiting-gc*` markers,
- weighted acquisition prefers GC4/GC3 before lighter backends,
- stale active/waiting markers are pruned by pid,
- each backend keeps the same frontend/self-backend job counts for stage2 and
  stage3 so `pcc2`/`pcc3` byte identity is not perturbed.

The default is now max-active 3 with per-active minimum 6 jobs. Overrides:

```text
PCC_BOOTSTRAP_FULL_MAX_ACTIVE_GC
PCC_BOOTSTRAP_FULL_PARALLEL_MIN_JOBS
```

Scheduling evidence:

```text
5-way without active lease: GC4 stage2 alone was 168313 ms at jobs=4.
max-active=2/jobs=8: GC4 stage2+stage3 completed in 143388 ms.
max-active=3/jobs=8: GC4 stage2+stage3 regressed to 226833 ms.
max-active=3/jobs=6: GC4 stage2+stage3 improved to 197382 ms but still
missed the matrix timeout.
warm full matrix, max-active=2/jobs=8: 10 passed in 259.33s.
warm full matrix, max-active=3/jobs=6: 10 passed in 247.56s.
```

This is a real scheduling improvement, but it is not close to the requested
200s target. It also confirms that scheduling alone is not the remaining root
cause: under max-active=3/jobs=6, GC4 stage2+stage3 still consumed about
197.5s of compile time.

## No.5 process-tree folded-stack/flamegraph for remaining hotspots

### DONE
After the GC-local fast paths stopped moving the matrix enough, I captured
process-tree `sample(1)` output and folded stacks for backend #4 stage2.
No external FlameGraph scripts are installed on this host
(`sample(1)` and `dtrace` are present; `flamegraph.pl`,
`stackcollapse-sample.pl`, `inferno-flamegraph`, and `py-spy` are not), so the
profile harness sampled all live pcc1 frontend worker processes and wrote a
folded stack text file.

Artifacts:

```text
/tmp/pcc-gc-flame-gc4/combined.folded
/tmp/pcc-gc-flame-gc4-codegen2/combined.folded
```

The first capture sampled the export window and showed
`build_closed_world_context -> parse` as the hot export-worker path. That is a
real cost, but it is not the full-stage bottleneck by itself: profile counters
show GC4 stage2/stage3 spend about 14-16s wall in export and about 74-79s wall
in codegen worker commands in the slower parallel runs.

The corrected codegen-window capture sampled `worker_*.manifest` processes and
showed the remaining broad hotspot:

```text
user_pcc_parse_py_lift_parse_and_lift
user_pcc_parse_py_parse_Parser__parse_stmt / _parse_block / _parse_expr
user_pcc_py_frontend_pipeline__native_export_from_wire
user_pcc_py_frontend_codegen_*_emit_stmt / emit_expr
user_pcc_py_frontend_codegen_control_flow_lowering_ControlFlowMixin__emit_if
user_pcc_py_frontend_codegen_typed_int_abi_*collect_typed_int_abi_call_safety*
user_pcc_py_frontend_codegen_hoist_lowering___nested_walk_1
user_pcc_py_frontend_codegen_call_expression_lowering_CallExpressionMixin__emit_call
```

The worker timing counters identify the largest module-level frontend/codegen
unit as module index 1 (`pcc.cli_bootstrap`). That file is about 11.9k lines and
contains the bootstrap CLI plus pcc1-native package tooling. Do not "optimize"
the matrix by trimming that module out of the bootstrap closure or delegating
those pcc1-native package commands to host Python: a prior minimal-CLI
experiment already improved bootstrap time but broke the no-host package gate.

Micro-benchmarking the native-export wire read on the captured 5.8 MiB
`native_exports.json` showed `_read_native_exports_wire()` at roughly
190-240ms per worker. That is visible in the flame stack, but it is only about
1-2 seconds per stage at 8 frontend workers, so it is not the main route to the
200s matrix target.

### Finding
The remaining matrix miss is no longer a collector-local bottleneck. It is a
shared Python frontend/codegen bottleneck amplified by running five GC-flavored
stage2/stage3 chains in parallel:

```text
export parse/lift +
codegen-worker parse/lift +
large-module codegen for pcc.cli_bootstrap +
normal emit_stmt/emit_expr/call/hoist/typed-int safety work
```

An AST/wire reuse protocol looked like the next high-leverage optimization
from this flamegraph, but the first implementation was denied by bootstrap
correctness evidence below. The remaining plausible routes are fixing the
underlying pcc1 JSON/string escape semantics first, or a
functionality-preserving split of `pcc.cli_bootstrap.py` that keeps the
pcc1-native package surface compiled. Blind GC object-index/frame-index
rewrites are denied by this flamegraph.

## No.6 AST wire reuse between export/codegen workers

### DENIED
The measured bottleneck includes duplicated codegen-worker parse/lift, so I
tested a worker protocol that let export workers write lifted AST shards and
codegen workers read those shards instead of parsing the same module again.

The protocol was made no-libpython compilable by replacing dynamic AST
construction with explicit node constructors and by avoiding `list(bytes)`.
The protocol then reached codegen, and the expected local counter moved in the
right direction:

```text
GC4 stage2 jobs=8, AST-wire attempt:
multi_frontend_worker_parse_sum_ms 42409 -> 1309
multi_frontend_worker_parse_max_ms 5653 -> 1309
```

But the experiment failed the actual bootstrap correctness boundary:

```text
stage2 compile succeeded and produced pcc2
stage_exec_barrier failed during the pcc2 smoke compile
stderr: SyntaxError in the self-backend host python -c string
```

The concrete symptom was that `_SELF_BACKEND_HOST_CODE` reached pcc2 with
literal `\n` escape text where real newlines were required. That points at the
pcc1 JSON/string escape path used by the AST wire, not a GC backend. Host-side
AST equality was insufficient evidence here: CPython roundtripped the AST, but
compiled pcc1's JSON/string behavior did not preserve the bootstrap compiler's
source-string semantics.

This optimization was removed. The reverted clean GC4 stage2 profile returned
to the previous correct shape and passed the stage2 barrier:

```text
GC4 stage2 jobs=8 after removing AST wire:
compile_python_total 50944 ms
multi_frontend_export_parallel 6933 ms
multi_frontend_codegen_worker_commands 37100 ms
multi_frontend_worker_parse_sum_ms 42409
stage2 elapsed_ms 51720, returncode 0
```

Keep this specific reverted route closed. The underlying pcc1 JSON/string
escape semantics have since been reduced and fixed separately in
`pcc1-native-json-string-escapes.md`, but AST-wire reuse still needs a fresh
proposal and bootstrap proof before it can be re-enabled.

## No.7 AST wire reuse after native JSON escape fix

### DENIED
After fixing native JSON string escapes, control-character dumping, and
float/Infinity round-trips, the AST-wire protocol reached a full focused GC4
bootstrap gate and preserved `pcc2`/`pcc3` byte identity:

```text
PCC_PY_FRONTEND_AST_WIRE=1
tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s
# 1 passed in 128.35s, pcc2/pcc3 byte-identical
```

The same focused gate with AST wire disabled was faster:

```text
PCC_PY_FRONTEND_AST_WIRE=0
tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s
# 1 passed in 105.95s, pcc2/pcc3 byte-identical
```

The profile explained the denial. AST wire removed most codegen-worker
parse/lift, but native JSON sidecar write/read moved enough work into the
export and worker phases that the total wall time regressed. The feature is
kept as an explicit diagnostic switch and is off by default:

```text
PCC_PY_FRONTEND_AST_WIRE=1
```

Correctness tests for the serializer remain because the experiment found real
JSON runtime bugs, but AST wire is not a bootstrap-speed optimization in its
current JSON-sidecar form.

## No.8 table-drive pcc1 C-API symbol classification in `cli_bootstrap.py`

### Code Change
`pcc/cli_bootstrap.py` had two large bootstrap-native C-API classification
functions:

```text
_native_known_capi_header     487 lines
_native_capi_implemented      420 lines
```

They were mechanically converted to tuple-backed lookup tables plus small
linear scan helpers. The symbol/header mapping and implemented-symbol set were
extracted from the old AST before replacement, avoiding hand-copied table
drift.

### Correctness Boundary
This is a refactor of pcc1-native package diagnostics and C-API support
classification only. It does not remove any pcc1 CLI command, does not delegate
package tooling to host Python, and does not alter bootstrap stage reuse,
no-libpython checks, or pcc2/pcc3 comparison.

### Evidence
Focused behavior test:

```text
env -u LC_ALL uv run pytest tests/python/test_cli_bootstrap_observability.py -q -n0
# 10 passed in 0.25s
```

Focused GC4 gate after the table-driven refactor:

```text
env -u LC_ALL uv run pytest \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py \
  -q -n0 -s

Bootstrap OK under PCC_GC_BACKEND=4: pcc2 and pcc3 are byte-identical.
1 passed in 105.49s
```

Profile for that focused GC4 run:

```text
stage2 compile_python_total 45111 ms
stage3 compile_python_total 46109 ms
stage2+stage3 compile total 91220 ms
multi_frontend_ast_wire_enabled 0
```

This is a weak but correct improvement. It reduces the large-module codegen
shape a little, but it does not address the dominant repeated frontend/codegen
work enough to bring the full matrix under 200s.

## Current Verification
Passed:

```text
black / py_compile for touched files
tests/python/gc/test_gc_backend_config_fastpath.py -q -n0
tests/python/test_py_multi_file_bootstrap_shim.py::...test_py_gc_backend_runtime_file_compiles_without_libpython_fallback
tests/python/gc/test_pcc_bootstrap_full_gc0.py -q -n0 -s
tests/python/gc/test_pcc_bootstrap_full_gc3.py -q -n0 -s
tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s
bootstrap harness plan/unit tests
process-tree sample/folded-stack captures for GC4 export and codegen windows
GC4 stage2 AST-wire denied experiment, followed by reverted stage2 barrier pass
tests/python/gc/test_pcc_bootstrap_full_gc4.py -q -n0 -s
# 1 passed in 102.51s, pcc2/pcc3 byte-identical on the final source state
tests/python/test_py_frontend_ast_wire.py -q -n0
tests/python/test_cli_bootstrap_observability.py -q -n0
tests/python/test_pcc_bootstrap_full.py -k 'process_group or bootstrap_matrix_plan or bootstrap_gc' -q -n0
tests/python/test_python_module_imports_parity.py::test_import_json_string_escape_roundtrip -q -n0
tests/python/test_python_module_imports_parity.py::test_import_json_float_roundtrip -q -n0
tests/python/test_pcc1_python_smoke.py::test_pcc1_smoke_json_loads -q -n0
```

Not passed:

```text
env -u LC_ALL uv run pytest tests/python/gc -q -s
# warm max-active=2/jobs=8: 10 passed in 259.33s
# warm max-active=3/jobs=6: 10 passed in 247.56s
```

## Conclusion
GC0 itself is not the remaining collector bottleneck. The confirmed safe
changes are the hot `py_obj.py` dispatch inline, the two-byte `_strs_eq`
prefix rejection, the active-GC lease/default resource budget, and the
table-driven `cli_bootstrap.py` C-API symbol classification. AST wire reuse is
denied as a performance optimization even after its JSON correctness blockers
were fixed, because its JSON sidecar costs more than it saves.

The process-tree folded stacks show that the remaining matrix miss is shared
frontend/codegen work: duplicated parse/lift when AST wire is off, expensive
JSON sidecars when AST wire is on, and large-module codegen for
`pcc.cli_bootstrap` / `pcc.py_frontend.pipeline`. A functionality-preserving
split of the pcc1-native array-core block moved the max worker index from
`pcc.cli_bootstrap` to `pcc.py_frontend.pipeline`, but it did not bring the
full matrix under 200s. An export-wire list fast path was denied by a pcc2 ->
pcc3 failure and reverted. The next safe work should target a no-libpython-safe
worker/export protocol or pipeline codegen shape, with pcc2 -> pcc3 proof for
any metadata-shape change.
