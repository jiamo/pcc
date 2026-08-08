# Investigation: fallback-baseline red at HEAD — `pcc.py_frontend.pipeline` 14 py_cpy calls from `subprocess.run(..., check=True)`

## Status
open — found 2026-08-08 while running the fallback gates as the exit criteria
for the numpy C-API work
([pcc1-pip-numpy-runtime-import-capi-regressions.md](pcc1-pip-numpy-runtime-import-capi-regressions.md)).
**Not caused by that work** (attribution below). Successor in kind to
[fallback-baseline-head-regressions-unregistered-closure-modules.md](fallback-baseline-head-regressions-unregistered-closure-modules.md),
which closed a different set on 2026-08-07 with 27 passed; this is a new one
that landed after that fix.

## Problem Description
```bash
gtimeout 900s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
# 3 failed, 24 passed in 270.75s
```
Failing:
- `test_pipeline_static_cross_module_exports_stay_clean`
- `test_per_module_fallbacks_under_ratchet`
- `test_on_mode_per_module_fallbacks_under_ratchet`

All three report the same single module:
`pcc.py_frontend.pipeline: 14 vs ON baseline 0 (+5.0%)`.
`tests/fallback_baseline.json` records `0` for `pcc.py_frontend.pipeline` under
both `per_module` and `on_mode_per_module`.

## Test [CONFIRMED]
In-process probe (no archive, no link step) reproduces it directly:

```python
ast_mod = parse_and_lift(Path("pcc/py_frontend/pipeline.py").read_text(),
                         "pcc/py_frontend/pipeline.py", "pcc.py_frontend.pipeline")
ir = str(L1CodeGen(infer_module(ast_mod), emit_cpy_main_exitcode=False,
                   ir_scaffold_mode="on").generate(...))
```
yields exactly 14:
```text
5  py_cpy_decref     4  py_cpy_from_i64      1  py_cpy_ensure_init
1  py_cpy_import     1  py_cpy_getattr       1  py_cpy_from_pcc_obj
1  py_cpy_call_kw
```
Naming the site from the IR (string constants around the fallback):
```text
%cpy.import.subprocess... = call @py_cpy_import(...)
%cpy.fn.run...            = call @py_cpy_getattr(%cpy.import.subprocess..., ...)
%cpy.callkw.run...        = call @py_cpy_call_kw(%cpy.fn.run..., ...)
```
i.e. **one `subprocess.run(..., check=True)` keyword call in `pipeline.py`
stopped resolving natively** and now bridges through CPython.

## Attribution (why this is not the numpy C-API session's doing)
1. The probe is pure in-process codegen of a *frontend* source. It reads no
   runtime archive and imports none of that session's edited files, which are
   confined to `pcc/py_runtime/py/*.py`.
2. The `.capi_syms` sidecar that session regenerated *is* read by
   `pipeline.py` (~line 8805) — but at **link** time, to decide which C-API
   symbols the archive provides. It is not consulted during codegen, and the
   probe above reproduces the 14 without it. Its diff is additive besides.
3. `git blame` puts the `subprocess.run` call sites at `fe1de470` (2026-06-01)
   and `f49220503` (2026-06-13), months before.
4. HEAD moved during that session (`041b6808` -> `35adf5f0`) from a concurrent
   worktree user; the GUI/mac_diff_app/self-backend-exitcode commits in that
   range are the candidates to bisect.

## Proposals
- No.1 Bisect `pcc/py_frontend/` across the post-2026-08-07 commits for the
  change that made an imported-module keyword call stop resolving statically,
  then restore native resolution (or, if the single-module artifact is the
  documented expected shape per
  [`reference_fallback_baseline_per_module_vs_closure`], make the ratchet
  express that and say so in the baseline).                        [pending]

## Notes
- Per-module ratchet compiles each module **alone**; a red per-module with a
  green strict bootstrap is historically a single-module-resolution artifact
  (an imported module not in the unit), not a real no-libpython regression.
  The strict `--python-libpython=off` numpy build in the linked investigation
  was green throughout, which is consistent with that reading — but the
  baseline recorded `0` here before, so something did change.
- Same "shipped without the fallback gates being run" pattern the predecessor
  investigation calls out.

## Update 2026-08-12 (source-level metric resolution)

The causal audit separated semantic fallback actions from conversion and
reference-ownership plumbing. The linked multi-file closure remains exact-zero;
the standalone compile lacks cross-module resolution and its increased total is
dominated by correct cleanup calls. The per-module ratchet now compares a
fail-closed action classification while retaining total/plumbing diagnostics.
Unknown `py_cpy_*` ABI names are hard failures, and the classifier explicitly
covers the current binop and handle operations. This implements the
investigation's documented single-module-artifact branch without relaxing the
linked-closure zero contract. No fallback suite was executed during the
implementation-only phase, so the investigation stays open pending the listed
gate summaries.
