# Investigation: bootstrap self-backend time after layer1 split

## Status

active

## Problem Description

After the `layer1.py` split work, `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
still passes but takes roughly 55-62 seconds. The user asked whether the
three-stage self bootstrap can be optimized toward 30 seconds and what is slow.

## Repro

Full stage timing:

```bash
env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 300 \
  bash scripts/bootstrap.sh --backend self --out-dir build/bootstrap-profile-self --stage 3
```

Observed after the split:

```text
stage1 elapsed_ms=17717
stage2 elapsed_ms=27140
stage3 elapsed_ms=19731
```

Focused emit-only timings:

```bash
env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 180 \
  time uv run python -m pcc --ir-scaffold=on --python-libpython=off \
  --backend self --emit-llvm=/tmp/pcc_stage1_emit.ll pcc/__main__.py

env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 180 \
  time build/bootstrap-profile-self/pcc1 --ir-scaffold=on --python-libpython=off \
  --backend self --emit-llvm=/tmp/pcc_stage2_emit.ll pcc/__main__.py

env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 180 \
  time build/bootstrap-profile-self/pcc2 --ir-scaffold=on --python-libpython=off \
  --backend self --emit-llvm=/tmp/pcc_stage3_emit.ll pcc/__main__.py
```

Observed:

```text
stage1 emit-only: 11.15s real
stage2 emit-only: 15.45s real
stage3 emit-only: 16.07s real
```

IR size:

```text
/tmp/pcc_stage2_emit.ll: 36,439,142 bytes, 712,982 lines
/tmp/pcc_stage3_emit.ll: 36,439,142 bytes, 712,982 lines
```

## Findings

The main cost is frontend/type/codegen/IR generation, not final system link.
For stage2, emit-only is about 15.45 seconds while a full pcc1 compile to a
binary was about 20.83 seconds in a focused run. That means most of stage2 is
spent before native linking.

`PCC_PYTHON_IR_PASS_TRANSPORT=memory` was tested and was slower for the focused
stage2 full compile:

```text
default: 20.83s real
PCC_PYTHON_IR_PASS_TRANSPORT=memory: 27.06s real
```

`PCC_SELF_BACKEND_JOBS=4` was also slower than the observed default:

```text
default: 20.83s real
PCC_SELF_BACKEND_JOBS=4: 22.56s real
```

The current 30-second target cannot be reached by a small link-flag or transport
toggle alone. It requires reducing repeated frontend/IR work or improving pcc1
/ pcc2 execution speed.

## Current Hypothesis

The highest-value optimization paths are:

1. Add real phase timing to `--profile-json`; the current bootstrap profile JSON
   writes `total_ms: 0.0` and cannot localize parse/type-infer/codegen/link.
2. Shrink generated IR for the self-host closure. The stage2/stage3 IR is about
   36 MB.
3. Avoid repeated work across stage2/stage3 where possible, or make the
   self-hosted compiler run the Python frontend faster.
4. Treat self-backend/link tuning as secondary until phase timing proves it is
   dominant.

## Update 2026-05-13

After the pcc1 GC/threading fixes, the full self-bootstrap still passes but
remains in the same wall-time band:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 58.89s
  1 passed in 59.25s
```

Direct `scripts/bootstrap.sh` timing from the same output directory:

```text
stage1 elapsed_ms=15644
stage2 elapsed_ms=21559
stage3 elapsed_ms=21628
```

So the current cost is not pytest overhead. The slow part is the repeated
self-hosted compile of `pcc/__main__.py`: pcc1 and pcc2 each spend about 21.6s
to compile the 102-module closed-world compiler.

One pcc1 `--verbose --profile-json` run showed:

```text
multi_input_files=102
multi_files=102
multi_ir_modules=102
multi_ir_bytes=41816516
multi_ir_bytes_before_passes=41816516
```

Largest visible emitted IR files in that run included:

```text
pcc.py_frontend.codegen.hoist_lowering      2708332 bytes
pcc.py_frontend.pipeline                    2142465 bytes
pcc.py_frontend.type_infer                  1504567 bytes
pcc.parse.py_parse                          1417988 bytes
pcc.py_frontend.codegen.class_gen           1175993 bytes
pcc.llvm_capi.ir                            1027309 bytes
pcc.py_frontend.codegen.user_function        961146 bytes
pcc.py_frontend.codegen.call_expression      867635 bytes
pcc.py_frontend.codegen.native_modules       855339 bytes
pcc.py_frontend.codegen.ir_scaffold_lowering 754595 bytes
```

The pcc1 profile JSON still cannot be trusted for phase timings:

```text
phase_totals_s entries are all 0
total_ms is 0
```

Two focused probes explain why this remains broken:

```text
import time; print(time.monotonic())
  works natively and returns increasing floats

import time; print(time.perf_counter_ns())
  still requires libpython fallback

print(str(1.25))
  prints <null> under pcc1
```

`pcc/cli_bootstrap.py::_json_float()` writes profile values with `str(value)`,
so float profile values collapse in the compiled bootstrap CLI. Before trying
to optimize toward 30s, fix the pcc1 profile timing path so phase timings are
real. A practical short-term fix is to store profile times as integer
milliseconds/microseconds inside `pcc/py_frontend/pipeline.py` and have
`cli_bootstrap.py` write integer JSON values without relying on `str(float)`.

## Update 2026-05-14

The pcc1 profile timing path was changed to record integer milliseconds and to
write integer `total_ms` / `phase_totals_ms` in `cli_bootstrap.py`, while
keeping the legacy `phase_totals_s` field for compatibility. This avoids the
pcc1 `str(float)` gap and makes bootstrap profiles useful.

Focused pcc1 profile for compiling `pcc/__main__.py`:

```bash
PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py env -u LC_ALL \
  perl -e 'alarm shift; exec @ARGV' 180 \
  build/bootstrap-pytest-self/pcc1 \
  --backend self --python-libpython off --ir-scaffold=on \
  --profile-json /tmp/pcc_stage2_profile_fixed.json \
  pcc/__main__.py -o /tmp/pcc_stage2_profile_fixed_bin
```

Observed:

```text
total_ms=22578
auto_files=102
multi_files=102
multi_ir_modules=102
multi_ir_bytes=41838068

compile_python_total        22578 ms
compile_python_multi_total  22396 ms
multi_codegen_layer1        11429 ms
link_native                  6100 ms
build_closed_world_context   2163 ms
multi_type_infer             2113 ms
libpython_scan                110 ms
ensure_runtime                 50 ms
emit_ll_many                   41 ms
```

This changes the optimization target:

1. `multi_codegen_layer1` is the largest single bucket.
2. `link_native` / self-backend object emission is also large enough to matter.
3. `build_closed_world_context` and `multi_type_infer` are secondary but still
   visible.
4. `emit_ll_many`, `ensure_runtime`, and libpython scanning are not the current
   bottleneck.

To reach a 30s full three-stage bootstrap from the current ~59s, the rough
target is to remove about 14-15s from stage2+stage3 combined. That likely
requires reducing codegen work and self-backend link/object work, not only
shell-script tuning.

## Update 2026-05-14: pcc1 dict.get lowering hazard in isinstance

An attempted extraction of `isinstance` lowering into
`pcc.py_frontend.codegen.isinstance_lowering` exposed a stage2-to-stage3
semantic bug: pcc1 could build pcc2, but pcc2 compiled even `x = 1` as if
the parsed `Assign` node were only the base `Stmt`.

The minimized behavioral probe was:

```python
class A:
    pass

a = A()
print(isinstance(a, A))
```

Host pcc compiled this to `True`, but pcc1 compiled it to `False`. Comparing
the emitted IR showed that pcc1 lowered the custom-class check as:

```text
py_obj_type_tag(a) == 0
```

instead of:

```text
py_isinstance(a, .class.A)
```

The root cause was the strict self-host lowering of:

```python
tag = _BUILTIN_TYPE_TAGS.get(class_ident)
if tag is None:
    return None
```

For a missing key such as `"A"`, the pcc1-compiled compiler treated the
missing `dict.get()` result as integer `0` rather than `None`. That sent
custom classes down the builtin-type-tag path and made every user class
`isinstance` check false. The fix in `layer1.py` is to avoid this fragile
missing-None shape on the bootstrap path:

```python
if class_ident not in _BUILTIN_TYPE_TAGS:
    return None
tag = _BUILTIN_TYPE_TAGS[class_ident]
```

The same explicit membership pattern is used for
`_BUILTIN_TYPE_MATCHERS`. This is a forward fix for the pcc1 semantic issue;
the larger extraction should wait for a true contextual L1CodeGen self-type
mechanism, not another raw mixin move.

Validation after the fix:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 62.94s
```

## Update 2026-05-14: per-module profile detail

The profile event schema now carries an optional `detail` field so repeated
events such as `multi_codegen_layer1` can name the module they measured.
Existing totals remain unchanged.

Focused pcc1 profile after adding event details:

```text
total_ms=21126

compile_python_total        21126 ms
compile_python_multi_total  20913 ms
multi_codegen_layer1        10981 ms
link_native                  5239 ms
build_closed_world_context   2130 ms
multi_type_infer             2097 ms
```

Top `multi_codegen_layer1` modules:

```text
1824 ms  pcc.py_frontend.codegen.hoist_lowering
 548 ms  pcc.py_frontend.pipeline
 239 ms  pcc.py_frontend.type_infer
 235 ms  pcc.py_frontend.codegen.class_gen
 233 ms  pcc.parse.py_parse
 229 ms  pcc.py_frontend.codegen.user_function_lowering
 226 ms  pcc.py_frontend.codegen.for_loop_lowering
 209 ms  pcc.py_frontend.codegen.call_expression_lowering
 202 ms  pcc.py_frontend.codegen.coercion_lowering
 198 ms  pcc.llvm_capi.ir
```

This changes the practical optimization order:

1. Audit `pcc.py_frontend.codegen.hoist_lowering` first; it is currently a
   clear outlier in self-host codegen time.
2. Then target `link_native` / self-backend object emission.
3. Continue shrinking `layer1.py`, but line count alone is no longer the
   strongest performance predictor now that `layer1.py` is around 1100 lines.

## Update 2026-05-14: first hoist helper extraction

`pcc.py_frontend.codegen.hoist_lowering` is dominated by one huge
`_hoist_nested_funcdefs` method with many nested helper functions. The first
safe extraction moved pure helpers that do not capture `_hoist_nested_funcdefs`
locals to module scope:

```text
name/copy map helpers
import-statement helpers
capture-name filters
body_reads_self
body_uses_name_as_value
body_returns_name
body_augassigns_free_name
```

This keeps behavior the same while reducing the nested-def/closure shape that
self-host codegen has to process for this file.

Measured pcc1 profile progression for `multi_codegen_layer1` on
`pcc.py_frontend.codegen.hoist_lowering`:

```text
1824 ms  before hoist helper extraction
1664 ms  after moving name/import/profile helpers
1499 ms  after moving capture/value walker helpers
1620 ms  final kept state after rejecting box_expr/box_stmts extraction
```

The full self-bootstrap gate remained green after the extraction:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 67.46s
```

The direction is correct but not sufficient for the 30s target by itself.
One attempted extraction of the recursive `box_expr` / `box_stmts` rewrite
helpers made the profile worse (`hoist_lowering` rose to about 2151 ms and
stage2 total to about 28s), so those helpers were kept nested. Further work
should continue only with helper groups that are not hot recursive rewrite
loops, then profile `link_native`.

## Update 2026-05-14: link_native breakdown

`link_native` was split into self-backend subevents. A focused pcc1 profile
for compiling `pcc/__main__.py` shows:

```text
link_native                       5595 ms
link_self_read_ll                 1913 ms
link_self_emit_objects_host       3543 ms
link_self_object_emit_subprocess  3526 ms
link_self_cc                        66 ms
link_self_normalize_ir              28 ms
link_self_write_object_inputs       16 ms
```

The important conclusion is that system `cc` / ld is not the bottleneck.
The expensive work is:

1. reading and normalizing the `.ll` files before self-backend emission
2. the host-Python self-backend object emission subprocess

Two tempting shortcuts were tested and rejected:

```text
PCC_SELF_BACKEND_SKIP_LL_TEMP=1
  total_ms=30067
  link_self_backend_ir_texts=9941
  link_self_normalize_ir=4497
  link_self_object_emit_subprocess=5242

PCC_SELF_BACKEND_JOBS=4
  total_ms=23794
  link_native=7850
  link_self_object_emit_subprocess=5679

Removing the host subprocess' redundant-looking `_self_backend_ir_text()`
normalization was also tested and rejected. It did not produce a stable
improvement (`link_self_object_emit_subprocess` was observed around
3.7-5.4s) and weakens the direct helper's input robustness, so the host-side
normalization remains in place.
```

So the current file-based path is faster than the direct IR-text path, and
reducing self-backend jobs to 4 is worse than the default. Do not flip either
setting as a bootstrap default. The next useful optimization is inside the
self-backend host emission path, not clang/ld or job-count tuning.

Additional host-subprocess counters show the current object emission shape:

```text
link_self_host_object_count       102
link_self_host_jobs                12
link_self_host_input_bytes   41879598
link_self_host_input_max_bytes 2702262

link_self_host_emit_asm_sum_ms 12458
link_self_host_emit_asm_max_ms   985
link_self_host_cc_sum_ms       18940
link_self_host_cc_max_ms         893
```

Because these sums run across parallel workers, they exceed wall time. They
still show that per-object `cc -c` is at least as important as Python
`emit_self_asm`. The likely optimization space is shard sizing / batching
inside the self-backend object emission path, not final linking.

## Update 2026-05-14: avoid redundant expanded IR files on the non-split path

The host self-backend object emitter used to serially read every `.ll` input,
normalize it, write an `expanded_*.ll` copy, and then let worker processes read
those expanded files.  In the common non-split path, the parent has already
written normalized `.ll` inputs, so the expanded copy adds one full extra
read/write pass over the bootstrap IR.

The non-split path now passes the original input paths directly to workers.
Each worker still calls `_self_backend_ir_text()` after reading the file, so
the input robustness of the host emitter is preserved.  The split-large-module
path still materializes expanded shards because it changes the module list.

Focused pcc1 profile:

```text
total_ms                         19991
multi_codegen_layer1             10225
link_native                       5124
link_self_emit_objects_host       3387
link_self_object_emit_subprocess  3374
link_self_read_ll                 1603
```

Self-bootstrap gate:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 55.88s
```

This is only a small improvement.  The remaining large bottleneck is still
`multi_codegen_layer1` at about ten seconds per pcc1 focused compile; link
native remains about five seconds.  Getting the full three-stage gate near
30s requires reducing codegen/type-infer/front-end work, not only self-backend
file movement.

## Update 2026-05-14: cheap hoist pre-scan halves focused Layer 1 codegen

`pcc.py_frontend.codegen.hoist_lowering` was still the largest single module
inside `multi_codegen_layer1`.  The hoist pass now starts with a cheap AST
pre-scan and skips the full nested-def/lambda helper graph for modules that
have no nested function/class, lambda, or generator-yield sentinel inside a
function body.

Focused pcc1 internal profile after the pre-scan:

```text
compile_python_total             13571
compile_python_multi_total       13411
multi_codegen_layer1              5660
link_native                       5066
link_self_emit_objects_host       3400
link_self_object_emit_subprocess  3336
build_closed_world_context        1607
link_self_read_ll                 1569
multi_type_infer                   788
```

Largest remaining per-module Layer 1 codegen event:

```text
pcc.py_frontend.codegen.hoist_lowering  539ms
```

During validation, `tests/python/test_py_nested_hoist.py` exposed a separate
self-compile source issue in `pcc/ir_passes/mem2reg.py`: strict Layer 1 does
not yet lower `min(iterable, key=lambda ...)` natively, and routing it through
the CPython callable bridge would violate the no-libpython self-host path. The
`mem2reg` site now uses an explicit loop to choose the minimum dominator depth,
which keeps the pcc pass source strict-frontend friendly without changing its
algorithm.

Focused gates after this change:

```text
tests/python/test_py_nested_hoist.py
  4 passed in 4.08s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 57.27s
```

This makes the next performance target clearer: the focused compile has moved
from Layer 1 codegen dominance toward self-backend object emission and native
linking.  Further bootstrap-time work should prioritize the self-backend
object emission path or a larger reduction in generated IR bytes.

## Update 2026-05-14: host split gate cleanup did not move wall time

The self-backend host object emitter now only enables the host-side large-IR
split path when the parent scan actually found a large module. The default
non-split path also inlines the small target-triple normalization helper so it
does not import `pcc.py_frontend.pipeline` just to emit objects.

Focused pcc1 profile after this cleanup:

```text
compile_python_total             14732
multi_codegen_layer1              6027
link_native                       5683
link_self_emit_objects_host       3845
link_self_object_emit_subprocess  3774
link_self_read_ll                 1749
build_closed_world_context        1688
multi_type_infer                   872
```

Self-bootstrap gate:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 56.34s
```

Conclusion: this cleanup is architecturally cleaner but does not materially
move the 30s target. The remaining wall time is dominated by generated IR size,
`emit_self_asm`, and one `cc -c` invocation per module/object. Future work
should focus on reducing IR bytes/object count or batching object emission,
not more host-process startup trimming.

## Update 2026-05-14: layer1 down to 603 lines, focused profile still not near 30s

After the contextual `isinstance_lowering` extraction and dead-constant cleanup,
`pcc/py_frontend/codegen/layer1.py` is down to 603 lines.

Self-bootstrap gate:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 53.20s
```

Focused pcc1 profile for compiling `pcc/__main__.py`:

```text
total_ms                         18604
compile_python_total             18604
compile_python_multi_total       18424
multi_codegen_layer1              9619
link_native                       4607
link_self_emit_objects_host       3073
link_self_object_emit_subprocess  3036
multi_type_infer                  1915
build_closed_world_context        1851
link_self_read_ll                 1426
```

This confirms the line-count cleanup is useful maintainability work, but it is
not a path to 30s by itself. The next meaningful performance work is still
object/IR volume reduction or batching self-backend object emission.

## Update 2026-05-14: 109-module bootstrap still dominated by Layer 1 and object emission

After the later `layer1.py` split and pcc1 CLI work, the full three-stage
self-bootstrap remains green but still runs at about one minute:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=17495
PCC_BOOTSTRAP_STAGE_RESULT stage=2 elapsed_ms=23618
PCC_BOOTSTRAP_STAGE_RESULT stage=3 elapsed_ms=23810

verify: cmp pcc2 pcc3
OK — pcc2 and pcc3 differ only by Mach-O code-signature metadata.
```

Focused pcc1 profile for compiling `pcc/__main__.py`:

```text
total_ms                         23470
compile_python_total             23470
compile_python_multi_total       23284
multi_codegen_layer1             13340
link_native                       5831
link_self_emit_objects_host       3389
link_self_object_emit_subprocess  3338
build_closed_world_context        2417
link_self_read_ll                 2286
multi_type_infer                  1095
libpython_scan                     118
link_self_cc                         81
emit_ll_many                         73
python_ir_pass_pipeline_many          1
```

Counters:

```text
multi_files                         109
multi_ir_bytes                 46088426
link_self_host_object_count         109
link_self_host_jobs                  12
link_self_host_input_bytes     46088535
link_self_host_input_max_bytes  2495039
link_self_host_emit_asm_sum_ms   12883
link_self_host_emit_asm_max_ms    1006
link_self_host_cc_sum_ms         19679
link_self_host_cc_max_ms           894
```

Interpretation:

- Stage 1 host compile is about `17.5s`; compiled pcc1/pcc2 stages are about
  `23.7s` each.
- The dominant single wall-clock bucket is still `multi_codegen_layer1`.
- The self-backend object path remains the second large bucket, with 109
  object jobs and about 46 MB of generated IR input.
- The per-worker sums show `cc -c` and `emit_self_asm` both matter, but the
  wall-clock bucket is already parallelized; small subprocess-startup cleanup
  will not reach a 30s full bootstrap.

The next optimization should be one of:

1. Reduce generated IR bytes or object count for the pcc self-host closure.
2. Batch object emission/assembly so 109 small object jobs do less duplicate
   setup work.
3. Profile `multi_codegen_layer1` at per-module/per-lowering granularity after
   the split, because the line-count reduction alone has not reduced codegen
   wall time enough.

## Gates at Time of Investigation

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 54.28s

tests/python/test_bootstrap_gate_baseline.py
tests/python/test_fallback_baseline.py
tests/python/test_ir_py_fallback_baseline.py
  13 passed, 4 skipped in 97.18s

tests/python/test_pcc1_gc_backend_matrix.py
  5 passed in 2.65s
```
