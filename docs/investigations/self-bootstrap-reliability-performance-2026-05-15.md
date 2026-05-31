# Investigation: self-bootstrap reliability and performance regression

## Status
resolved for the 80s regression; future <60s work remains codegen/IR-size work

## Problem Description

The mandatory self-bootstrap gate still passes, but it has drifted from roughly
55 seconds to 80+ seconds, and a stage3 crash was observed after the previous
Mach-O publish-race mitigation.

Latest observed gate:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 83.66s
```

Earlier same-session failure:

```text
stage1 elapsed_ms=14980
stage2 elapsed_ms=32191
stage3 pcc2 -> pcc3
Segmentation fault: 11
```

Crash report:

```text
pcc2-2026-05-15-061541.ips
EXC_BAD_ACCESS at 0x151854df0
py_decref
user_pcc_py_frontend_pipeline_compile_python
user_pcc_cli_bootstrap__observed_compile_python
user_pcc_cli_bootstrap_bootstrap_cli_main
```

## Reproduction

Primary gate:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self \
  -q -n0
```

The crash is non-deterministic. The same generated `pcc2` completed the stage3
command under LLDB and as a standalone command:

```bash
env -u LC_ALL -u LC_CTYPE PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  perl -e 'alarm shift; exec @ARGV' 120 \
  /Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc2 \
  --backend self --python-libpython off \
  /Users/jiamo/my/pcc/pcc/__main__.py \
  -o /Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc3.probe
```

## Current Evidence

- The gate can still pass, so the current tree is not deterministically broken.
- The stage3 crash happens after stage2 output exists and during `pcc2`
  execution.
- LLDB did not reproduce the crash, which is consistent with timing-sensitive
  cleanup or publish-boundary behavior.
- The crash report points to `py_decref` in the `compile_python` return path,
  so a generated cleanup/refcount bug remains plausible.
- The old Mach-O publish-race investigation remains relevant, but
  `codesign --verify` is no longer sufficient evidence that the class is fully
  closed.
- The first confirmed performance cause was generated safepoints emitted as a
  direct `pcc_thread_safepoint()` call on every loop/function gate. Replacing
  that with a load of exported `pcc_thread_stop_requested` and a slow-path call
  reduced the gate from `82.30s` to about `71s`.
- Further conditioning safepoint emission on explicit `PCC_WITH_THREADS=1`
  preserved the pcc1 threaded safepoint gate, but did not materially reduce the
  current bootstrap timing. The remaining `71s` needs a stage-level timing
  breakdown rather than more safepoint guessing.
- A later stage3 crash reproduced after the ZPage age-pressure work:
  `pcc2-2026-05-15-065841.ips` had the same
  `py_decref -> user_pcc_py_frontend_pipeline_compile_python` stack. The same
  `pcc2` binary then completed the exact standalone stage3 command, reinforcing
  the immediate-exec/publish-boundary hypothesis. The self-backend Darwin
  publish path now runs `/bin/sync` after `codesign --verify`; two consecutive
  full self-bootstrap runs passed after that change.
- A later passing run drifted to `83.66s`. The global `/bin/sync` publish
  barrier is a plausible variable-cost contributor because it flushes unrelated
  filesystem state. The publish path now defaults to a cheaper read-back
  barrier on the signed executable and keeps the global sync available through
  `PCC_SELF_BACKEND_PUBLISH_SYNC=1` for reliability bisects.

## Ranked Hypotheses

1. Generated cleanup/refcount path sometimes decrefs a stale or non-owned object
   after `compile_python` completes. Prediction: a conditional LLDB breakpoint
   or runtime debug gate around bad `py_decref` will stop before the stage3
   segfault on a failing run.
2. The self-backend publish path still has an immediate-exec race even after
   `codesign --verify`. Prediction: adding a stronger post-publish exec/read
   barrier changes the stage3 crash rate without changing generated code.
   Current evidence: adding `/bin/sync` after verify gave two consecutive
   passing full self-bootstrap runs, but this still needs longer ratchet data.
3. Recent safepoint or GC telemetry code added runtime cleanup overhead and
   pushed the gate from ~55s to 80s+. Prediction: stage-level timing will show
   the increase concentrated in generated-code execution or runtime archive
   calls, not in link/publish.
4. Codesign/verify and extra subprocess boundaries are a correctness fix but
   account for a meaningful fraction of the slowdown. Prediction: timing the
   publish path separately shows repeated signing/verification cost per stage.
5. The global `/bin/sync` barrier adds host-wide I/O variance. Prediction:
   replacing it with a target-file read-back barrier keeps the stage3
   immediate-exec crash closed while reducing bootstrap wall time variance.

## Next Steps

1. Add a focused timing report for stage1/stage2/stage3 that separates
   frontend pipeline, self-backend link, publish/sign/verify, and process
   startup where possible.
2. Re-run the self-bootstrap gate enough times to measure crash rate only after
   the timing loop exists; do not use blind reruns as proof of correctness.
3. If `py_decref` crash reproduces, catch it with LLDB/runtime debug at the bad
   pointer boundary and reduce to the generated cleanup site.
4. Optimize only after the reliability surface is understood; correctness gate
   stays `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`.

## Validation

Current validation after the observed crash:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 83.66s
```

Validation after generated safepoint fast-path work:

```text
tests/python/test_gc_threading_substrate.py::test_python_codegen_emits_thread_safepoint_at_loop_backedges_and_function_entry
tests/python/test_gc_threading_substrate.py::test_python_codegen_ir_contains_loop_and_entry_thread_safepoints
2 passed in 0.33s

tests/python/test_pcc1_threading_gc_runtime.py::test_pcc1_c_runtime_pure_compute_loop_safepoints_under_threaded_gc
2 passed in 8.31s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 71.24s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 58.61s
```

No newer `pcc2` crash report appeared after `pcc2-2026-05-15-062105.ips`
during these validation runs.

## Update: performance regression closure

The 80s+ regression is closed as of 2026-05-17. The remaining cost is no longer
the Darwin publish barrier or redundant self-backend IR file transport; it is
mostly pcc-compiled Layer1 codegen plus the closed-world export/type context.

Code changes:

- self-backend linking now defaults to passing module IR text directly instead
  of writing pipeline `.ll` files and reading them back;
- target-triple normalization scans only the IR header and multi-module linking
  no longer normalizes every module in the parent process before handing it to
  the host object emitter;
- native built-in module attr-store predeclaration no longer recursively scans
  function bodies, because function body lowering declares those globals when
  the functions are emitted.

Rejected optimization:

- exact-type field-name lookup using `type(obj)` was faster under CPython but
  broke pcc1 smoke with `__class__`; it was removed.

Validation:

```text
CPython cProfile emit-only:
before total profile time: 37.414s
after total profile time: 26.405s
before L1CodeGen.generate cumulative: 28.907s
after L1CodeGen.generate cumulative: 17.955s

scripts/bootstrap.sh --backend self --stage 3 --out-dir build/bootstrap-codex-profile-final
stage1 elapsed_ms=14304
stage2 elapsed_ms=26835
stage3 elapsed_ms=26763
profile total_wall_ms=67902

scripts/bootstrap.sh --backend self --stage 3 --out-dir build/bootstrap-codex-final-verify
stage1 elapsed_ms=14803
stage2 elapsed_ms=27424
stage3 elapsed_ms=27252
```

The short-term 60s target was not reached in this environment. The profile now
shows the next work clearly: shrinking the 109-module bootstrap IR closure or
optimizing pcc-compiled Layer1/class lowering. Removing `/bin/sync` or the
stage compile-smoke barrier is not a valid route for this target, because those
remain correctness barriers for the transient stage-boundary crash class.

Validation after adding `/bin/sync` to the Darwin self-backend publish path:

```text
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 58.99s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 64.15s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 65.51s
```

Code change after the `83.66s` observation:

```text
Darwin self-backend publish now signs and verifies the output, then defaults to
`cat "$out" >/dev/null` as a target-file stability barrier. Set
`PCC_SELF_BACKEND_PUBLISH_SYNC=1` to restore the previous global `/bin/sync`
barrier for reliability comparison.

scripts/bootstrap.sh now accepts `PCC_BOOTSTRAP_PROFILE_DIR=<dir>` and writes
stage-local profile JSON as `<dir>/stage1.json`, `<dir>/stage2.json`, and
`<dir>/stage3.json`. This keeps the mandatory gate unchanged while making the
next slow run attributable to frontend, IR pass, self-backend, link, codesign,
or publish-barrier phases.
```

Validation after switching the default publish barrier to target-file read-back
and enabling `PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc-bootstrap-profile`:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 63.80s
```

Top profile totals:

```text
stage1 compile_python_total=14865ms multi_codegen_layer1=7285ms link_native=4741ms
stage2 compile_python_total=23751ms multi_codegen_layer1=14235ms link_native=4497ms
stage3 compile_python_total=23605ms multi_codegen_layer1=14060ms link_native=4534ms
```

Current evidence points at stage2/stage3 `multi_codegen_layer1` and native
link/self-object emission as the dominant remaining cost, not the publish
barrier.

## Update: stage transition exec barrier

After the backend #4 small-page pooling work, the mandatory gate reproduced the
same failure shape:

```text
stage1 elapsed_ms=16836
stage2 elapsed_ms=26298
stage3 pcc2 -> pcc3
Segmentation fault: 11
```

The exact same `pcc2` binary then completed the standalone stage3 command:

```text
/Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc3.replay: replacing existing signature
exit 0
```

This again points at the stage transition / immediate-exec boundary rather than
a deterministic frontend compile failure. `scripts/bootstrap.sh` now runs a
lightweight Darwin self-backend stage exec barrier after each stage output:
`codesign --verify`, target-file read-back, and a short configurable delay
(`PCC_BOOTSTRAP_STAGE_EXEC_DELAY`, default `0.10`). This deliberately avoids
returning to the earlier global `/bin/sync` default while giving the next stage
binary a stable execution boundary.

Validation after adding the stage exec barrier:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 69.09s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 70.38s
```

The gate is back to passing, but the performance task remains open: this is
still above the short-term 60s target and must be addressed with profile data,
not by weakening the stage transition barrier.

## Update: profile report command and runtime mirror closure

The next No.43 slice adds a reusable report command instead of relying on
manual profile inspection:

```bash
env -u LC_ALL uv run python scripts/bootstrap_profile_report.py \
  /tmp/pcc-bootstrap-profile --top 8
```

`pcc/bootstrap_profile_report.py` reads `stage1.json`, `stage2.json`, and
`stage3.json` from `PCC_BOOTSTRAP_PROFILE_DIR`; the script can optionally read a
bootstrap log with `PCC_BOOTSTRAP_STAGE_RESULT ... elapsed_ms=...` lines to add
stage wall time. This gives later runs a stable text/JSON report for dominant
phase attribution.

During this validation run, stage1 initially failed before producing `pcc1`
because `libpy_runtime_pcc_py.a` could not rebuild:

```text
Layer 1 unknown function 'pcc_gc_backend'
py/py_obj_dealloc.py
```

Root cause: the pcc-Python deallocator mirror had gained backend-specific
freeing logic but was missing the `pcc_gc_backend` extern declaration. Adding
that extern restored the pcc-Python runtime archive:

```text
make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
success
```

Current validation:

```text
tests/python/test_bootstrap_profile_report.py
2 passed in 0.11s

tests/python/test_virtual_threads_gap.py
1 passed, 4 xfailed in 0.04s

tests/python/test_gc_backend4_production.py::test_backend4_genzgc_step_drains_selected_zpage_as_page_budget
1 passed in 5.01s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.85s
```

Profile summary from the passing self-bootstrap run:

```text
stages: 3
total_compiler_profile_ms: 71449
stage1 compile_python_total=16697ms
stage2 compile_python_total=27083ms
stage3 compile_python_total=27669ms

top phases:
compile_python_multi_total=71059ms
multi_codegen_layer1=40361ms
link_native=17515ms
link_self_emit_objects_host=10180ms
link_self_object_emit_subprocess=10113ms
build_closed_world_context=8159ms
link_self_read_ll=6782ms
```

The gate is again functional, but performance remains above the short-term
target. The measured bottleneck is still stage2/stage3 codegen plus
self-backend object emission/native link, not the publish barrier.

## Update: structured stage result artifacts

The timing loop no longer depends on pytest exposing bootstrap stdout. When
`PCC_BOOTSTRAP_PROFILE_DIR` is set, `scripts/bootstrap.sh` now writes two
additional files per stage:

```text
stageN.result.json
stageN.time
```

`stageN.result.json` records stage wall time, compile wall time, child user/sys
time, publish-barrier time, return code, backend, and output path. The report
command merges these files with the compiler profile JSON and now prints:

```text
total_compile_wall_ms
total_compile_user_ms
total_compile_sys_ms
total_publish_barrier_ms
total_unprofiled_wall_ms
```

This separates compiler-profiled work from publish/stage boundary overhead and
any remaining unprofiled gap.

Validation:

```text
bash -n scripts/bootstrap.sh
success

tests/python/test_bootstrap_profile_report.py
2 passed in 0.10s
```

Stage1-only smoke with structured artifacts:

```text
PCC_BOOTSTRAP_PROFILE_DIR=/tmp/pcc-bootstrap-profile-stage1-codex
PCC_BOOTSTRAP_OUT_DIR=/tmp/pcc-bootstrap-out-stage1-codex
scripts/bootstrap.sh --stage 1 --backend self

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=17449 output=/tmp/pcc-bootstrap-out-stage1-codex/pcc1
```

Report from that smoke:

```text
total_wall_ms: 17449
total_compile_wall_ms: 17154
total_compile_user_ms: 36000
total_compile_sys_ms: 4072
total_publish_barrier_ms: 212
total_unprofiled_wall_ms: 727

top phases:
compile_python_total: 16722 ms
compile_python_multi_total: 16570 ms
multi_codegen_layer1: 7708 ms
link_native: 6070 ms
link_self_emit_objects_host: 3453 ms
link_self_object_emit_subprocess: 3415 ms
link_self_read_ll: 2461 ms
build_closed_world_context: 1772 ms
```

The short-term performance target is still open. The new artifacts make the
next full run attributable without weakening stage transition barriers.

## Update: signed-temp publish and exec-smoke barrier

The structured timing loop immediately caught two more transient crashes:

```text
stage2 pcc1 -> pcc2
returncode=139
wall_ms=392
compile_wall_ms=348

stage3 pcc2 -> pcc3
returncode=139
wall_ms=357
compile_wall_ms=<subsecond>
```

In both cases, the same just-built stage binary later completed the equivalent
standalone command. The new report command also learned to include
`stageN.result.json` records even when the failed stage never produced a
compiler profile JSON, so these subsecond failures are now visible in the
stage table instead of being dropped from the report.

The Darwin self-backend publish path now signs and verifies the temporary
executable before moving it into the final output path. This prevents the final
path from ever naming an unsigned executable. The final path is still verified
after the move and read back before the stage is considered published.

That was not sufficient by itself: a later run still crashed on the first
stage3 execution of `pcc2`, with crash report:

```text
pcc2-2026-05-15-184938.ips
py_decref
user_pcc_py_frontend_pipeline_compile_python
user_pcc_cli_bootstrap__observed_compile_python
user_pcc_cli_bootstrap_bootstrap_cli_main
```

`scripts/bootstrap.sh` therefore adds a stage exec-smoke barrier for Darwin
self-backend outputs:

```text
codesign --verify "$out"
cat "$out" >/dev/null
sleep "$PCC_BOOTSTRAP_STAGE_EXEC_DELAY"
"$out" --help >/dev/null 2>&1
```

This deliberately keeps the first load/exec of each freshly published stage
inside the stage boundary instead of letting the next compile stage be the
first execution. `stageN.result.json` records
`publish_barrier_returncode` so a future barrier failure is distinguishable
from a compiler-stage crash.

Validation after the exec-smoke barrier:

```text
tests/python/test_self_backend_publish_policy.py
2 passed in 0.16s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 80.96s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 78.03s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.42s
```

Profile summary from the second passing run:

```text
total_wall_ms: 77271
total_compile_wall_ms: 75947
total_compile_user_ms: 127142
total_compile_sys_ms: 13275
total_publish_barrier_ms: 1253
total_unprofiled_wall_ms: 1968
total_compiler_profile_ms: 75303

top phases:
compile_python_total: 75303 ms
compile_python_multi_total: 74911 ms
multi_codegen_layer1: 42715 ms
link_native: 18776 ms
link_self_emit_objects_host: 10974 ms
link_self_object_emit_subprocess: 10884 ms
build_closed_world_context: 8317 ms
link_self_read_ll: 7127 ms
```

No newer pcc2 crash report appeared after `pcc2-2026-05-15-184938.ips` during
the two exec-smoke validation runs. Reliability is improved, but the
performance target is still open: the correctness barrier costs about 1.25s
across three stages, while the dominant remaining cost is still generated
codegen plus self-backend object emission/native link.

## Update: backend #0 frame-root fast path

The No.42 Phase 2 root-hook work briefly made backend #0 register every
generated-code active frame by default. That preserved frame-root
observability, but self-bootstrap exposed the cost immediately:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
FAILED after 600s timeout

sample / build/bootstrap-pytest-self/pcc1
top of stack: pcc_gc_note_frame_leave
```

The sampled stage1 process was still compiling `pcc1 -> pcc2` and spent nearly
all samples in `pcc_gc_note_frame_leave`. This was not a scheduler-progress
problem; it was malloc/free/graph-lock overhead from dynamic active-frame root
registration in the default refcount backend.

The fix restores backend #0 generated-code frame roots to a default fast no-op
path. Tests that need backend #0 frame-root observability call
`pcc_gc_set_backend(0)`, which now explicitly enables backend #0 frame tracking
for that process. Non-refcount backends still track active frame roots by
default, and No.42 continuation suspended-root hooks remain available through
their separate registration API.

Validation after the fast path:

```text
tests/python/test_virtual_threads_gap.py tests/python/test_gc_coroutine_scheduler_roots_production.py
5 passed, 3 xfailed in 13.15s

tests/python/test_gc_coroutine_roots.py tests/python/test_gc_root_precision.py
7 passed in 73.33s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 72.01s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 63.94s
```

One immediate post-timeout bootstrap run failed stage2 with exit 139 in 18s,
but the same `pcc1 -> pcc2` command succeeded standalone and a full gate rerun
passed. This matches the existing transient stage-boundary crash class, not the
frame-root performance regression.

## Update: Phase 4 scheduler validation reproduced stage3 transient

After the No.42 Phase 4 cooperative virtual-thread scheduler slice, the full
self-bootstrap gate again reproduced the same transient class:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
FAILED exit 139

stage1 pcc1 elapsed_ms=18161
stage2 pcc2 elapsed_ms=27362
stage3 pcc3 elapsed_ms=66
```

The same freshly generated `pcc2` then completed the exact stage3 compile under
LLDB and as a plain standalone command:

```text
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 120 \
  build/bootstrap-pytest-self/pcc2 --ir-scaffold=on --python-libpython=off \
  --backend self pcc/__main__.py -o /tmp/pcc3_repro_phase4_plain

exit 0
```

The full pytest gate then passed on rerun:

```text
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 73.50s
```

This keeps the issue classified as the existing stage-boundary / immediate-exec
transient, not a deterministic regression from the virtual-thread scheduler
object. No.43 remains open: the crash-rate loop still needs to localize the
`py_decref -> user_pcc_py_frontend_pipeline_compile_python` return-path crash
to a repairable root cause.

## Update 2026-05-16: crash-rate loop and reliability closure

No.43 now has a reusable crash-rate gate. `scripts/bootstrap_crash_rate.py`
repeatedly runs `scripts/bootstrap.sh` with per-run output, profile, and log
directories, then writes a `summary.json` artifact using schema
`pcc.bootstrap_crash_rate.v1`. `tests/python/test_bootstrap_crash_rate.py`
covers dry-run summary behavior so the gate format is regression-tested.

The first debug-runtime crash-rate runs converted the previous transient class
into two deterministic repair points:

- Dynamic `DynType.clear()` could dispatch a dict to `py_list_clear()` because
  `clear` was missing from the dynamic dict native-method table.
  `py_obj_clear()` now dispatches list and dict by runtime tag, and dynamic
  `.clear()` lowering uses that helper.
- Split string accessors allocated `PyStrObject` through `malloc`, so stage2
  debug release checks saw valid large IR strings as untracked stack-looking
  addresses. Both C and pcc-Python `py_str_accessors` now allocate string
  objects through `pcc_gc_alloc()`. The debug checker also accepts valid
  untracked `PyObject` headers when the exact-size allocation table misses
  under high allocation load.

Validation:

```text
scripts/bootstrap_crash_rate.py --runs 2 --debug-runtime --out-root /tmp/pcc-bootstrap-crash-rate-codex-debugcheck-fix --timeout 900
runs=2 passes=2 failures=0 failure_rate=0.000

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 75.61s

tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
17 passed in 64.86s

tests/python/test_bootstrap_crash_rate.py
1 passed
```

This is enough to close the stage-boundary transient segfault as a reliability
task: it is no longer closed by a single rerun, but by a repeatable crash-rate
loop plus localized fixes. The performance side remains a follow-up: wall time
is still above the short-term 60s target, dominated by layer1 codegen and
self-backend object/native link work.

## Update 2026-05-16: read-back barrier was still too weak

Phase 6 virtual-thread comparison validation reproduced the same stage-boundary
class after the earlier reliability closure. A normal full self-bootstrap
failed in stage2 immediately after stage1 published `pcc1`:

```text
stage1 elapsed_ms=18077 output=build/bootstrap-pytest-self/pcc1
stage2 elapsed_ms=63 output=build/bootstrap-pytest-self/pcc2
Segmentation fault: 11
```

The just-published `pcc1` passed `--help`, and replaying the same stage2
compile command against the same binary completed successfully:

```text
build/bootstrap-pytest-self/pcc1 --ir-scaffold=on --python-libpython=off \
  --backend self pcc/__main__.py -o /tmp/pcc2_repro_phase6_plain
exit 0
```

That falsifies a deterministic Phase 6/runtime change and re-points the failure
at the publish/immediate-exec boundary. Enabling the old stronger publish sync
validated the hypothesis:

```text
PCC_SELF_BACKEND_PUBLISH_SYNC=1 \
tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 75.07s
```

The Darwin self-backend publish path now defaults back to `/bin/sync` after
final `codesign --verify`. The environment variable remains, but the polarity is
correctness-first: unset or `1` uses `/bin/sync`; `PCC_SELF_BACKEND_PUBLISH_SYNC=0`
or `off` opts into the lighter read-back barrier for performance experiments.

A later rerun showed `/bin/sync` plus the existing `--help` smoke was still not
sufficient. Stage3 crashed in `56ms` with the same
`py_decref -> user_pcc_py_frontend_pipeline_compile_python` stack, while the
same `pcc2 --help` and standalone stage3 compile replay both passed. The stage
barrier now also runs a same-mode minimal Python compile:

```text
PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py \
  <stage-binary> --ir-scaffold=on --backend self --python-libpython off \
  /tmp-or-build-smoke/smoke.py -o /tmp-or-build-smoke/smoke
```

That moves the first heavy execution of a freshly published stage binary into
the publish barrier itself, with failures recorded through the existing
`publish_barrier_returncode` path.

Validation after changing the default:

```text
tests/python/test_self_backend_publish_policy.py tests/python/test_virtual_thread_comparison_report.py
6 passed in 0.23s

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
1 passed in 65.20s

scripts/bootstrap_crash_rate.py --runs 2 --timeout 900 --out-root /tmp/pcc-bootstrap-crash-rate-compile-smoke
runs=2 passes=2 failures=0 failure_rate=0.000
```

This reopens the earlier conclusion that target-file read-back was sufficient.
For now, correctness requires the global sync barrier plus the same-mode compile
smoke on Darwin self-backend publish. The performance task remains open and
should look for compile/codegen speedups, not remove this barrier without a
stronger replacement.
