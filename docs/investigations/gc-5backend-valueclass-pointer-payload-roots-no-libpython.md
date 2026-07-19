# Investigation: valueclass pointer payload roots under backend #4 relocation

## Status
active

## Problem Description
The `G-P1-GC` common contract added
`tests/python/gc_production_contract/test_valueclass_pointer_payload.py`, which
boxes a `@pcc.valueclass` carrying pointer fields, forces backend #4 to
relocate the `ValueBox`, then mutates and reads pointer payload fields through
the Python object path.

The initial current-state evidence said backend #0 passed while backends #1..#4
raised `AttributeError: items`. After the first two narrowing fixes in this
session, the failure reduced to backend #4 only: the program returned zero but
printed only the relocation prologue, not the five payload-readback lines from
`check_payload`.

## Repro

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 480 \
  uv run pytest tests/python/gc_production_contract/test_valueclass_pointer_payload.py -q -n0
```

Observed before the root fix in this investigation:

```text
....F
backend #4 stdout:
relocated
True
True
True
True
True
```

Expected backend #4 to continue with:

```text
4
8
bag
2
tail
```

## Test [CONFIRMED]

The focused test above is the gating regression. It compiles the same strict
no-libpython self-backed program once, then runs it under `PCC_GC_BACKEND=0..4`.

After the root fix, the focused gate passed:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 480 \
  uv run pytest tests/python/gc_production_contract/test_valueclass_pointer_payload.py -q -n0
# 5 passed in 0.97s
```

## Proposals

- No.1 Resolve forwarded instance/valuebox pointers in runtime accessors     [REJECTED as implemented]
- No.2 Do not speculatively execute dynamic valueclass getattr fallback     [CONFIRMED partial]
- No.3 Root borrowed user-function object parameters as GC frame slots     [CONFIRMED]
- No.4 Treat stale runtime archives as a test/build invalidation failure     [CONFIRMED]
- No.5 Preserve pcc-Python runtime raw-pointer/new-reference ownership     [CONFIRMED]
- No.6 Match cross-module `-> None` extern function ABI to same-module declarations     [CONFIRMED]
- No.7 Suppress implicit GC roots/retains in pcc-Python runtime-library primitives     [CONFIRMED]
- No.8 Rebuild pcc-emitted runtime archives when compiler sources are newer     [CONFIRMED]
- No.9 Root-cause remaining 5-GC full bootstrap matrix failures     [active]
- No.10 Audit apparent bootstrap hangs against recent GC indexing/rooting changes     [active]
- No.11 Pin owned nested string `BinOp` temporaries across allocating runtime calls     [active]
- No.12 Backend #3 direct pcc1->pcc2 hang audit: safepoint root scan and frame-leave cost     [active]
- No.13 Rewrite direct slots for unsupported minor-arena owners during backend #3 promotion     [active]
- No.14 backend #3 pcc1->pcc2 follow-up: ownership holes fixed; bootstrap still blocked on tracking/index/root cost     [active]
- No.15 Backend #3/#4 focused root-slot fast path verification     [CONFIRMED focused]
- No.16 Backend #3 full bootstrap correctness restored, 60s still open     [CONFIRMED]
- No.17 Backend #4 forwarding-target lookup index     [CONFIRMED focused]
- No.18 Direct valueclass payload locals root pointer fields     [CONFIRMED focused]
- No.19 Closure-captured direct valueclass payload crosses object ABI boundary     [CONFIRMED focused]
- No.20 Tuple-unpacked direct valueclass payload targets need payload storage roots     [CONFIRMED focused]
- No.21 For-loop direct valueclass payload targets need object-to-payload conversion     [CONFIRMED focused]
- No.22 Comprehension direct valueclass payload targets need nested attr type recovery and roots     [CONFIRMED focused]
- No.23 Set/dict comprehension direct valueclass payload targets share the indexed comprehension root path     [CONFIRMED focused]
- No.24 List/tuple subscript direct valueclass payload targets preserve payload roots     [CONFIRMED focused]
- No.25 Bool-op direct valueclass payload targets preserve payload roots     [CONFIRMED focused]
- No.26 Module-global direct valueclass payload targets need module storage and field roots     [CONFIRMED focused]
- No.27 Module-global direct valueclass payload roots need overwrite and teardown ownership     [CONFIRMED focused]
- No.28 Boxed list-setitem valueclass constructor fields need ownership transfer     [CONFIRMED focused]
- No.29 Covered ValueBox object-boundary constructor fields need ownership proof and attr temp release     [CONFIRMED focused]

## No.1 Resolve forwarded instance/valuebox pointers in runtime accessors

### Code Change

Initial patch added a `_resolve_instance()` helper and routed
`py_instance_get_field`, `py_instance_set_field`,
`py_instance_getattr_default`, `py_instance_getattr`,
`py_instance_setattr`, and `py_instance_delattr` through it before reading the
header or class slot.

### REJECTED as implemented

This was the wrong abstraction for the pcc-Python runtime mirror. Returning an
existing instance pointer from `_resolve_instance()` used normal object-return
ownership and emitted `pcc_gc_retain` on a borrowed receiver. LLDB showed
`user_py_class__resolve_instance -> pcc_gc_retain` on `py_instance_set_field`
and `py_instance_setattr`, inflating backend #0 cycle members' refcounts so
`gc.collect()` reported zero collected and finalizers never ran. The helper was
removed; backend #4 is protected by updateable frame roots instead.

## No.2 Do not speculatively execute dynamic valueclass getattr fallback

### Code Change

`_maybe_emit_valueclass_payload_attr_from_dyn()` previously emitted
`py_obj_getattr(box, "field")` unconditionally and then selected between the
valuebox field and the fallback result. That was wrong because the fallback has
exception side effects. It now branches on the valueclass match and calls
`py_obj_getattr` only in the fallback block.

### CONFIRMED partial

This removed the misleading `AttributeError: extra/items` side effect and
exposed the real backend #4 root/update failure. It did not make the payload
contract pass by itself: backend #4 still returned early with no readback
output after the first `gc.collect()`.

## No.3 Root borrowed object parameters as GC frame slots

### Code Change

Object parameters in user functions are now registered as borrowed GC frame
roots. The slots are updateable by backend #4 relocation but do not change
ownership: there is no extra release on cleanup. The existing owned-local
frame-root helper was split so borrowed locals can share the
`pcc_gc_frame_enter` / `pcc_gc_frame_leave` mechanics without pretending to own
the object.

The cleanup path now also leaves non-owned frame roots on all paths. Pinned
temporary roots are tracked separately so only those roots run
`pcc_gc_unpin`/`pcc_gc_store_root(..., NULL)` cleanup.

### CONFIRMED

The decisive substitution was to change only backend #4's final call from
`check_payload(box)` to `check_payload(loaded)`, where `loaded` is the object
read back through `pcc_gc_load_ptr()` from a registered scheduler root slot.
That temporary probe printed the full expected payload readback:

```text
relocated
True
True
True
True
True
4
8
bag
2
tail
```

This proves the valuebox relocation copy retained the payload. The broken path
was the stale function parameter slot: `check_payload` stored the old forwarded
source in `%box.addr`, then `gc.collect()` eventually cleared the forwarding
source, so later field access saw a stale pointer and returned through
`err.exit`.

After rooting object parameters, generated IR for
`user_valueclass_pointer_payload_check_payload(ptr %box)` includes
`pcc_gc_frame_enter` on `%box.addr` and matching `pcc_gc_frame_leave` exits, and
the focused five-backend contract passes.

The first broad version also rooted class-method receivers/args. A causal slice
removed those class-method parameter roots: `test_finalizer_cycle[0]` still
failed and `test_valueclass_pointer_payload.py` still passed 5/5, proving method
receiver/arg roots were not required for this bug and not the backend #0
regression cause.

## No.4 Treat stale runtime archives as a test/build invalidation failure

### Code Change

The shared pcc1 runtime fixture now reuses `libpy_runtime_pcc_py.a` only when
`pipeline._runtime_archive_stale(...)` says it is current. The runtime substrate
tests also cover the `PCC_RUNTIME_CC=pcc` / `PCC_RUNTIME_HIGH=c` path: if
`libpy_runtime_pcc.a` is older than a runtime C source such as `src/py_class.c`,
`_ensure_runtime()` must rebuild the `libpy_runtime_pcc.a` target.

### CONFIRMED

The stale-archive issue was a real test/build-system gap, not the semantic root
of the backend #4 valueclass payload failure. It mattered because a stale
archive can hide or fabricate runtime conclusions after a C runtime edit.

Confirmed regression:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 120 \
  uv run pytest \
  tests/python/test_runtime_substrate_spike.py::test_pcc_c_archive_staleness_tracks_runtime_c_sources \
  tests/python/test_runtime_substrate_spike.py::test_pcc_c_archive_staleness_rebuilds_runtime_archive \
  tests/python/test_runtime_substrate_spike.py::test_no_libpython_pcc_python_archive_staleness_ignores_libpython_bridge \
  -q -n0
# 3 passed
```

## No.5 Preserve pcc-Python runtime raw-pointer/new-reference ownership

### Code Change

The pcc-Python `py_class.py` mirror now avoids the borrowed-pointer
`_resolve_instance()` helper and returns freshly allocated instance/valuebox
pointers as raw pointer expressions (`ptr_add(x, 0)`) so return lowering does
not insert an extra `pcc_gc_retain`. The C mirror was kept in the same
no-helper shape.

### CONFIRMED

After removing `_resolve_instance()`, backend #0 still failed
`test_finalizer_cycle[0]`. Runtime logging showed each cycle member still had
refcount 2 after `a = None; b = None`: one internal cycle edge plus one leaked
return retain from `py_instance_new`. After returning instance/valuebox
allocations through raw pointer expressions, the focused backend #0 finalizer
case passed and the backend #4 valueclass payload case remained green:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 240 \
  uv run pytest \
  'tests/python/gc_production_contract/test_finalizer_cycle.py::test_cycle_member_finalizers_run[0]' \
  -q -n0
# 1 passed in 0.73s

env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 480 \
  uv run pytest tests/python/gc_production_contract/test_valueclass_pointer_payload.py -q -n0
# 5 passed in 1.12s
```

The seven backend #0 nodes that failed in the first full contract run also pass
after the ownership fix:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest \
  'tests/python/gc_production_contract/test_finalizer_cycle.py::test_cycle_member_finalizers_run[0]' \
  'tests/python/gc_production_contract/test_finalizer_resurrection.py::test_resurrected_object_survives_gc[0]' \
  'tests/python/gc_production_contract/test_gc_collect_reentrancy.py::test_gc_collect_reentrancy_is_safe[0-plain_reentrant]' \
  'tests/python/gc_production_contract/test_gc_collect_reentrancy.py::test_gc_collect_reentrancy_is_safe[0-cycle_reentrant]' \
  'tests/python/gc_production_contract/test_mixed_reachability.py::test_collection_boundary_and_scale[0-mixed_boundary]' \
  'tests/python/gc_production_contract/test_weakref_callback.py::test_weakref_callback_fires_on_collection[0]' \
  'tests/python/gc_production_contract/test_weakref_finalizer.py::test_weakref_finalizer_contract[0]' \
  -q -n0
# 7 passed in 5.68s
```

The full common production contract is now green across all five backends:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 900 \
  uv run pytest tests/python/gc_production_contract -q -n0
# 115 passed in 23.25s
```

The runtime archive staleness checks and focused GC root set also pass after the
runtime ownership fix:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 420 \
  uv run pytest \
  tests/python/test_runtime_substrate_spike.py::test_pcc_c_archive_staleness_tracks_runtime_c_sources \
  tests/python/test_runtime_substrate_spike.py::test_pcc_c_archive_staleness_rebuilds_runtime_archive \
  tests/python/test_runtime_substrate_spike.py::test_no_libpython_pcc_python_archive_staleness_ignores_libpython_bridge \
  tests/python/gc_production_contract/test_exception_roots.py \
  tests/python/gc_production_contract/test_valuebox_roots.py \
  tests/python/gc_production_contract/test_valueclass_pointer_payload.py \
  -q -n0
# 18 passed in 2.70s
```

Fallback baselines also pass:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest tests/python/test_fallback_baseline.py \
  tests/python/test_ir_py_fallback_baseline.py -q -n0
# 18 passed in 103.32s (0:01:43)
```

## Update 2026-06-01 — 5-GC full bootstrap matrix exposes a cross-module `None` ABI bug

After parameterizing
`tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
over `PCC_GC_BACKEND=0..4`, the full matrix result was:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 4200 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py -q -n0
# gc0 passed; gc1/gc2/gc3 failed with Trace/BPT trap during stage1 smoke;
# gc4 timed out during the same stage1 smoke window.
```

Focused `gc1` smoke repro using the generated `pcc1` printed:

```text
[BAD_INCREF] o=... tag=-1
```

LLDB stopped at:

```text
pcc_debug_bad_incref
py_decref
pcc_gc_release
user_pcc_py_frontend_codegen_hoist_lowering_HoistLoweringMixin__hoist_nested_funcdefs
```

`PCC_DEBUG_HOIST=1` showed the crash happens after
`[pcc.hoist] smoke:module skip`, i.e. in the early-return cleanup path, not in
real nested-function hoisting.

The decisive IR audit:

```text
define external void @user_pcc_py_frontend_codegen_hoist_analysis_write_hoist_profile(i1 %enabled, ptr %path, ptr %stats)
declare external ptr @user_pcc_py_frontend_codegen_hoist_analysis_write_hoist_profile(i1, ptr, ptr)
%write_hoist_profile_ret = call ptr (...) @user_pcc_py_frontend_codegen_hoist_analysis_write_hoist_profile(...)
call void @pcc_gc_release(ptr %write_hoist_profile_ret)
```

So the failure is a generic cross-module native function ABI mismatch:
same-module `-> None` functions lower to `void`, but native sibling extern
declarations decode `("none",)` through `_abi_ir_type` / `_map_type`, producing
`PyObject*`. The caller then releases a garbage return register. Backend #0 did
not expose this, but the non-reference backends make it fatal. This is not a GC
semantic to weaken; the extern declaration ABI must match the callee.

## No.6 Match cross-module `-> None` extern function ABI to same-module declarations

### Code Change

Update native-module extern function declaration lowering so export metadata
with `return_ty == ("none",)` maps to LLVM `void`, matching
`_declare_user_function` for same-module `-> None` functions. Add a focused
multi-file IR regression proving an imported sibling `-> None` function is
declared and called as `void`, and that no `pcc_gc_release` is emitted for a
fake pointer return.

### CONFIRMED

Focused IR regression and pcc1 stage-smoke evidence are green:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 180 \
  uv run pytest tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_cross_module_none_return_extern_uses_void_abi -q -n0
# 1 passed

env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 300 \
  uv run pytest \
    tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_cross_module_function_call \
    tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_cross_module_function_with_args \
    tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_cross_module_none_return_extern_uses_void_abi \
    -q -n0
# 3 passed
```

`scripts/bootstrap.sh --backend self --stage 1` plus its built-in smoke compile
now passes under `PCC_GC_BACKEND=1`, `2`, `3`, and `4`.

### Full matrix after the ABI fix

The first bug was real but not sufficient for the full gate. After the fix:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 4200 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py -q -n0
# 4 failed, 1 passed in 1895.45s
```

Result split:

- `gc0`: passed.
- `gc1`, `gc2`, `gc4`: timed out at the test's 600s per-backend subprocess
  timeout during the full bootstrap.
- `gc3`: stage1 and stage2 completed, then stage3 failed with exit 133 and
  `[BAD_INCREF]` while pcc2 compiled pcc3.

Direct `gc1` full bootstrap with a 1200s outer timeout showed that this is not
the old stage1 smoke trap and not merely pytest capture:

```bash
PCC_GC_BACKEND=1 bash scripts/bootstrap.sh \
  --backend self --out-dir build/bootstrap-pytest-self-gc1 --stage 3
# stage1 passed; stage2 pcc1 compile stayed at 100% CPU beyond 1200s.
```

A 3s macOS `sample` of the hot `pcc1` process put the stack under:

```text
pipeline._build_python_frontend_shared_exports_parallel
pipeline._write_native_exports_wire
pipeline._native_export_to_wire
pcc_gc_note_frame_leave
```

So there are now two distinct remaining full-bootstrap failures:

1. `gc1/gc2/gc4` stage2 long-run or non-termination in native export-table
   wire serialization / GC frame-leave overhead.
2. `gc3` stage3 `BAD_INCREF` after pcc2 exists.

Follow-up LLDB on a `PCC_DEBUG_RELEASES=1` `gc3` repro stopped at:

```text
pcc_debug_bad_incref
py_decref
py_dealloc_list
user_py_obj_dealloc__dealloc_dispatch
pcc_dealloc_with_trash
py_decref
pcc_gc_release
user_pcc_py_frontend_pipeline__package_import_targets
user_pcc_py_frontend_pipeline__collect_multi_source_relative_closure
user_pcc_py_frontend_pipeline_compile_python_multi
```

With debug releases enabled the crash happens while `pcc1` compiles pcc2; in
the non-debug matrix run, `gc3` reached pcc2 and crashed in the next compile.
Either way, this is a real list-element lifetime/ownership failure in the
package import target collection path, not the earlier `write_hoist_profile`
`None` ABI mismatch.

### Boundary

The `None -> void` ABI bug is fixed and regression-covered, but it did not
complete the full five-GC bootstrap gate. The remaining full-bootstrap failures
are separate bugs and must be tracked independently; do not raise the timeout
or weaken GC/runtime behavior and declare this complete.

## No.7 Suppress implicit GC roots/retains in pcc-Python runtime-library primitives

### Code Change

When `compile_python(..., python_library=True)` compiles sources under
`pcc/py_runtime/py/`, codegen sets a runtime-library suppression flag. User
function lowering then skips automatic borrowed-parameter frame roots under
that flag, and return lowering skips borrowed-return retains under the same
flag.

This is intentionally scoped to the pcc-Python runtime library. Compiler/user
modules still use the normal implicit borrowed-parameter root policy added for
the backend #4 relocation/updateable-slot bug.

### CONFIRMED

The bad interaction was inside the runtime substrate itself, not in ordinary
compiled Python. The pcc-Python runtime primitives implement the GC barriers
and retain operations; injecting those same operations into the primitive
definitions creates recursion. The observed failing archive contained:

```text
_ptr_can_have_header -> pcc_gc_frame_enter
pcc_gc_retain return lowering -> pcc_gc_retain
```

Comparing the C-runtime archive against the pcc-Python runtime archive showed
the C runtime passed while the pcc-Python archive failed, narrowing the cause
to runtime-library codegen rather than the valueclass payload program.

Focused regression:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 900 \
  uv run pytest \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_obj_runtime_refcount_primitives_do_not_self_root \
  tests/python/gc_production_contract/test_valueclass_pointer_payload.py \
  -q -n0
# included in the focused 11-test gate: passed
```

The regression asserts that `pcc_gc_note_frame_enter`'s body does not call
`@pcc_gc_frame_enter`, that `pcc_gc_retain` does not call itself, and that
`_ptr_can_have_header` does not self-root.

## No.8 Rebuild pcc-emitted runtime archives when compiler sources are newer

### Code Change

Runtime archive staleness now considers compiler/codegen sources for
pcc-emitted runtime archives, not only runtime C/high-level sources. When an
archive is stale, `_ensure_runtime()` invokes the runtime build with `make -B`
so the stale decision from pcc is not discarded by a Makefile that does not
model compiler source dependencies.

The helper was written using pcc1-supported filesystem operations
(`os.listdir`, `os.path.isdir`, `os.path.isfile`, `os.path.getmtime`, and
explicit string suffix checks), because an earlier `os.walk` / tuple
`endswith(...)` version reintroduced no-libpython fallback in the compiled
stage.

### CONFIRMED

The build gap was user-visible: `libpy_runtime_pcc.a` /
`libpy_runtime_pcc_py.a` can be older than the compiler source that emits them,
and a normal make invocation can still report the archive up to date. That
invalidates runtime conclusions after a codegen/runtime-source edit.

Focused regression:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 900 \
  uv run pytest \
  tests/python/test_runtime_substrate_spike.py::test_pcc_emitted_archive_staleness_tracks_compiler_sources \
  tests/python/test_runtime_substrate_spike.py::test_pcc_c_archive_staleness_rebuilds_runtime_archive \
  -q -n0
# included in the focused 11-test gate: passed
```

The C-archive rebuild test also asserts that the stale path uses `-B`.

## No.9 Root-cause remaining 5-GC full bootstrap matrix failures

### Code Change

In progress. The current worktree contains a diagnostic/performance patch that
keeps the existing forwarding and identity linked lists but adds O(1) pointer
indexes for lookup/removal in both the C runtime and the pcc-Python runtime
mirror. It also gates relocation-forwarding table probes so ordinary known
objects with no relocation-candidate flag do not linearly scan the forwarding
list on every oldification/root resolution/read-barrier path.

This is not yet a confirmed root fix. It changes the failure mode from a
stage2 long-run dominated by forwarding/identity scans to a quicker
`[BAD_INCREF]`, which is useful evidence but not completion.

### active

Current test shape proof:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 120 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py --collect-only -q
# collected exactly:
# test_full_three_stage_bootstrap_self[gc0]
# test_full_three_stage_bootstrap_self[gc1]
# test_full_three_stage_bootstrap_self[gc2]
# test_full_three_stage_bootstrap_self[gc3]
# test_full_three_stage_bootstrap_self[gc4]
```

Current full matrix:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 4200 \
  uv run pytest tests/python/test_pcc_bootstrap_full.py -q -n0
# 1 passed, 4 failed in 1875.94s
```

Result split:

- `gc0`: passed.
- `gc1`, `gc2`, `gc4`: built `pcc1`, then hit the test's 600s per-backend
  subprocess timeout.
- `gc3`: failed with exit 133 / `[BAD_INCREF]` during stage2 (`pcc1 -> pcc2`).

This is the current open gate. It is not a reason to disable challenger
backends, raise the timeout as a success substitute, or weaken GC/runtime
semantics.

### Causal audit update

Recent frontend rooting changes are still in the causal window and must be
audited before any claim that the GC backends are independently broken. One
direct substitution removed the broad borrowed-object local GC-root branch in
`assignment_statement_lowering.py` and rebuilt a `gc3` pcc1. That was
disproven as a valid fix: the stage2 compile failed quickly with
`[BAD_INCREF] o=... tag=-1`. Therefore the borrowed-local root branch is not
an arbitrary workaround to remove; it is currently protecting a real stale
pointer/UAF shape, even though it also exposes backend #3 performance pressure.

The first forwarding fast path was also disproven as written. It skipped
forwarding lookup unless the object still carried the relocation-candidate
flag, but backend #4's valueclass pointer-payload contract then failed because
relocation-set removal/reset cleared that flag after installing forwarding.
The fix was to preserve the flag on forwarded sources, then use a two-stage
known/unknown-object rule so raw or invalid frame-root values do not have their
headers read before a pure forwarding-table probe. Focused verification after
that correction:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 600 \
  uv run pytest \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_borrowed_object_local_rebind_keeps_gc_root \
  tests/python/gc_production_contract/test_valueclass_pointer_payload.py \
  -q -n0
# 6 passed in 23.60s
```

Adding forwarding/identity pointer indexes then kept the focused runtime and
GC contracts green:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 900 \
  uv run pytest \
  tests/python/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_borrowed_object_local_rebind_keeps_gc_root \
  tests/python/gc_production_contract/test_valueclass_pointer_payload.py \
  -q -n0
# 7 passed in 24.13s
```

The `gc3` direct stage2 compile with an indexed pcc1 still fails:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 300 \
  env PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py PCC_PYTHON_IR_PASSES=off \
  PCC_PY_FRONTEND_JOBS=1 PCC_GC_BACKEND=3 \
  build/bootstrap-forwarding-index-gc3/pcc1 \
  --backend self --python-libpython off pcc/__main__.py \
  -o build/bootstrap-forwarding-index-gc3/pcc2.probe
# failed in about 33s with [BAD_INCREF] o=... tag=-1
```

LLDB stopped at:

```text
pcc_debug_bad_incref
py_decref
py_dealloc_list
user_py_obj_dealloc__dealloc_dispatch
pcc_dealloc_with_trash
py_decref
pcc_gc_release
user_pcc_parse_py_lift__Lifter__e_Str
user_pcc_parse_py_lift__Lifter_lift_expr
user_pcc_parse_py_lift__Lifter__e_Ternary
user_pcc_parse_py_lift__Lifter_lift_expr
user_pcc_parse_py_lift__Lifter__e_BinOp
user_pcc_parse_py_lift__Lifter_lift_expr
user_pcc_parse_py_lift__Lifter__s_AugAssign
```

The bad object header was already poisoned and the payload bytes included the
string literal `"true"`, pointing at list-element ownership/rooting around
`pcc/parse/py_lift.py::Lifter._e_Str`:

```python
cooked: list[str] = []
for raw_text, is_raw in e.parts:
    cooked.append(raw_text if is_raw else _decode_escapes(raw_text))
return pa.StrLit(..., "".join(cooked))
```

Next probe: inspect generated IR for `_e_Str`, especially tuple-iteration /
tuple-unpack ownership of `raw_text` and the `cooked.append(...)` path, then
add a focused regression that reproduces the string-element lifetime failure
before patching. Do not work around this by disabling tuple tracking, skipping
GC tracking, dropping barriers, or treating the indexed forwarding patch as a
bootstrap fix.

### Update 2026-06-01 continuation

The `_e_Str` hypothesis is superseded by a later, more precise repro. A
backend #3 owner-promotion fix now rewrites referents when a young owner is
promoted to old storage, in both the C runtime and the pcc-Python runtime
mirror. Focused backend #3 tests for list referent promotion and the adjacent
oldification/forwarded-source cases passed, so the earlier stale forwarded
list-slot failure shape is no longer the active blocker.

The pcc-Python runtime-library self-rooting regression was also sharpened. The
test now extracts the exact `define ptr @pcc_gc_retain` body; the previous
string search could inspect a declaration/call site and miss a recursive retain
inside the function body. The fix moved the C-ABI/raw-scaffold return
suppression ahead of the borrowed-parameter retain path, so runtime primitives
such as `pcc_gc_retain` no longer retain their own borrowed parameter on
return.

The current direct `gc3` stage2 repro is still red:

```bash
env -u LC_ALL -u LC_CTYPE perl -e 'alarm shift; exec @ARGV' 360 \
  env PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=py PCC_PYTHON_IR_PASSES=off \
  PCC_PY_FRONTEND_JOBS=1 PCC_GC_BACKEND=3 \
  build/bootstrap-pytest-self-gc3/pcc1 \
  --backend self --python-libpython off pcc/__main__.py \
  -o build/bootstrap-pytest-self-gc3/pcc2.probe
# fails quickly with [BAD_INCREF]
```

The same `pcc1` under `PCC_GC_BACKEND=0` can produce a stage2 probe, which
keeps the current crash scoped to backend #3 semantics rather than the general
self-backend bootstrap command shape.

With `PCC_DEBUG_RELEASES=1` and a diagnostic bad-incref backtrace enabled, the
current failure localizes to:

```text
[BAD_RELEASE] name=owned:_export_method_symbol:BinOp:/Users/jiamo/my/pcc/pcc/py_frontend/pipeline.py:3235:1 obj=... exact_size=50 refcount=0 tag=4 flags=4360 reason=bad-header
```

The source expression is:

```python
if class_name + "_" + method_name in top_level_func_names:
```

This points at nested string-`BinOp` ownership/rooting around
`pipeline._export_method_symbol`, not the old `_e_Str` tuple-iteration shape.
The next proposal must build a minimized reproducer for this nested
`BinOp`-inside-membership call pattern, inspect its generated ownership
cleanup, and only then patch the smallest proven bug. Do not disable tuple
tracking, `py_gc_track`, barriers, or challenger GC behavior to make the gate
green.

## No.10 Audit apparent bootstrap hangs against recent GC indexing/rooting changes

### Code Change

`tests/python/process_timeout.py` provides a shared process-group timeout
helper. `tests/python/test_pcc_bootstrap_full.py` no longer relies on
`subprocess.run(timeout=...)` for the full bootstrap gate: it now launches
`scripts/bootstrap.sh` with `start_new_session=True` and, on timeout, sends
`TERM` then `KILL` to the whole child process group. The runtime-archive
prebuild fixture in `tests/python/conftest.py` uses the same helper so host
`pcc` runtime builds cannot leave compiler children behind either. A focused
harness regression creates a parent process that spawns a sleeping child and
verifies the timeout reaps the grandchild too.

### Active

The `alarm shift; exec @ARGV` timeout wrapper is no longer acceptable evidence
for heavy bootstrap/compiler commands. It failed in this repository after
`exec`, leaving an already exec'd `pcc1` running for more than 11 minutes. The
run was killed manually, and `AGENTS.md` now forbids alarm-then-`exec`
wrappers for heavy commands. Future heavy repros must use a watchdog parent
that forks the command into its own process group and kills that process group
on expiry.

The current process audit still sees an existing full-bootstrap pytest process
chain running `build/bootstrap-pytest-self-gc1/pcc1`; that process belongs to
the already-running five-GC gate and was not killed. Do not stack another heavy
bootstrap repro on top of it unless that gate finishes or is explicitly
abandoned.

A later audit proved that the old full-bootstrap pytest timeout left
`bootstrap.sh` and `pcc1` as PPID=1 orphans after the pytest parent was gone.
That orphan process group was killed manually. This is a test-harness bug, not
a GC semantics result.

Focused verification for the new timeout harness:

```bash
env -u LC_ALL -u LC_CTYPE perl -e '<process-group wrapper>' 60 \
  uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_process_group_timeout_reaps_bootstrap_children \
  -q -n0
# 1 passed in 1.10s
```

The five-backend bootstrap test itself still collects exactly the intended
backend matrix after adding the helper test:

```bash
env -u LC_ALL -u LC_CTYPE perl -e '<process-group wrapper>' 120 \
  uv run pytest \
  'tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self' \
  --collect-only -q
# collected gc0, gc1, gc2, gc3, gc4
```

There is also an independent foreground
`uv run pytest tests/python/test_pcc_bootstrap_full.py` process owned by a
different parent process group. It was not killed. Heavy direct `gc3` repros
are deferred while that process is consuming the bootstrap CPU budget.

Three proposed hang mechanisms were checked against current source:

- `pipeline.py`'s `_runtime_archive_compiler_sources_newer_than` worklist
  loop uses `pending_dirs.pop()` and only appends real child directories.
  The active compiler-source roots currently contain no symlink directories, so
  a symlink directory cycle is not the current bootstrap hang cause.
- `py_gc_index_table.c` is not an open-addressing table. The
  `while (*slot != NULL)` loops are linked-list bucket removals, and inserts
  prepend a heap entry to a bucket. The specific "full table with no empty slot
  causes endless linear probing" mechanism is therefore denied for the current
  code.
- The recent forwarding/identity doubly-linked list wiring was inspected at
  the insertion, rollback, removal, clear, and count sites. Current source uses
  index-based lookup for `pcc_gc_forwarding_find(from)` and
  `pcc_gc_identity_find(obj)`, with list traversal only for counting/clearing
  and for `pcc_gc_forwarding_target_exists(target)`. No source-level list
  self-cycle or non-advancing traversal has been confirmed.

The remaining performance-risk finding is different: the sample that placed
`gc1` stage2 inside
`pipeline._write_native_exports_wire -> _native_export_to_wire` with most
runtime samples in `pcc_gc_note_frame_leave` aligns with the recent implicit
borrowed-parameter/local frame-root work. `pcc_gc_note_frame_leave` takes the
global graph lock and linearly scans `pcc_gc_frames` to find the matching
`slots` pointer. High-frequency tiny helper calls can therefore turn the
correctness fix into severe overhead or a long-running gate without an actual
infinite loop. This is still in the recent-change causal window and must be
proved with a focused frame-root telemetry/profiling probe before any semantic
change.

## No.11 Pin owned nested string `BinOp` temporaries across allocating runtime calls

### Code Change

Candidate codegen patch, not yet confirmed by the full gate:

- `expr_dispatch_lowering.py` pins an owned pointer-valued nested `BinOp`
  operand after it is produced and unpins it after the outer binary operation
  runtime call has consumed it.
- `list_method_lowering.py` pins an owned pointer-valued `BinOp` argument while
  `py_list_append` consumes it, then releases the owned temporary through the
  existing cleanup path.
- `tests/python/test_gc_root_precision.py` adds IR regressions for the two
  shapes exposed by the `gc3` pcc1 stage2 repro:
  `class_name + "_" + method_name in top_level_func_names` and
  `arg_parts.append(class_name + " " + method_name)`.

### Active

The first localized `gc3` bad release was:

```text
[BAD_RELEASE] name=owned:_export_method_symbol:BinOp:pcc/py_frontend/pipeline.py:3235:1
```

That expression is a nested string `BinOp` used as the left operand of an outer
string concat before membership testing. The focused IR regression for this
shape failed before the candidate patch because the inner concat result was
released after the outer concat without being pinned across the allocating
outer `py_str_concat`.

After the first candidate fix, the direct `gc3` stage2 repro moved to a second
owned string `BinOp` call-argument shape:

```text
[BAD_RELEASE] name=owned:_irbuilder_call_from_args_list:BinOp:pcc/llvm_capi/ir.py:2683:1
```

The source expression is:

```python
arg_parts.append(str(arg_ty) + " " + _value_ref(a))
```

This points at a freshly owned string concat being consumed by `list.append`;
the candidate patch pins that item across `py_list_append`.

The old-wrapper direct repro after the second candidate fix is invalid as a
pass/fail signal because the `alarm; exec` wrapper failed to kill the exec'd
`pcc1`. The process was killed manually.

Focused checks rerun with the process-group timeout wrapper:

```bash
env -u LC_ALL -u LC_CTYPE perl -e '<process-group wrapper>' 240 \
  uv run pytest tests/python/test_gc_root_precision.py -q -n0
# 5 passed in 1.43s

env -u LC_ALL -u LC_CTYPE perl -e '<process-group wrapper>' 240 \
  uv run pytest \
  tests/python/test_gc_backend_generational.py::test_generational_backend_borrowed_frame_root_rewrite_preserves_source_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_young_owner_promotion_rewrites_list_referent_to_oldified_copy \
  -q -n0
# 3 passed in 17.49s
```

This confirms the focused IR shape and adjacent backend #3 root-rewrite gates,
but not the direct `gc3` pcc1 stage2 repro. Before this proposal can be marked
confirmed, rerun that direct backend #3 repro with the process-group timeout
wrapper when the independent full-bootstrap process is no longer consuming the
same CPU budget.

## No.12 backend #3 direct pcc1->pcc2 hang audit: safepoint root scan and frame-leave cost

### Active

This is not closed. The current direct backend #3 pcc1->pcc2 gate still fails.

New focused red/green evidence:

- Red: `pcc_gc_safepoint()` under backend #3 promoted a borrowed frame root even
  though the call has only a budget-1 automatic step. Probe output before the
  fix was `["1", "0", "1", "1"]`: one relocation forwarding happened during
  safepoint and the root no longer read back as the original object.
- Fix: `pcc_gc_safepoint()` in both `py_obj.c` and `py_obj.py` now skips the
  automatic `pcc_gc_step(1)` for backend #3. Explicit `pcc_gc_step(...)` and
  minor-refill promotion are unchanged.
- Green:
  ```bash
  uv run pytest \
    tests/python/test_gc_backend_generational.py::test_generational_backend_safepoint_does_not_promote_frame_roots \
    tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_safepoint_does_not_promote_frame_roots \
    ...backend3 borrowed/root/owner focused tests... \
    -q -n0
  # 8 passed in 112.56s

  uv run pytest tests/python/test_gc_root_precision.py -q -n0
  # 5 passed in 23-25s range
  ```

Direct `PCC_GC_BACKEND=3` pcc1->pcc2 after the safepoint fix no longer sat
silently for 11 minutes, but the next bottleneck was sampled in
`pcc_gc_note_frame_leave`:

```text
sample: pcc_gc_note_frame_leave ~3800/5000 samples
```

Root cause for that part: the frontend now registers many borrowed local frame
roots, while the pcc-Python runtime frame registry removes roots by linearly
searching a singly linked list by `slots`. Function cleanup then becomes O(n^2)
when many root slots are left one-by-one.

Attempted fixes and current status:

- C runtime: added `prev` links and a `slots -> frame node` index so C
  `pcc_gc_note_frame_leave` can unlink in O(1), with duplicate-slot reindexing.
- pcc-Python mirror: a direct index attempt was rejected after focused probes
  segfaulted; generated low-level helpers self-registered GC frame roots while
  the frame registry was being mutated, causing recursive `pcc_gc_frame_enter`
  paths. The mirror was restored to the original linear logic.
- Frontend: added `_gc_rooted_local_order` so non-owned/borrrowed frame roots
  can be left in reverse registration order. Owned-local release/leave order was
  restored to the old sorted release+immediate-leave behavior after changing
  that order exposed a `BAD_INCREF`.

Current direct evidence:

```bash
PCC_GC_BACKEND=3 build/bootstrap-direct-gc3-lifo3/pcc1 \
  --backend self --python-libpython off pcc/__main__.py \
  -o build/bootstrap-direct-gc3-lifo3/pcc2.probe
# exits ~60s with:
# [BAD_INCREF] o=... tag=-1
# py_decref -> user_pcc_py_frontend_pipeline_compile_python_multi
```

With `PCC_GC_MINOR_HEAP_SIZE=67108864`, the `BAD_INCREF` did not appear within
60 seconds, but sampling still showed `pcc_gc_note_frame_leave` dominating. That
separates the original timeout from `pipeline.py` traversal: the active problem
is backend #3 runtime/root-management cost and a follow-on reference bug around
minor promotion / cleanup, not a directory worklist loop.

## No.13 Rewrite direct slots for unsupported minor-arena owners during backend #3 promotion

### Active

Focused root cause confirmed 2026-06-03: backend #3 promotion skipped direct
slot rewriting for a young `PY_FLAG_GC_MINOR_ARENA` owner when that owner could
not be copy-oldified. A list owner hit this path: the child was oldified through
the frame root and was readable through `pcc_gc_load_ptr(owner, &items[0])`, but
the raw owned slot `items[0]` still held the stale minor source.

Code change:

- C runtime `pcc_gc_promote_young_object()` now scans direct referent slots in
  the unsupported-minor-owner branch instead of returning immediately.
- The scan is intentionally non-recursive in that branch: it rewrites
  already-forwarded or copy-oldifiable direct children, but does not recurse
  into another unsupported young container and risk A<->B container cycles.
- `pcc/py_runtime/py/py_gc_backend.py` mirrors the same `recurse` mode for the
  pcc-Python runtime archive.
- `tests/python/test_gc_root_precision.py` assertions were narrowed to the
  actual safety contract: the nested string temporary is pinned across the
  allocating concat/list append, while cleanup may release before the final
  unpin once the destination owns the item.

Focused evidence:

```bash
env -u LC_ALL -u LC_CTYPE uv run pytest tests/python/test_gc_root_precision.py -q -n0
# 5 passed in 1.24s

env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_backend_generational.py::test_generational_backend_safepoint_does_not_promote_frame_roots \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_safepoint_does_not_promote_frame_roots \
  tests/python/test_gc_backend_generational.py::test_generational_backend_borrowed_frame_root_rewrite_preserves_source_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_young_owner_promotion_rewrites_list_referent_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_young_owner_promotion_rewrites_list_referent_to_oldified_copy \
  -q -n0
# 6 passed in 74.18s
```

This is not confirmed against the direct backend #3 pcc1->pcc2 repro yet. The
next required gate is the direct repro with the process-group timeout helper.

## No.14 backend #3 pcc1->pcc2 follow-up: ownership holes fixed; bootstrap still blocked on tracking/index/root cost

### Active

This is not closed. The direct backend #3 pcc1->pcc2 gate still fails, but the
first failing boundary changed.

New fixes landed in this slice:

- Raw-scaffold `Subscript` RHS ownership was too conservative. `raw[i]` for
  `str` lowers through `py_str_index`, which returns an owned `str`; the
  previous `Subscript -> borrowed` rule registered `c = raw[i]` and
  `nxt = raw[i + 1]` as borrowed roots in `_decode_escapes`. The ownership
  classifier now treats object-typed subscript results as owned. Regenerated
  `pcc/parse/py_lift.py` IR shows `c.owned` / `nxt.owned` cleanup and positive
  owned frame maps.
- Owned-local cleanup releases through `pcc_gc_load_ptr()` before
  `pcc_gc_release()`, so backend #3 forwarding/read barriers run before cleanup
  consumes an owned local.
- `pcc_gc_release()` on a backend #3 forwarded source now consumes the source
  reference instead of directly decrefing the forwarded target. This preserves
  the source/target ownership split after root or slot rewrite.
- Unsupported `PY_FLAG_GC_MINOR_ARENA` owners are now promoted in place. The
  branch clears `PY_FLAG_GC_YOUNG`, sets `PY_FLAG_GC_OLD`, then promotes
  referents with the owner already old so tuple/list A<->B cycles terminate.
  The pcc-Python runtime mirror matches the C runtime.

Focused evidence:

```bash
env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_arena_tuple_cycle_promotes_in_place \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_arena_tuple_cycle_promotes_in_place \
  -q -n0
# 2 passed in 31.70s

env -u LC_ALL -u LC_CTYPE uv run pytest tests/python/test_gc_root_precision.py \
  tests/python/test_gc_backend_generational.py::test_generational_backend_release_of_forwarded_source_consumes_source_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_release_of_forwarded_source_consumes_source_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_string_loop_owned_root_cleanup \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_string_loop_owned_root_cleanup \
  tests/python/test_gc_backend_generational.py::test_generational_backend_oldified_tuple_retains_old_child_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_oldified_tuple_retains_old_child_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_arena_tuple_cycle_promotes_in_place \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_arena_tuple_cycle_promotes_in_place \
  -q -n0
# 15 passed in 152.21s
```

Bootstrap evidence after these fixes:

- Serial `scripts/bootstrap.sh --backend self --stage 3` with
  `PCC_GC_BACKEND=3` and `PCC_BOOTSTRAP_PY_FRONTEND_JOBS=1` reached the stage2
  pcc1 command for ~108s, printed the stage2 output line, then returned
  SIGSEGV before leaving a `pcc2` artifact. The script correctly removes stale
  stage outputs at the start of each stage, so absence of `pcc2` is expected
  after the crash.
- Direct `pcc1 -> pcc2` under the process-group timeout helper no longer
  reproduces a quick `_e_Str` double-free or the earlier parser-time
  promotion/object-index hang as the first symptom. It now primarily
  long-runs in codegen / GC tracking:
  - default GC3: timeout after 330s
  - `PCC_GC_MINOR_HEAP_SIZE=67108864`: timeout after 390s, reached codegen,
    ~6.7GiB RSS
  - `PCC_GC_MINOR_ALLOC_MAX=16`: timeout after 330s

Samples show time spread across pcc-Python codegen plus GC3
object/forwarding index lookup/insert, frame-root enter/leave, relocation read
barriers, and allocator calls. Current hypothesis: the remaining bootstrap
blocker is no longer one stale parser string object; it is backend #3
tracking/index/root overhead or retained state under the self-host codegen
workload. Next useful loop should either minimize the codegen long-run with a
captured worker/module, or instrument GC3 object/index counts and live/forwarded
source counts during pcc1 stage2 to find the retention slope.

## No.15 Backend #3/#4 focused root-slot fast path verification

### Code Change

Backend #3/#4 root-slot promotion now skips a slot when the child is neither
`PY_FLAG_GC_YOUNG` nor `PY_FLAG_GC_RELOCATION_CANDIDATE`. The same guard exists
in the pcc-Python runtime mirror. Relocation candidates are intentionally kept
on the slow path so forwarded source slots can still be rewritten.

### CONFIRMED focused

Focused GC3/GC4 evidence after the guard:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_gc_root_precision.py::test_string_subscript_assignment_is_owned_local_in_raw_scaffold \
  tests/python/test_gc_backend_generational.py::test_gc_frame_registry_hot_path_skips_frame_index_hashing \
  tests/python/test_gc_backend_generational.py::test_generational_minor_refill_skips_global_young_scan \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_borrowed_frame_root_rewrite_preserves_source_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_forwarded_minor_source_is_inactive_after_oldify \
  tests/python/test_gc_backend_generational.py::test_generational_backend_cross_domain_remembered_slot_rewrite \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_promotes_tls_exception_root \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_arena_tuple_cycle_promotes_in_place \
  -q -n0
# 9 passed in 33.52s

env -u LC_ALL uv run pytest \
  tests/python/gc_production_contract/test_valueclass_pointer_payload.py \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_relocation_retargets_remembered_list_slots \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_remembered_set_tracks_unique_dirty_slots \
  -q -n0
# 7 passed in 51.63s

env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_gc_backend.py \
  pcc/py_runtime/py/py_substrate.py
# passed

env -u LC_ALL uv run pytest \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_borrowed_frame_root_rewrite_preserves_source_ref \
  -q -n0
# 1 passed in 25.62s

env -u LC_ALL uv run pytest \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_forwarded_minor_source_is_inactive_after_oldify \
  -q -n0
# 1 passed in 25.51s

env -u LC_ALL uv run pytest \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_cross_domain_remembered_slot_rewrite \
  -q -n0
# 1 passed in 25.16s
```

A combined three-node pcc-Python mirror run hit its 60s watchdog after printing
one pass. A process audit showed unrelated full-bootstrap process groups still
running in the workspace; those process groups were terminated, and the follow-up
`ps` check found no remaining `test_pcc_bootstrap_full`,
`bootstrap-pytest-self`, `pcc-python-multi-codegen-worker`, or `uv run pytest`
processes. This closes only the focused GC3/GC4 fast-path verification; it does
not close the full five-GC bootstrap matrix.

## No.16 Backend #3 full bootstrap correctness restored, 60s still open

### Code Change

Backend #3 root/relocation work was extended with safe performance fast paths:

- root-slot promotion rejects non-header/raw values and skips stable non-young,
  non-relocation-candidate slots while preserving relocation-candidate
  forwarded-source rewrites;
- frame root nodes cache root count, borrowed mode, and per-slot stable values;
- `pcc_gc_note_relocation_read` and generated slot loads skip the graph/index
  slow path for ordinary non-candidate objects;
- backend #3 write barriers skip the graph lock when the store is not old-owner
  to young-value;
- the object index now uses slab/free-list entries and a larger initial table;
- backend #3 defaults use a 32MiB minor heap and `PCC_GC_MINOR_ALLOC_MAX=16`.

The C runtime and pcc-Python runtime mirror were kept in sync. An attempted
additional shortcut that removed the pointer-shape guard from slot
relocation-candidate checks caused worker segfaults and was reverted; that path
is not safe for this runtime because some generated slots can still carry
non-object pointer-shaped values.

### CONFIRMED

Focused checks:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_obj.py pcc/py_runtime/py/py_gc_backend.py
# passed

env -u LC_ALL uv run pytest \
  tests/python/test_gc_backend_generational.py::test_gc_frame_registry_hot_path_uses_frame_index_lookup \
  tests/python/test_gc_backend_generational.py::test_generational_minor_heap_default_is_bootstrap_sized \
  tests/python/test_gc_backend_generational.py::test_gc_relocation_read_non_candidate_fast_path_skips_graph_lock \
  tests/python/test_gc_backend_generational.py::test_gc_slot_barriers_fast_path_non_relocation_and_non_old_to_young \
  tests/python/test_gc_backend_generational.py::test_gc_object_index_uses_slab_entries_and_single_insert_lookup \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_rewrites_frame_root_slot_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_borrowed_frame_root_rewrite_preserves_source_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_forwarded_minor_source_is_inactive_after_oldify \
  tests/python/test_gc_backend_generational.py::test_generational_backend_release_of_forwarded_source_consumes_source_ref \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_minor_refill_rewrites_frame_root_slot_to_oldified_copy \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_borrowed_frame_root_rewrite_preserves_source_ref \
  -q -n0
# 11 passed in 72.14s

git diff --check
# passed
```

Full backend #3 correctness gate:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self[gc3] \
  -q -n0 -s
# Bootstrap successful under PCC_GC_BACKEND=3: pcc2 and pcc3 are byte-identical.
# 1 passed in 104.15s
```

Direct stage probes show the remaining boundary is performance, not a crash:
the same pcc1 compiles `pcc2` in roughly 40s under GC3 and roughly 21s under
GC0. `PCC_PY_FRONTEND_JOBS=16` did not materially improve the GC3 stage2 wall,
and `PCC_PY_FRONTEND_JOBS=20` was worse. The full GC3 bootstrap is therefore
correct but not inside the requested 60s target.

## No.17 Backend #4 forwarding-target lookup index

### Code Change

Backend #4 forwarding entries now maintain a secondary target index in both
runtime tiers:

- C runtime: `pcc_gc_forwarding_target_index_*` maps target object pointer to
  the head of that target's forwarding-node chain, and `PccGcForwardNode`
  carries `target_next` / `target_prev`.
- pcc-Python runtime mirror: forwarding nodes expanded from 32 bytes to
  48 bytes with the same target-chain slots at offsets 32 and 40.
- `_forwarding_target_exists(...)` / `pcc_gc_forwarding_target_exists(...)`
  now answer from the target index instead of scanning the full forwarding
  list.
- source removal, target freeing, and full forwarding clear remove both the
  source index and the target index entries.

This specifically targets the backend #4 relocation-set selection path, where
objects that are already forwarding targets must not be selected again, but the
old implementation paid a full forwarding-list scan for each candidate.

### CONFIRMED focused

Focused validation after the target-index patch:

```bash
env -u LC_ALL uv run pytest \
  tests/python/test_gc_backend4_production.py::test_backend4_forwarding_target_lookup_is_indexed \
  -q -n0
# 1 passed in 0.36s

env -u LC_ALL uv run python -m py_compile \
  pcc/py_runtime/py/py_obj.py pcc/py_runtime/py/py_gc_backend.py
# passed

git diff --check
# passed

env -u LC_ALL uv run pytest \
  tests/python/gc_production_contract/test_valueclass_pointer_payload.py \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_relocation_retargets_remembered_list_slots \
  tests/python/test_gc_backend4_production.py::test_backend4_genzgc_remembered_set_tracks_unique_dirty_slots \
  tests/python/test_gc_backend4_production.py::test_backend4_forwarding_target_lookup_is_indexed \
  -q -n0
# 8 passed in 43.81s
```

This confirms the structural optimization and the focused GC4 relocation /
valueclass contract. It does not yet claim a fresh cold `gc4` three-stage
self-bootstrap result or the full five-GC bootstrap matrix.

## No.18 Direct valueclass payload locals root pointer fields

### Failure

The boxed valueclass contract did not cover direct payload values that stay
unboxed in local variables or function parameters. A new focused program keeps
`Bag(items: list, label: str)` in direct payload form, runs `gc.collect()`
before and after a method call on `b.items`, and then reads the payload again.

Focused red gate:

```bash
env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 failed
# backend #0: BAD_INCREF tag=0
# backend #1/#2/#3: stdout "0\n<null>\nbag\n0\nbag\n"
# backend #4: BAD_INCREF tag=5
```

### Root Cause

Two separate issues combined:

- Direct valueclass payload pointer fields were not registered as GC frame
  slots, so explicit collection could reclaim or fail to rewrite the embedded
  `PyObject*` field.
- Direct payload attribute loads were classified like owned object-producing
  attribute reads. `b.items.append(4)` therefore emitted
  `pcc_gc_release(b.items)` even though `b.items` is borrowed from the payload
  field and remains stored there.

The first attempted root fix also produced invalid same-block IR because it
stored a `Value` in the entry-alloca insertion cache. The corrected shape
inserts the field GEP after the payload alloca and leaves the alloca cache
untouched.

### Fix

Python codegen now discovers pointer-bearing field paths in direct valueclass
payload types, including nested valueclass payload fields. For each path it
registers the actual field address inside the local/parameter payload alloca as
a borrowed one-slot GC frame root. Backend #4 remap therefore updates the
payload field itself.

Ownership classification now treats direct valueclass payload attribute loads
as borrowed, so method calls on payload fields do not release a field still
owned by the surrounding payload.

### CONFIRMED focused

```bash
env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 28.49s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valuebox_roots.py \
  tests/python/gc_production_contract/test_valueclass_pointer_payload.py
# 10 passed in 1.39s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_value_class_unboxed.py \
  tests/python/data_model/test_value_class_runtime.py \
  tests/python/data_model/test_value_class_source_shape.py \
  tests/python/data_model/test_value_class_field_flattening.py
# 103 passed in 24.12s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py \
  tests/python/test_ir_py_fallback_baseline.py
# 18 passed in 148.25s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 26.36s
```

`tests/python/test_bootstrap_gate_baseline.py` was also run but skipped all
four checks because its canonical `build/bootstrap-{llvm,self}/pcc{1,2,3}`
artifacts are absent. This slice therefore does not claim a fresh pcc1/pcc2/pcc3
fixed point or the full five-GC bootstrap matrix.

## No.19 Closure-captured direct valueclass payload crosses object ABI boundary

### Failure

The direct-payload root gate covered locals, parameters, method receivers,
typed returns, constructor temporaries, walrus targets, reassignment targets,
conditional expressions, loop-carried locals, and exception/finally flow, but
did not cover a direct valueclass payload captured by a nested closure.

The minimized strict self-backend program kept
`Holder(Nested(list, str), list, str)` in direct payload form, captured it in
`inner()`, forced collection before and after calling `touch_holder(captured)`,
and then read nested pointer fields again. It failed at compile time:

```text
PCC-PY-COMPILE-001: [python-frontend] marshal_to_object: DynType with IR { { ptr, ptr }, ptr, ptr } not supported
```

Direct `compile_python(..., emit_llvm_only=True, libpython_mode="off",
ir_scaffold_mode="on", backend="self")` traceback localized the failure to
`_emit_arg_for_abi_param(...)`: the hidden closure argument was target-typed as
`DynType`/object ABI while `_emit_expr(Name("captured"))` returned the aggregate
valueclass payload.

### Fix

Two object-boundary corrections landed:

- First assignment of a valueclass constructor payload now records the local's
  effective storage/declared type as the valueclass payload type instead of the
  original unannotated `DynType`. Later name loads and closure capture
  bookkeeping can therefore recover the real payload type.
- `_emit_arg_for_abi_param(...)` now recognizes object-ABI parameters whose
  actual argument IR is a valueclass payload. For `Name` arguments it consults
  the current environment's declared type, then boxes the payload through
  `_emit_valueclass_payload_to_object(...)` instead of asking generic `DynType`
  marshalling to handle an aggregate.

The existing closure-capture tuple bridge and boxed-parameter cell init paths
also use `_emit_value_as_pcc_object_or_bridge(...)`, so direct valueclass
payloads crossing closure object boundaries use the same ValueBox projection
path as container/object boundaries.

### CONFIRMED focused

The minimized `/tmp` probe compiled and ran under backend #4 with the expected
readback:

```text
2
7
closure-nested
2
8
closure-holder
2
closure-nested
2
closure-holder
```

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/assignment_statement_lowering.py \
  pcc/py_frontend/codegen/user_function_lowering.py \
  pcc/py_frontend/codegen/unary_call_lowering.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.06s

env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_native_closure.py
# 4 passed in 1.19s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py::test_pipeline_and_codegen_host_contract_do_not_drift
# 1 passed in 0.22s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 26.85s
```

This is a focused closure-captured direct-payload GC/ABI slice only. It does
not claim broad value payload completion, a pcc1/pcc2/pcc3 fixed point, or a
fresh full five-GC bootstrap matrix.

## No.20 Tuple-unpacked direct valueclass payload targets need payload storage roots

### Failure

The direct-payload root gate did not cover tuple-literal unpack assignment into
fresh valueclass payload locals. A minimized strict self-backend program tried:

```python
left, right = (
    Holder(Nested([110], "unpack-left-nested"), ["unpack-left-head"], "unpack-left-holder"),
    Holder(Nested([111], "unpack-right-nested"), ["unpack-right-head"], "unpack-right-holder"),
)
```

then forced collection, called `touch_holder(left)` and `touch_holder(right)`,
forced collection again, and read both direct payloads back.

Before the fix, compilation failed before any backend run:

```text
PCC-PY-COMPILE-001: [python-frontend] Layer 1 tuple-unpack target 'left' has unsupported type ClassType
```

### Fix

`_store_value_at_name(...)`, the common helper used by tuple-unpack name
targets, now accepts valueclass payload target types in addition to scalar and
object targets. It allocates the payload storage type and, after storing the
unpacked payload, registers pointer-bearing payload field addresses through
`_ensure_valueclass_payload_gc_roots(...)`. This mirrors the normal assignment
path for direct valueclass payload locals without changing object/scalar unpack
ownership rules.

### CONFIRMED focused

The minimized `/tmp` probe compiled and ran under backend #4 with the expected
left/right payload readback.

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/assignment_store_lowering.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.01s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 23.38s

env -u LC_ALL uv run pytest -q -n0 tests/python/test_native_float_tuple_unpack.py
# 2 passed in 0.98s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_list_unpack_assignment.py \
  tests/python/test_py_unpacking.py \
  tests/python/test_gc_effectiveness.py::test_tuple_unpack_instance_return_no_growth \
  tests/python/test_gc_effectiveness.py::test_tuple_unpack_dict_self_cycle_reclaims_between_iterations
# 5 passed in 2.15s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_tuple_unpack_rebind_to_borrowed_value_does_not_overrelease
# 1 passed in 0.40s
```

## No.21 For-loop direct valueclass payload targets need object-to-payload conversion

### Failure

The direct-payload root gate did not cover list/tuple-backed `for` loop targets
whose inferred element type is a valueclass payload. A minimized strict
self-backend program iterated over a list literal of
`Holder(Nested(list, str), list, str)` values, forced collection in each
iteration, called a typed callee that mutates nested pointer fields, then read
the final loop-carried value back.

Before the fix, the loop-variable slot had native aggregate payload storage
(`{ { ptr, ptr }, ptr, ptr }` for this shape), but `_emit_for_list_index(...)`
stored the raw `PyObject*` returned by `py_list_get` into that aggregate slot.
The probe printed a random integer and `<null>`, then backend #4 crashed.

### Fix

`_emit_for_list_index(...)` now detects valueclass payload element types on the
non-native-int list/tuple indexed loop path. It converts the fetched object with
`_emit_object_to_valueclass_payload(...)` before storing the loop target, falling
back to the existing generic marshal only if the valueclass conversion does not
produce a payload. After the store it registers pointer-bearing payload field
addresses with `_ensure_valueclass_payload_gc_roots(...)`.

This keeps object/scalar loop targets and the native typed-int list fast path on
their existing branches.

### CONFIRMED focused

The minimized `/tmp` probe compiled under strict no-libpython self-backend and
printed the expected for-first/for-second payload readback under all five GC
backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_for_probe.py -o /tmp/pcc_value_for_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_for_probe_bin
# all five backends printed the expected payload readback
```

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/for_loop_lowering.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.06s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 24.46s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_for_generic_iterable.py \
  tests/python/test_python_iteration_parity.py \
  tests/python/test_native_float_add_generic.py
# 14 passed in 5.38s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_typed_int_unboxed.py::test_typed_list_int_loop_defaults_to_boxed_tagged_shape \
  tests/python/test_py_typed_int_unboxed.py::test_unsafe_i64_typed_list_int_loop_keeps_accumulator_unboxed \
  tests/python/test_py_typed_int_unboxed.py::test_typed_list_i64_runtime_helpers_match_c_fast_path \
  tests/python/test_py_typed_int_unboxed.py::test_unsafe_i64_typed_list_int_loop_falls_back_for_heap_int_elements
# 4 passed in 0.84s
```

This is a focused for-loop direct-payload conversion/rooting slice only. It does
not claim broad value payload completion, a pcc1/pcc2/pcc3 fixed point, or a
fresh full five-GC bootstrap matrix.

This is a focused tuple-unpacked direct-payload GC/assignment slice only. It
does not claim broad value payload completion, pcc1/pcc2/pcc3 bootstrap, or a
fresh full five-GC bootstrap matrix.

## No.22 Comprehension direct valueclass payload targets need nested attr type recovery and roots

### Failure

The direct-payload root gate did not cover list-comprehension targets whose
inferred element type is a valueclass payload and whose element expression reads
a nested payload field. A minimized strict self-backend program used:

```python
values = [
    current.nested.label
    for current in [
        Holder(Nested([130], "comp-first-nested"), ["comp-first-head"], "comp-first-holder"),
        Holder(Nested([131], "comp-second-nested"), ["comp-second-head"], "comp-second-holder"),
    ]
    if keep()
]
```

where `keep()` forces `gc.collect()` and returns `True`.

Before the fix, all five GC backends failed with `AttributeError: label`.
Removing the collection still failed, while `current.title` succeeded. That
localized the bug to nested valueclass payload attribute type recovery inside
the comprehension target scope, not to backend #4 relocation alone.

### Fix

`attr_load_lowering.py` now resolves valueclass payload expression types through
the current environment. For a chain such as `current.nested.label`, it can
infer from the `current` env slot that `current.nested` is a `Nested` payload
even when the AST type on the intermediate `Attr` is imprecise.

`comprehension_lowering.py` now registers pointer-bearing payload field roots
for indexed valueclass comprehension targets after storing each payload value.
Before restoring the outer comprehension scope, it clears those hidden target
pointer fields so the borrowed root slots do not keep a comprehension-local
payload alive beyond the comprehension.

### CONFIRMED focused

The minimized `/tmp` probe compiled under strict no-libpython self-backend and
printed the expected nested label readback under all five GC backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_comp_probe.py -o /tmp/pcc_value_comp_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_comp_probe_bin
# all five backends printed comp-first-nested / comp-second-nested
```

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/attr_load_lowering.py \
  pcc/py_frontend/codegen/comprehension_lowering.py \
  pcc/py_frontend/codegen/host_contract.py \
  pcc/py_frontend/codegen/_l1_codegen_static_methods.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.14s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 26.70s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_comprehension_scope_no_libpython.py \
  tests/python/test_py_comprehension_iterators.py \
  tests/python/test_native_comprehension_over_generator.py \
  tests/python/test_python_iteration_parity.py
# 17 passed in 6.91s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_py_frontend_ir_pass_pipeline.py \
  tests/python/test_ir_scaffold_symbols.py \
  tests/python/test_ir_scaffold_simple_methods.py
# 267 passed in 31.82s
```

This is a focused comprehension target direct-payload attr/rooting slice only.
It does not claim broad value payload completion, a pcc1/pcc2/pcc3 fixed point,
or a fresh full five-GC bootstrap matrix.

## No.23 Set/dict comprehension direct valueclass payload targets share the indexed comprehension root path

### Coverage

After No.22 fixed indexed list-comprehension targets, the remaining sibling
forms were set-comprehension and dict-comprehension targets that bind direct
valueclass payloads and read nested pointer fields after a collection in the
comprehension `if` clause:

```python
labels = {
    current.nested.label
    for current in [Holder(...), Holder(...)]
    if keep()
}
table = {
    current.title: current.nested.label
    for current in [Holder(...), Holder(...)]
    if keep()
}
```

### CONFIRMED focused

No implementation change was required for this slice. The indexed
comprehension target path fixed in No.22 already covers set and dict
comprehensions because they use the same `_emit_comprehension_indexed(...)`
target binding before the kind-specific innermost insertion.

The minimized `/tmp` probe compiled under strict no-libpython self-backend and
printed the expected set/dict payload readback under all five GC backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_comp_collections_probe.py -o /tmp/pcc_value_comp_collections_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_comp_collections_probe_bin
# all five backends printed:
# 2 / True / True / dict-first-nested / dict-second-nested
```

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.19s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 26.28s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_comprehension_scope_no_libpython.py \
  tests/python/test_py_comprehension_iterators.py \
  tests/python/test_native_comprehension_over_generator.py \
  tests/python/test_python_iteration_parity.py \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[list_comprehension]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[dict_set_comprehension]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[nested_list_comprehension]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[multifor_list_comprehension]'
# 21 passed in 8.02s
```

This is a focused set/dict comprehension target coverage slice only. It does
not claim broad value payload completion, a pcc1/pcc2/pcc3 fixed point, or a
fresh full five-GC bootstrap matrix.

## No.24 List/tuple subscript direct valueclass payload targets preserve payload roots

### Coverage

The direct-payload root gate did not yet cover a direct valueclass payload
loaded from a typed list or tuple subscript expression and rebound to a local:

```python
values = [Holder(...), Holder(...)]
picked = values[1]
gc.collect()
touch_holder(picked)

values = (Holder(...), Holder(...))
picked = values[1]
gc.collect()
touch_holder(picked)
```

`touch_holder(...)` mutates nested pointer-bearing payload fields across
explicit collections and reads them back.

### CONFIRMED focused

No implementation change was required for this slice. The existing direct
assignment/subscript path already preserves the valueclass payload projection
and registers pointer-bearing payload roots for the rebound local.

The minimized `/tmp` probes compiled under strict no-libpython self-backend and
printed the expected list-subscript and tuple-subscript payload readback under
all five GC backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_subscript_probe.py -o /tmp/pcc_value_subscript_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_subscript_probe_bin
# all five backends printed the expected sub-second payload readback

env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_tuple_subscript_probe.py -o /tmp/pcc_value_tuple_subscript_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_tuple_subscript_probe_bin
# all five backends printed the expected tuple-sub-second payload readback
```

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.32s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 27.66s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_subscript_raise.py \
  tests/python/test_native_list_index_error.py \
  tests/python/test_native_tuple_index_range.py \
  tests/python/test_py_multi_file_compile.py::MultiFileCompileTests::test_tuple_unpack_rebind_to_borrowed_value_does_not_overrelease \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[list_tuple_unpack_slice_mutation]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[negative_index_and_slices]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[tuple_list_constructors]'
# 8 passed in 3.52s
```

This is a focused list/tuple subscript target coverage slice only. It does not
claim broad value payload completion, a pcc1/pcc2/pcc3 fixed point, or a fresh
full five-GC bootstrap matrix.

## No.25 Bool-op direct valueclass payload targets preserve payload roots

### Coverage

The direct-payload root gate did not yet cover short-circuit bool-op expressions
that produce a direct valueclass payload and rebind it to a local:

```python
selected_or = left or right
gc.collect()
touch_holder(selected_or)

selected_and = first and second
gc.collect()
touch_holder(selected_and)
```

`touch_holder(...)` mutates nested pointer-bearing payload fields across
explicit collections and reads them back.

### CONFIRMED focused

No implementation change was required for this slice. The existing bool-op
projection/local-root path already preserves the valueclass payload projection
and registers pointer-bearing payload roots for the rebound local.

The minimized `/tmp` probe compiled under strict no-libpython self-backend and
printed the expected `bool-left-*` and `bool-second-*` payload readback under
all five GC backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_boolop_probe.py -o /tmp/pcc_value_boolop_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_boolop_probe_bin
# all five backends printed the expected bool-op payload readback
```

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.33s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 26.75s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_constructor_condition_truthiness_self_backend \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_conditional_expr_projection_self_backend \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_conditional_expr_projection_boxes_valuebox \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[bool_short_circuit]' \
  'tests/python/test_python_cpython_alignment.py::test_supported_python_features_match_cpython[conditional_expression]'
# 5 passed in 1.77s
```

This is a focused bool-op target coverage slice only. It does not claim broad
value payload completion, a pcc1/pcc2/pcc3 fixed point, or a fresh full
five-GC bootstrap matrix.

## No.26 Module-global direct valueclass payload targets need module storage and field roots

### Failure

The direct-payload root gate did not cover module-global valueclass payloads:

```python
global_holder = Holder(Nested([200], "global-nested"), ["global-head"], "global-holder")

def main() -> None:
    gc.collect()
    touch_holder(global_holder)
```

Before the fix, the strict self-backend probe compiled but failed before any
relocation-specific backend on GC0:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_probe.py -o /tmp/pcc_value_module_global_probe_bin
# passed before the fix

PCC_GC_BACKEND=0 /tmp/pcc_value_module_global_probe_bin
# NameError: name 'global_holder' is not defined
```

The module-level declare pass only allocated `_module_globals` for scalar and
object assignments. A top-level valueclass constructor assignment was skipped,
so user functions could not resolve the global binding.

### Code Change

`pcc/py_frontend/codegen/module_global_lowering.py` now:

- accepts valueclass payload types in `_ensure_module_global_name(...)`;
- declares top-level valueclass payload assignments in `_declare_module_globals_for(...)`;
- recursively builds zero initializers for literal struct global storage;
- emits module-root GC frame entries for each pointer-bearing field address
  inside a module-global valueclass payload aggregate.

This patch intentionally does not claim full module-global payload teardown or
reassignment ownership semantics. It proves module-global payload storage,
name resolution, and field-root tracing/readback.

### CONFIRMED focused

After the patch, the minimized strict self-backend probe compiled and printed
the expected module-global payload readback under all five GC backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_probe.py -o /tmp/pcc_value_module_global_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_module_global_probe_bin
# all five backends printed the expected global payload readback
```

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/module_global_lowering.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.35s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 27.04s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/data_model/test_value_class_runtime.py::test_valueclass_runtime_nested_valuebox_module_global_projection_self_backend \
  tests/python/test_py_value_class_unboxed.py::test_valueclass_constructor_module_global_dyn_projection_boxes_valuebox \
  tests/python/test_py_cross_module_class_inference.py::CrossModuleClassInferenceTests::test_module_qualified_module_global_value \
  tests/python/test_py_module_augassign.py::test_module_global_augassign_uses_module_storage \
  tests/python/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown
# 5 passed in 2.04s
```

This is a focused module-global payload rooting/readback slice only. It does
not claim broad value payload completion, module-global payload teardown or
reassignment ownership, a pcc1/pcc2/pcc3 fixed point, or a fresh full five-GC
bootstrap matrix.

## No.27 Module-global direct valueclass payload roots need overwrite and teardown ownership

### Failure

The previous module-global slice registered payload field roots and fixed
readback, but aggregate module-global stores still raw-stored the whole payload.
That kept the old pointer-bearing field reference alive across reassignment:

```python
global_holder = Holder(Track("old"), "old-holder")

def replace() -> None:
    global global_holder
    global_holder = Holder(Track("new"), "new-holder")
```

CPython printed:

```text
new-holder
new
1
del:old
```

Before the fix, strict self-backend pcc printed `new-holder`, `new`, and `0`
under all five GC backends. The live module-global readback was correct, but
the old field-owned object was not released.

### Code Change

`pcc/py_frontend/codegen/module_global_lowering.py` now emits field-level
module-global valueclass payload helpers. Reassigning a valueclass payload
module global:

- clears each old pointer-bearing field with `pcc_gc_store_root(slot, NULL)`;
- stores the new aggregate payload;
- refreshes each new pointer-bearing field through `pcc_gc_store_root(...)`,
  so backend #3/#4 observe and heal the real root slot.

`pcc/py_frontend/codegen/module_lifecycle_lowering.py` uses the same clear
helper during module fini, and `host_contract.py` plus
`_l1_codegen_static_methods.py` include the helper surface for the L1
standalone/class-generation path.

### CONFIRMED focused

The strict self-backend reassignment probe now matches CPython under all five
GC backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_reassign_probe.py \
  -o /tmp/pcc_value_module_global_reassign_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_module_global_reassign_probe_bin
# all five backends printed new-holder/new/1/del:old
```

The shutdown probe also proves module fini clears the pointer-bearing field:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_module_global_teardown_probe.py \
  -o /tmp/pcc_value_module_global_teardown_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_module_global_teardown_probe_bin
# all five backends printed stderr del:shutdown
```

Focused and adjacent gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/module_global_lowering.py \
  pcc/py_frontend/codegen/module_lifecycle_lowering.py \
  pcc/py_frontend/codegen/assignment_store_lowering.py \
  pcc/py_frontend/codegen/assignment_statement_lowering.py \
  pcc/py_frontend/codegen/host_contract.py \
  pcc/py_frontend/codegen/_l1_codegen_static_methods.py \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.33s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 24.26s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown \
  tests/python/test_py_module_augassign.py::test_module_global_augassign_uses_module_storage
# 2 passed in 0.80s
```

This is a focused module-global payload overwrite/teardown ownership slice
only. It does not claim broad value payload completion, a pcc1/pcc2/pcc3 fixed
point, or a fresh full five-GC bootstrap matrix.

## No.28 Boxed list-setitem valueclass constructor fields need ownership transfer

### Failure

The valuebox container readback contract covered liveness of pointer-bearing
payload fields, but it did not prove that constructor-owned field references are
released after the payload is boxed through an object boundary. A minimized
list overwrite probe exposed the gap:

```python
cell = [Holder(Track("old"), "old-holder")]
gc.collect()
cell[0] = Holder(Track("new"), "new-holder")
gc.collect()
```

CPython printed `new-holder`, `new`, `1`, `del:old`. Before the fix, strict
self-backend pcc printed `new-holder`, `new`, and `0` under all five GC
backends. The generated IR already released the ValueBox temporary after
`py_list_set`, but `_emit_valueclass_payload_to_object(...)` retained each
field through `py_valuebox_set_field(...)` and never released the
constructor-owned field reference.

### Code Change

`pcc/py_frontend/codegen/type_abi_lowering.py` adds an explicit
`consume_fields` parameter to `_emit_valueclass_payload_to_object(...)`. The
default remains non-consuming so boxing an existing direct valueclass variable
does not steal fields still owned by its payload roots.

Direct valueclass constructor object-boundary conversions pass
`consume_fields=True`. The container bridge carries the same flag for literal
paths when the original AST expression is a direct valueclass constructor. The
ownership helper `_valueclass_payload_expr_fields_are_owned(...)` keeps this
classification source-shaped instead of deciding from the aggregate IR value
alone.

### CONFIRMED focused

The strict self-backend list-overwrite probe now matches CPython under all five
GC backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_valuebox_list_overwrite_probe.py \
  -o /tmp/pcc_valuebox_list_overwrite_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_valuebox_list_overwrite_probe_bin
# all five backends printed new-holder/new/1/del:old
```

The nested nonlocal probe also kept nested valueclass payload readback and
finalizer release stable under all five backends:

```bash
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  /tmp/pcc_value_nonlocal_payload_probe.py \
  -o /tmp/pcc_value_nonlocal_payload_probe_bin
# passed

PCC_GC_BACKEND=0..4 /tmp/pcc_value_nonlocal_payload_probe_bin
# expected nested readback plus 1/del:old
```

Focused gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  tests/python/gc_production_contract/test_valuebox_roots.py \
  pcc/py_frontend/codegen/ownership_lowering.py \
  pcc/py_frontend/codegen/type_abi_lowering.py \
  pcc/py_frontend/codegen/cpy_bridge_lowering.py \
  pcc/py_frontend/codegen/exact_int_lowering.py \
  pcc/py_frontend/codegen/literal_lowering.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valuebox_roots.py
# 5 passed in 1.15s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.23s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 24.52s
```

This is a focused boxed list-literal/list-setitem constructor-field ownership
slice only. It does not claim tuple/dict-value literal ownership,
list.append/dict-setitem/attribute-store ownership, broad value payload
completion, a pcc1/pcc2/pcc3 fixed point, or a fresh full five-GC bootstrap
matrix.

## No.29 Covered ValueBox object-boundary constructor fields need ownership proof and attr temp release

### Failure

After No.28, the remaining covered ValueBox object-boundary ownership cases
were added to `tests/python/gc_production_contract/test_valuebox_roots.py`:
tuple literal, dict value literal, `list.append`, dict subscript assignment,
and normal object attribute store.

The first expanded gate failed on all five GC backends at only the attribute
store tail:

```text
5
del:dict-set-old
5
```

The expected tail was:

```text
6
del:attr-old
```

This showed tuple literal, dict value literal, `list.append`, and dict
subscript assignment were already releasing constructor-owned fields correctly,
while `cell.value = FinalizerHolder(Track("attr-old"), ...)` leaked the old
field after `cell.value = None`.

### Code Change

`pcc/py_frontend/codegen/attr_store_lowering.py` now treats the direct
valueclass constructor projection branch like the other object-boundary stores:

- `_emit_valueclass_payload_to_object(...)` is called with
  `consume_fields=True`, so `py_valuebox_set_field(...)` retains are balanced by
  field releases for owned constructor payload fields;
- after `_emit_attr_store_value(...)` stores the boxed value, the owned
  ValueBox expression temporary is released.

The second point was the observed missing reference edge: clearing the object
attribute released the stored reference, but the expression temporary still held
the ValueBox and therefore its `Track` field alive.

### CONFIRMED focused

Focused gates on the final code state:

```bash
env -u LC_ALL uv run python -m py_compile \
  pcc/py_frontend/codegen/attr_store_lowering.py \
  tests/python/gc_production_contract/test_valuebox_roots.py
# passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valuebox_roots.py
# 5 passed in 26.97s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc_production_contract/test_valueclass_direct_payload_roots.py
# 5 passed in 1.21s

env -u LC_ALL uv run pytest -q -n0 tests/python/gc_production_contract
# 140 passed in 23.75s
```

This is a focused covered ValueBox object-boundary ownership slice only. It
does not claim broad value payload completion, a pcc1/pcc2/pcc3 fixed point, or
a fresh full five-GC bootstrap matrix.

## Report (only when the investigation is closing)

Pending broader gate: full self bootstrap because this fix touches runtime and
shared Python codegen.
