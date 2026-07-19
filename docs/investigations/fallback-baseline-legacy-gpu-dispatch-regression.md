# Fallback baseline RED: 12 `py_cpy_*` in `compile_python` GPU dispatch (legacy `ir_scaffold=off`)

## Status

**OPEN — diagnosed to the construct; regression *timing* not attributed; not yet fixed.**
Found 2026-06-26 while gating an unrelated frontend fix. Appears in **single-module
per-module compilation** (`ir_scaffold=off` AND `on`); the **multi-file
no-libpython closure / three-stage bootstrap is GREEN** (the gpu modules resolve
in the full closure). So the real no-libpython criterion is intact; this is the
per-module *diagnostic* ratchet.

## Symptom

`tests/python/test_fallback_baseline.py` fails 7 of 18:

```
test_total_fallbacks_under_ratchet        : fallback total 12 vs baseline 0
test_per_module_fallbacks_under_ratchet   : pcc.py_frontend.pipeline: 12 vs baseline 0
test_pipeline_static_cross_module_exports_stay_clean
test_on_mode_total_fallbacks_under_ratchet  (+3 more on_mode_*)
```

Baseline `tests/fallback_baseline.json` (captured 2026-05-01) pins
`totals.fallbacks_total = 0` and `per_module["pcc.py_frontend.pipeline"] = 0`.

## Root construct (CONFIRMED)

All 12 `py_cpy_*` are emitted inside `user_pcc_py_frontend_pipeline_compile_python`
(i.e. `compile_python`, `pcc/py_frontend/pipeline.py:8198`). The cluster is one
host-import-and-call bridge repeated for the GPU optional-feature dispatch:

```
py_cpy_ensure_init, py_cpy_import, py_cpy_getattr, py_cpy_from_pccstr,
py_cpy_call1, py_cpy_getattr, 3×(py_cpy_to_pcc_obj + py_cpy_decref)
```

The source pattern (compile_python, ~lines 8269 / 8379 / 8521):

```python
source_contains_gpu_kernel = getattr(<gpu_kernel_module>, "source_contains_gpu_kernel")
prepare_gpu_kernels_for_source = getattr(<gpu_kernel_module>, "prepare_gpu_kernels_for_source")
compile_metal_runtime_bridge   = getattr(<gpu_metal_module>, "compile_metal_runtime_bridge")
```

i.e. `getattr(dynamically_imported_user_module, "fn")(args)`. The legacy
(`ir_scaffold=off`) lowering can't statically resolve a call on a value obtained
via `getattr` of a lazily `__import__`-ed user module, so it bridges through
CPython.

## Isolation (CONFIRMED)

- **Not** the class-attr-override fix landed the same day: forcing
  `ClassLowering.class_attr_overridden_by_subclass` to always return `False`
  (pre-change behavior) yields the *same* 12. The class-attr fix is fully
  exonerated.
- **Both modes**: pipeline.py single-module compile emits **12** in `ir_scaffold=off`
  **and** `on`. So this is NOT a legacy-only artifact.
- **But the real closure is clean**: the multi-file `ir_scaffold=on` /
  `--python-libpython=off` three-stage bootstrap (`test_pcc_bootstrap_full_gc0`)
  is green and byte-identical, and the baseline's own headline criterion is the
  multi-file closure. The 12 appear in *single-module* per-module compilation,
  where `pcc.gpu_kernel`/`gpu_metal` are absent from the unit so the GPU-dispatch
  calls can't be statically resolved and bridge through CPython. In the full
  closure those modules are present and resolve natively.
- **Trigger is specific**: a faithful minimal
  `pkg=__import__("pcc.gpu_kernel"); fn=getattr(getattr(pkg,"gpu_kernel"),"src_contains"); fn(s)`
  emits **0** in both modes. So the bridge needs compile_python's actual shape —
  `getattr()` on a module object returned from a *helper call*
  (`_load_pcc_gpu_kernel_module()` -> DynType), then a call on that — not a
  directly-chained getattr. (Likely a type-inference resolution difference for
  the call-returned DynType module.)

## Ownership / timing — UNATTRIBUTED (do not assume)

Prior `/loop` rows in `docs/current-goal-state.md` (factorial, float(str),
bytes.split/partition — all 2026-06-26) record "fallback baselines 18 passed",
i.e. this gate was green earlier this session. It is now red. The class-attr fix
is exonerated (forced-False proof), but whether an *earlier* 2026-06-26 frontend
edit shifted the type-inference resolution of the GPU getattr-call is **not
established** — code-path reasoning suggests none of them touch
getattr-call-on-user-module lowering, but that is reasoning, not proof. This
needs a real causality audit (AGENTS.md "Bootstrap regression discipline"),
ideally with git bisection (agent cannot use git here). Do **NOT** recapture the
baseline to hide it.

## Fix direction (not attempted)

Make the legacy lowering resolve `getattr(known_user_module, "name")(args)` to a
direct native call when the module is statically known (it is — `pcc.gpu_kernel` /
`pcc.gpu_metal` are in-tree), instead of the CPython import-getattr-call bridge.
Alternatively confirm whether `ir_scaffold=on` already does this (it appears to —
strict path is clean) and port that resolution into the `off` path, or decide the
`off` legacy path's diagnostic ratchet should track `on`.

## Where to look

- `pcc/py_frontend/pipeline.py::compile_python` GPU dispatch (~8269/8379/8521).
- The frontend call/getattr lowering that decides native-call vs `py_cpy_*`
  bridge for a call on a `getattr` result (`call_expression_lowering.py`,
  `attr_load_lowering.py`, and the `ir_scaffold` on/off branch).
- `tests/fallback_baseline.json` (`_recapture_log`, `per_module`, `on_mode_*`).
