# BUG-P1-PIPELINE-MODULE-FALLBACK-RATCHET-RED: stale ceilings, not a lowering regression

## The red

`test_fallback_baseline.py::test_per_module_fallbacks_under_ratchet` (OFF) and
`::test_on_mode_per_module_fallbacks_under_ratchet` (ON) were red against the
current worktree on four pipeline modules (standalone-module `py_cpy_*` action
counts, identical in both ir_scaffold modes):

```text
module                                       current   recorded ceiling
pcc.py_frontend.pipeline_context               483      441
pcc.py_frontend.pipeline_frontend_worker_exec   49       19
pcc.py_frontend.pipeline_libpython              78       73
pcc.py_frontend.pipeline_frontend_parallel      52      (unlisted, implicitly 0)
```

The gate had been deselected behind the stale 2026-06-24 repo-root pcc1 and
re-enabled on 2026-08-31 when that binary was rebuilt.

## Attribution (what the actions are)

Mirroring the ratchet's own method (parse_and_lift -> infer -> L1CodeGen
single-module generate -> classify `py_cpy_*` via probe_fallback_categories):

- `pipeline_frontend_worker_execution`: all 49 actions are in
  `run_codegen_worker` and cascade from 7 `py_cpy_import`s of FUNCTION-LOCAL
  cross-module imports (type_infer, codegen.layer1, parse.py_lift,
  pcc.backend.self_backend_aarch64_darwin, pcc.llvm_capi.direct_indexed_kernel,
  pcc.backend.arm64_asm_driver, pcc.backend.native_object) -- the
  deferred-worker / direct-indexed-kernel worker emit and native-object
  encoding path. Each subsequent getattr/call/setattr on those imported
  objects is one dynamic action.
- `pipeline_frontend_parallel`: 52 actions from `import json` in
  `_encode_module_ir_artifact` and a `pcc.py_frontend` fromimport at module
  scope, cascading through `_load_noop_action_result` (30) and
  `compile_parallel_uncached` (13) -- the IR-artifact / noop-action encoding.

Independent single-module compilation has no cross-module export context, so
these sibling-module imports resolve through the CPython bridge there. In the
production multi-module strict closure they resolve natively: the closure-level
strict fallback gate (`-k "closure and not per_module"`, 1 passed) and the
bootstrap gate baseline are green -- zero `py_cpy_*` in the real build.

## Controlled proof it is stale, not regressed

Hypotheses: (H1) the compiler's single-module import resolution regressed;
(H2) the recorded ceilings were captured against an older source and never
re-verified while the gate was deselected.

- The four files are byte-stable since the baseline commit 9dbb1404
  (2026-08-19): identical line counts (1424 / 673 / ...), and no import lines
  were added since.
- Current compiler on the 9dbb1404 source: 483 / 49 / 78 / 52 (= current).
- **9dbb1404's own compiler** (pure `git archive 9dbb1404 pcc scripts` export,
  no worktree change) on 9dbb1404's own source, OFF mode: **483 / 49 / 78 / 52**.
- Same, ON mode: **483 / 49 / 78 / 52**.

The baseline commit's own compiler+source never produced 441/19/73/0, so those
figures were stale (H2). No compiler regression exists (H1 refuted).

## Disposition (per the row: "fix the lowering or justify and recapture")

No lowering was changed or weakened. `tests/fallback_baseline.json`
`per_module_actions` and the ON per-module ceilings for the four modules were
recaptured to 483 / 49 / 78 / 52, with a `_recapture_log` entry naming the
features (direct-indexed-kernel worker emit, native-object encoding,
IR-artifact/noop-action JSON encoding) and this controlled attribution --
following the accepted 2026-08-26 precedent entry, which recaptured
self_backend_* standalone ceilings for the identical
"independent compilation cannot resolve sibling helpers; the strict closure
stays zero" reason.

## Gates

- `test_per_module_fallbacks_under_ratchet` (OFF): 1 passed (238 s) after
  recapture.
- `test_on_mode_per_module_fallbacks_under_ratchet` (ON): green after the ON
  recapture (part of the full run below; before it, the file ran 29 passed with
  only this ON ratchet red).
- Full row gate `test_fallback_baseline.py + test_ir_py_fallback_baseline.py
  -q -x -n0`: **43 passed in 573.50 s**, exit 0 -- every OFF/ON per-module
  ratchet and every closure-level strict test green.
