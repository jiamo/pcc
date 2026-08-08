# Evidence: `import numpy` restored on the migrated pcc-Python C-API shim

Date: 2026-08-08
Task: PKG-P0-NUMPY-IMPORT-RESTORE-ON-FREESTANDING-CAPI
Investigation: docs/investigations/pcc1-pip-numpy-runtime-import-capi-regressions.md

## Claim (mode-labeled)

Under **both host pcc and `pcc1`** (the stage-1 self-hosted compiler binary),
`--backend self --python-libpython=off --ir-scaffold=on`, **default runtime mode**
(pcc-Python port archive linked; `PCC_RUNTIME_CC` unset), with
`PCC_HOST_PYTHON=/usr/bin/false`, a compiled app imports **real numpy 2.4.6**
and runs an array add on **all five GC backends**.

What this does NOT claim: it is not a pcc1->pcc2->pcc3 fixed-point result
(stage2/stage3 were not run), and it does not cover Defect 1 (default `auto`
package-acquire mode, still failing closed with `PCC-PKG-ACQUIRE-HASH-REQUIRED`);
the numpy site used here was installed via the host-assisted acquire path.

## Commands and output

```bash
SITE=build/test-package-cache/default-env
printf 'import numpy as np\nprint(np.__version__)\nprint((np.array([1,2,3])+1).tolist())\n' > /tmp/np_main.py
PCC_PACKAGE_SITE=$SITE env -u LC_ALL uv run pcc --backend self \
  --python-libpython=off --ir-scaffold=on /tmp/np_main.py -o /tmp/np_app
for gc in 0 1 2 3 4; do
  PCC_PACKAGE_SITE=$SITE PCC_GC_BACKEND=$gc PCC_HOST_PYTHON=/usr/bin/false /tmp/np_app
done
```

```text
GC0: 2.4.6 [2, 3, 4] (exit=0)
GC1: 2.4.6 [2, 3, 4] (exit=0)
GC2: 2.4.6 [2, 3, 4] (exit=0)
GC3: 2.4.6 [2, 3, 4] (exit=0)
GC4: 2.4.6 [2, 3, 4] (exit=0)
```

Same five results with `pcc1` in place of `uv run pcc`, after
`bash scripts/bootstrap.sh --backend self --stage 1`
(`PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=17837`):

```bash
PCC_PACKAGE_SITE=$SITE build/bootstrap/pcc1 --backend self \
  --python-libpython=off --ir-scaffold=on /tmp/np_main.py -o /tmp/np_app_pcc1
```

## Generic regression (no numpy required)

```bash
gtimeout 900s env -u LC_ALL uv run pytest -q -n0 \
  "tests/python/test_pcc_native_extension_loader.py::test_pcc_native_multiphase_capi_surface_default_runtime"
# 1 passed
```

Fails before the fix, passes after; runs in default runtime mode on purpose,
since `PCC_RUNTIME_CC=cc` links the C sources and masks every port defect.

## Constraints honored

- No C implementation returned to the production link; all 12 fixes are in
  `pcc/py_runtime/py/*.py`.
- No numpy special-casing: every fix is a generic C-API semantic, and the
  regression test uses a synthetic extension.

## Gates run

- five-backend numpy run above: green
- new multiphase C-API regression: green
- `tests/python/test_fallback_baseline.py` + `test_ir_py_fallback_baseline.py`:
  **24 passed, 3 failed** — all three are the single pre-existing HEAD red
  `pcc.py_frontend.pipeline: 14` (`subprocess.run` keyword call), attributed and
  filed separately as FALLBACK-P1-PIPELINE-SUBPROCESS-KWARGS-RESOLUTION /
  docs/investigations/fallback-baseline-pipeline-subprocess-run-kwargs-regression.md.
  Not caused by this work: the failing probe is pure in-process codegen of a
  frontend source and loads none of the edited runtime ports.
- `tests/python/test_pcc_native_extension_loader.py` +
  `tests/python/test_package_extension_abi.py`: **104 passed**
- `tests/python/test_bootstrap_gate_baseline.py`: 2 passed, 2 deselected
- `scripts/bootstrap.sh --backend self --stage 1`: green, and the resulting
  `pcc1` reproduces all five numpy results above
- NOT yet run: the opt-in pcc1 network install gate (Defect 1 territory) and
  the gc0 full stage1->stage2->stage3 bootstrap gate.
