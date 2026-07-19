# pcc1 native libpython scan: host-parity fix (false PCC-PKG-003 on every pcc-native artifact)

## Claim

pcc1's native `-m pcc.package build-exec`/linkage shim no longer fabricates a
`libpython]` edge on clean pcc-native artifacts. Its detection now mirrors the
host `linkage._LIBPYTHON_PATTERNS` exactly (version digit required after
`libpython`, boundary checks, `-lpython`, `Python.framework`,
`python<digits>.dll`), and the pcc1 report echoes the caller's `include_dirs`
per the host contract. This was NOT a self-host miscompile: pcc1 intercepts
`-m pcc.package` with a native shim in `cli_bootstrap.py`, whose simplified
bare-substring scan matched pcc's own runtime diagnostic literal
`[pcc-native/no-libpython]` — embedded in every artifact linking
`libpy_runtime.a` — flagging every pcc-native artifact as libpython-linked.

## Changes

- `pcc/cli_bootstrap.py`: shared `_native_libpython_match_span()` +
  digit/boundary-aware `_native_text_has_libpython` / `_native_libpython_edge`
  / `_native_libpython_grep_pattern` (has/edge derive from one span finder so
  they cannot drift); report `include_dirs` echoes the caller's list (the
  materialized pcc-capi include dirs stay visible in the compile commands
  only), matching `build_exec.py`.
- `tests/python/test_package_linkage.py`: new
  `test_pcc1_native_libpython_scan_parity_with_host_patterns` (native has/edge
  vs host `_libpython_edges` over a corpus incl. the diagnostic literal and
  boundary/no-digit non-edges); one stale spelling expectation updated to the
  host-regex match (`libpython3.14`).

## Verification [CONFIRMED]

- Required gate (was exit-2 red):
  `tests/python/test_package_build_exec.py::test_pcc1_build_exec_builds_reusable_numpy_capi_provider_without_host_python`
  -> 1 passed against a freshly rebuilt stage-1 pcc1; manual run shows
  `ok=true, links_libpython=false, link_libpython_edges=[], no_libpython_runtime=true`.
- `test_package_build_exec.py` 24 passed; `test_package_linkage.py` 17 passed;
  `test_package_import_path.py -k pcc1_pip_install` 14 passed.
- Diagnosis chain: `docs/investigations/pcc1-linkage-scanner-fabricates-libpython-edge.md`.

## Boundary

- `cli_bootstrap.py` is bootstrap-critical: stage-1 rebuilds were green twice,
  but the full pcc1->pcc2->pcc3 bootstrap gates are still required before
  commit-level completion (scheduled with the end-of-goal full-project
  validation, per the user's defer).
- Edge SPELLING now follows the host regex match (e.g. `libpython3.14`, not
  `libpython3.14.dylib`); gates compare detection, not spelling.
