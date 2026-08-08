# 2026-08-07 — GC4 pcc2 graph-lock deadlock fixed (returns_none extern ABI) + three HEAD gate regressions cleared

## Claim (mode-labeled)
The five-GC bootstrap gate's backend-4 chain (`--backend self
--python-libpython off --ir-scaffold on`, stages executed under
`PCC_GC_BACKEND=4`) completes again: stage1→stage2→stage3 plus the post-stage
smoke that previously hung forever. This is a pcc1-self-host-only miscompile
class (host pcc0 output was never affected).

## Root cause
Under pcc1, `encode_type`'s isinstance chain degrades a sibling export's
`-> None` return descriptor to `("dyn",)`. The plain-function extern
consumers (`native_modules._extern_user_function_return_ir_type`,
`extern_func_info_lowering._extern_info_to_funcdef`) trusted the descriptor
and declared the callee ptr-returning against its `ret void` definition; the
caller then rooted/increfed the callee's leftover x0 — deterministically the
TLS block base, i.e. the graph-lock depth word — leaving the lock word set
with no owner (100% CPU spin). Fix: both consumers now honor the schema
`returns_none` bool first (same contract class_gen's method plans already
used). Full chain: docs/investigations/
gc4-pcc2-graph-lock-deadlock-stage2-miscompile.md.

## Gates run (all green)
- `uv run pytest -q -n0 -m integration tests/python/gc/test_pcc_bootstrap_full_gc4.py`
  — 1 passed (25:41, cold chain; re-run again at the final tree state after
  the scaffold-table edits below).
- GC4 smoke direct: pcc2 compiling the 4-line repro under PCC_GC_BACKEND=4
  in 0.56s (was: permanent hang); GC0/GC3 smokes also pass.
- `tests/python/test_extern_returns_none_abi.py` — 4 passed (new focused
  regression pinning the degraded-descriptor shape).
- `tests/python/test_py_multi_file_compile.py` +
  `test_py_multi_file_bootstrap_shim.py` — 133 passed (was 14 failed; the
  failures were HEAD regressions, see below).
- `tests/python/test_fallback_baseline.py` + `test_ir_py_fallback_baseline.py`
  — 27 passed (was 6 failed).
- `tests/python/test_bootstrap_gate_baseline.py` — 2 passed.

## Also fixed in this slice (HEAD regressions blocking the gates)
1. ad60403d dropped every trailing module-level `main()` call
   (`elif False:  # TEMP disabled for bisect`); implemented the intended
   exit-code semantics properly (docs/investigations/
   self-backend-entry-main-call-dropped-exitcode-regression.md).
2. 93cfbca5 / c079c05a / 27b290cb left three unregistered auto-mode fallback
   surfaces (self_module_contracts registry, c_varargs json.dumps indent +
   closure import, udiv/urem scaffold tables) (docs/investigations/
   fallback-baseline-head-regressions-unregistered-closure-modules.md).

## Not claimed
- No five-backend matrix re-run in this slice (gc4 chain proven; gc0/gc3
  smoke-level only). The full matrix remains the cross-backend evidence step.
- GC4 long-run throughput is unchanged and stays open as
  PERF-P1-GC4-FREESTANDING-LONGRUN (this slice removed the deadlock, and the
  earlier Fibonacci index-hash fix removed the 12×→2.8× gap; the churn-rate
  gap vs GC1-3 remains).
