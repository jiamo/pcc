# Package install-success vs import/native-support separation

Date: 2026-07-08

Task: `PKG-P0-INSTALL-IMPORT-SEPARATION`

Scope:
- The pcc package install manifest now records install and import/native-support
  outcomes as SEPARATE, explicit fields, so an install success can never read as
  an import success or a pcc-native support claim.
- Slice is the in-process install flow (`pcc.package.install.install_package`),
  not the real `pcc1 -m pip install` binary path.

Changed files:
- `pcc/package/install.py` — `install_package(...)` manifest construction
  (additive, no existing keys renamed).
- `tests/python/test_package_install_import_claims.py` — new test file.

Manifest fields added (all distinct):
- `install_success` (bool) = files placed / linkage+build ok, kept separate from
  the existing `ok` key.
- `import_attempted` (bool) = `False`; `import_success` = `None` (tri-state:
  import is a separate un-run gate, never attempted during install).
- `install_native_package_claim` / `native_package_claim` (bool) = `False`
  unconditionally — installing a CPython-ABI / cpython-compat wheel, or merely
  placing a native-looking artifact, is never a pcc-native support claim.
- `linkage_native_package_claim` (bool) = the nested linkage scan's ABI-only
  conclusion. This is deliberately separate from the install claim; a compatible
  native artifact scan can be true while install/import support remains false.
- `wheel_tags` = `{python_tag, abi_tag, platform_tag}` from
  `_wheel_tags_from_name(...)` (None for sdists/dirs — honest). Existing
  `abi_mode`, `linkage.uses_cpython_extension_abi`, and metadata tags untouched.
- No package-name special-casing.

Gates:
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_package_install_import_claims.py::test_pcc1_pip_install_pure_py_fixture_records_install_without_import`
  - passed
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/python/test_package_install_import_claims.py::test_pcc1_pip_install_cpython_extension_fixture_records_wheel_tags_without_native_claim`
  - passed
- Full file: `3 passed` after the 2026-07-09 review fix adding the
  native-artifact/linkage-vs-install claim split regression.
- Regression `tests/python/test_package_linkage.py`: `15 passed, 1 skipped`
- Prose gate ("manifest records install_success, import_attempted,
  import_success, abi_mode, and native_package_claim separately"): verified — all
  five fields present in `install_package` manifest.

## Update 2026-07-08 — pcc1 binary install path closed, promoted DONE_STRONG

The open boundary (real `pcc1 -m pip install` binary path not exercised) is now
closed. `pcc/cli_bootstrap.py::_native_install_manifest_json` (the pcc1
no-libpython native pip-install manifest) emits `install_success`,
`import_attempted` (false), `import_success` (null), `native_package_claim`
(false), and `wheel_tags` at all three emit sites (on-disk manifest, printed
`out`, and the error fallback), reusing the existing `_native_wheel_tag_fields`
splitter (no new parser).

- Build: `gtimeout 580s ... pcc/__main__.py -o build/bootstrap-compat-runner-pcc1/pcc1`
  -> exit 0 (~20s).
- pcc1 gate: `PCC_CURRENT_PCC1=... uv run pytest -q -n0
  tests/python/test_package_install_import_claims_pcc1.py
  tests/python/test_package_abi_mode_labels_pcc1.py
  tests/python/test_pcc1_compat_runner.py` -> `16 passed` (install-import pcc1
  gate + regressions on the abi-labels and compat-runner pcc1 gates).
  The pcc1 binary `-m pip install <wheel> --target <site> --abi=pcc-native`
  records install_success separately from import_attempted/import_success,
  records `install_native_package_claim=false` separately from
  `linkage_native_package_claim`, and keeps native_package_claim false for both
  a pure-py wheel (install_success true) and a cpython-abi wheel
  (install_success false, rejected).

The claim (install success never implies import success or pcc-native support)
now holds on BOTH the in-process installer and the real pcc1 no-libpython binary.
Open boundary empty.

Result: DONE_STRONG (was DONE_WEAK).

Claim: the in-process and pcc1 install manifests separate install success from
import/native-support claims, with CPython-ABI and native-artifact fixtures
recording ABI/linkage facts while keeping `native_package_claim=False` and
`import_success=None`.

Open boundary: empty for local wheel install manifests. Real network package
fetch and real extension import remain separate package/ABI ladder tasks.
