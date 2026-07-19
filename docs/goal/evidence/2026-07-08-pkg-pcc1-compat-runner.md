# pcc1 --python-libpython=auto compatibility-runner manifest + gate

Date: 2026-07-08

Task: `PKG-P0-PCC1-COMPAT-RUNNER`

Scope:
- The pcc1 CLI (`pcc.cli_bootstrap`) now recognizes `--python-libpython=<mode>`
  before `-m`, routes the module run with that mode, and emits a
  compatibility-runner manifest line for `auto`/`on` while keeping `off` (and
  plain `-m`) as the strict, manifest-free research default.
- This slice proves the mode label / manifest / routing / gate, not a full
  libpython-linked runner executing arbitrary cpython-compat packages.

Changed files:
- `pcc/cli_bootstrap.py` — `_module_request_libpython_mode(argv)` (flag-before-`-m`
  parsing), `compat_runner_manifest(mode)` + `compat_runner_manifest_json(mode)`
  (hand-rolled JSON, no `json` module — pcc1 no-libpython closure safe), and
  `_run_python_module_from_pcc1_with_mode(module_argv, mode)` emit point wired
  into `bootstrap_cli_main`.
- `tests/python/test_pcc1_compat_runner.py` — new (11 host unit cases + 1 pcc1
  binary gate).
- `docs/design/pcc-pcc1-compat-runner.md` — new contract doc.

Manifest (auto/on): `{"requested_execution_mode": "cpython-compat",
"execution_mode": "pcc-native", "python_libpython_mode": "auto",
"allows_libpython_fallback": false, "links_libpython": false,
"native_package_claim": false}`. For `off` the helper reports
`requested_execution_mode="pcc-native"`, `execution_mode="pcc-native"`,
`python_libpython_mode="off"`, `allows_libpython_fallback=false`,
`links_libpython=false`, `native_package_claim=false`, and no manifest line is
emitted. `native_package_claim` is always false. The doc states the current pcc1
binary is built no-libpython: auto/on records a cpython-compat request, not an
actual libpython fallback runner.

Gates (all run by owner):
- Build: `gtimeout 580s env -u LC_ALL uv run pcc --backend self
  --python-libpython=off --ir-scaffold=on pcc/__main__.py -o
  build/bootstrap-compat-runner-pcc1/pcc1`
  - succeeded (~19s with compile cache; 33MB binary). This also proves the new
    cli_bootstrap.py code compiles under the strict no-libpython native subset.
- Binary gate: `gtimeout 300s env -u LC_ALL
  PCC_CURRENT_PCC1=build/bootstrap-compat-runner-pcc1/pcc1 uv run pytest -q -n0
  tests/python/test_pcc1_compat_runner.py`
  - `12 passed` (includes
    `test_pcc1_python_libpython_auto_module_runs_through_compatibility_path`).
- Pre-build in-process smoke of `bootstrap_cli_main(['--python-libpython=auto',
  '-m','pcc.package.inspect','mlx','--json'])`: AUTO -> rc=0 + exactly one
  manifest line; OFF and plain `-m` -> rc=0 + zero manifest lines; module ran.

Result: DONE_WEAK.

Claim: `pcc1 --python-libpython=auto -m <module>` is routed through the
mode-aware module path and emits a manifest with
`requested_execution_mode=cpython-compat`, actual `execution_mode=pcc-native`,
`allows_libpython_fallback=false`, `links_libpython=false`, and
`native_package_claim=false`, verified on a freshly built pcc1 no-libpython
binary; `--python-libpython=off` remains the strict default with no manifest.

Open boundary: this gates the request/actual mode labels, manifest, routing, and
doc. It does not implement or exercise a full libpython-linked runner executing
real cpython-compat ecosystem packages (e.g. actual `import numpy` via
libpython); the module used in the gate is a native pcc1 package handler.
Broader compat-runner execution of real third-party packages remains open
package/ABI work.
