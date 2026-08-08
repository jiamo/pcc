# Investigation: fallback-baseline gates red at HEAD — three unregistered-surface regressions from recent commits (self_module_contracts, c_varargs closure import, udiv/urem scaffold gap)

## Status
resolved — three independent causes, all committed at HEAD before this
session's worktree and each shipped without the fallback gates being run
(same "verification blocked by system load" commit pattern as the trailing
`main()` drop, see
[self-backend-entry-main-call-dropped-exitcode-regression.md](self-backend-entry-main-call-dropped-exitcode-regression.md)).
All fixed; `tests/python/test_fallback_baseline.py` +
`test_ir_py_fallback_baseline.py` now 27 passed.

## Problem Description
`env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py
tests/python/test_ir_py_fallback_baseline.py -q -n0` failed 6 of 27
(2026-08-07): per-module ratchet (`class_gen: 16 vs baseline 0`), closure
totals (`10 vs baseline 0`, OFF and ON modes), and the ON-mode contextual
ratchet (`generation_lowering: 6`, `unsafe_lowering: 18`). The strict
`--python-libpython=off` bootstrap stayed green throughout — these are
auto-mode / probe-mode surface regressions, not broken bootstraps — but the
ratchet exists precisely to keep that CPython surface pinned at zero.

## Repro
```bash
env -u LC_ALL uv run pytest tests/python/test_fallback_baseline.py -q -n0
# 6 failed before the fixes below; 27 passed after.
```
Localization probes (session scratchpad): solo-compile class_gen via
`L1CodeGen` and grep `py_cpy_` call instructions; the closure multi compile
via `probe_stage1_closure._try_full_multi_compile`; the contextual probe via
`pipeline.compile_contextual_per_module_fallback_counts(..., emit_ir_dir=...)`.
Note the probe trap: `count_py_cpy_fallback_calls` (a pipeline function name)
and `py_cpy_call_*` DECLARE lines both contain the grep needle — count only
call instructions.

## Test [CONFIRMED]
All three causes observed as py_cpy call instructions in the respective IR
before the fix, zero after. Attribution: `git diff HEAD` shows none of the
implicated source files (class_gen.py, c_varargs.py import edge,
unsafe_lowering.py) modified in the worktree; `git log -S` dates each cause.

## Causes and fixes (one commit each)

1. **93cfbca5** added `pcc/py_frontend/codegen/self_module_contracts.py` and
   imported its constants in class_gen, but never registered the module in
   layer1_support's static native registry
   (`_PCC_FRONTEND_STATIC_NATIVE_MODULES`, the static export table, and
   `_default_native_module_exports`). Solo probes of class_gen bridged the
   import through `py_cpy_import`/`py_cpy_getattr` and every
   `== PY_AST_FIELD_OVERRIDE_MODULE` comparison through cpy `__eq__`
   (16 calls). Fix: register the module with its two string constants and
   the `IR_SCAFFOLD_FORCED_MODULES` frozenset module-global.

2. **c079c05a** made `generation_lowering` import `postprocess_varargs_ir`
   from `pcc.codegen.c_varargs`, pulling that C-frontend module into the
   stage1 closure; its `VarargsRewriteReport.format_json` used
   `json.dumps(..., indent=2, sort_keys=True)`, and the native json lowering
   (`native_text_modules._emit_native_json_call`) supports only a literal
   `sort_keys` kwarg — so auto mode bridged the whole call chain (10 calls).
   Fixes: (a) drop `indent=` from `format_json` (its only consumer
   `json.loads`-parses the output; comment in place documents the
   bootstrap-safe-dialect constraint); (b) register `pcc.codegen.c_varargs`
   (function export for `postprocess_varargs_ir`) so the contextual probe of
   generation_lowering resolves the import natively (was 6 calls).

3. **27b290cb/2287ceb8** (pcc_gui/metal) added unsafe intrinsics whose
   lowering calls `self.builder.udiv(...)` / `self.builder.urem(...)`; the
   ir-scaffold IRBuilder method tables (`ir_scaffold_lowering.py`) listed
   `sdiv`/`srem` but not `udiv`/`urem`, so the contextual ON-mode probe of
   unsafe_lowering dispatched those two methods dynamically through the cpy
   bridge (18 calls). Fix: add `udiv`/`urem` to all four scaffold tables
   (method set, arity table, kwargs table, dispatch list);
   `pcc.llvm_capi.ir.IRBuilder` already defines both with signatures
   identical to sdiv/srem.

## Notes
- Gates after the fixes: fallback suite 27 passed; multi-file gates +
  focused regressions 138 passed; GC4 full bootstrap gate re-run at this
  tree state for the scaffold-table change (bootstrap-critical path).
- Recurring lesson (third instance today): when an established gate is red,
  `git log` the most recent commits touching the failing subsystem BEFORE
  suspecting the working tree — three consecutive HEAD commits shipped with
  red gates this week.
- Structural smell worth a future slice: layer1_support's static export
  table is a hand-maintained mirror of real module contents; nothing fails
  closed when a new closure module is added without registration — the
  fallback ratchet is currently the only tripwire, and only when someone
  runs it.
