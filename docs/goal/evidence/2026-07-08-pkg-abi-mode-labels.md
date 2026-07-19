# Package import result cpython-compat vs pcc-native mode labels

Date: 2026-07-08

Task: `PKG-P0-ABI-MODE-LABELS`

Scope:
- Every `linkage_report(...)` result (and each per-artifact `scans` entry) now
  carries explicit execution-mode labels so an A-mode (libpython/cpython-compat)
  compatibility success can never silently promote to a B-mode (no-libpython /
  pcc-native) support claim.
- Slice is the in-process `pcc.package.linkage.linkage_report`, not the no-host
  `pcc1` CLI JSON mirror.

Changed files:
- `pcc/package/linkage.py` — `linkage_report(...)` result enrichment (additive;
  scanners `scan_artifact`/`scan_link_command` stay mode-agnostic).
- `tests/python/test_package_abi_mode_labels.py` — new test file.

Labels added + generic mapping (derived from `abi_mode`, no package special-case):
- `execution_mode` (str), `links_libpython` (bool, kept scan-derived and
  untouched), `native_package_claim` (bool).
- `abi_mode in ("libpython","cpython-compat")` -> `execution_mode="cpython-compat"`,
  `native_package_claim=False` always.
- `abi_mode == "pcc-native"` -> `execution_mode="pcc-native"`;
  `native_package_claim=True` only when at least one artifact/command scan
  exists and the scan set has no libpython edge and no CPython-ABI usage, so an
  empty scan, a `PCC-PKG-004` rejection, or a `PCC-PKG-003` libpython edge keeps
  it `False`.

Gates:
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_package_abi_mode_labels.py::test_libpython_auto_cpython_abi_fixture_reports_cpython_compat`
  - passed
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_package_abi_mode_labels.py::test_libpython_off_cpython_abi_fixture_rejects_with_pcc_pkg_004`
  - passed
- Full file: `2 passed in 0.20s`
- Review fix 2026-07-09: added empty-scan regressions for both host
  `linkage_report(...)` and pcc1 mirror `_native_linkage_json([], [], [],
  "pcc-native")`; both must keep `native_package_claim=false`.
- Regression `tests/python/test_package_linkage.py`: `15 passed, 1 skipped`
- Prose gate ("report includes execution_mode=cpython-compat, links_libpython=true,
  native_package_claim=false for cpython-compat imports"): verified via the
  cp313-cp313 wheel fixture (its `.so` fires both PCC-PKG-004 and PCC-PKG-003).

## Update 2026-07-08 — pcc1 CLI mirror closed, promoted DONE_STRONG

The open boundary (no-host pcc1 mirror `_native_linkage_json` did not emit the
labels) is now closed. `pcc/cli_bootstrap.py::_native_linkage_json` emits
`execution_mode` and `native_package_claim` (top-level + per-scan) with the same
generic mapping as the in-process `linkage_report`. Verified on a freshly built
pcc1 no-libpython binary.

- Build: `gtimeout 580s ... pcc/__main__.py -o build/bootstrap-compat-runner-pcc1/pcc1`
  -> exit 0 (~48s).
- pcc1 mirror gate: `PCC_CURRENT_PCC1=... uv run pytest -q -n0
  tests/python/test_package_abi_mode_labels_pcc1.py` -> `3 passed` (runs the pcc1
  binary's `-m pcc.package.linkage --abi=<mode> --artifact <whl> --json` and
  asserts execution_mode/native_package_claim on both libpython and pcc-native
  modes, PCC-PKG-004 present under pcc-native, plus an empty-scan pcc-native
  report with `native_package_claim=false`).
- In-process regression: `test_package_abi_mode_labels.py` +
  `test_package_linkage.py` -> `18 passed`. `git diff --check` clean.

The claim (every package import result carries cpython-compat vs pcc-native
labels) now holds on BOTH the in-process and the pcc1 no-libpython CLI paths.
Open boundary empty.

Result: DONE_STRONG (was DONE_WEAK).

Claim: the in-process `linkage_report` result carries mode-labeled
`execution_mode`/`links_libpython`/`native_package_claim` fields such that a
cpython-compat import success never reports a pcc-native claim, and a pcc-native
rejection or empty scan keeps `native_package_claim=False`.

Open boundary: empty.
