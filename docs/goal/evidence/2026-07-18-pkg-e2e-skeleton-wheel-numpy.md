# PKG ladder slice: generic package E2E skeleton (wheel) + numpy 缝合 + README repair

## Claim

The pcc-native package pipeline now has ONE reusable E2E proof skeleton, and two
distributions flow through it with zero package-name branching:

1. **wheel 0.45.1 (pure Python, second non-numpy package)** — full pipeline:
   `pcc -m pip install`-equivalent local install (no index/network) into a fresh
   site -> pcc1 `--backend self --python-libpython=off --ir-scaffold=on` compiles
   `import wheel; print(wheel.__version__)` -> binary prints `0.45.1` -> `otool
   -L` shows no libpython/python3.
2. **numpy 2.4.4 (缝合)** — the SAME run layer (`compile_run_assert_no_libpython`)
   against the prebuilt pcc-native numpy-core site: prints `2.4.4` and
   `[2, 3, 4]`, no libpython.

Also: the README numpy 1-2-3 is reproducible again — step 2 (the full
`numpy_head_gate.py run`, loader probe included) was exiting 1 on two real
runtime import-semantics bugs, both root-caused and FIXED the same day (see
below); the gate now passes outright, so no reduced-scope mode was kept.

## Changes

- `tests/integration/pcc_native_e2e.py` (new): skeleton —
  `install_pcc_native` (acquire/build), `compile_run_assert_no_libpython`
  (run layer), `run_package_e2e` (full pipeline). Package-agnostic by
  construction.
- `tests/integration/test_pcc_native_package_e2e.py` (new): env-gated
  (`PCC_RUN_PACKAGE_E2E_INTEGRATION=1`) pinned gates for wheel (full pipeline)
  and numpy (same run layer, prebuilt site).
- `tests/fixtures/packages/wheel-0.45.1-py3-none-any.whl` (+README): vendored
  upstream artifact so the gate is self-contained offline.
- `scripts/numpy_head_gate.py` de-dup: the gate now imports
  `_STALE_TOOLCHAIN_FLAG_REPLACEMENTS` / `_normalize_stale_toolchain_flags`
  from `pcc.package.build_exec` instead of keeping a drift-prone copy
  (deprecated-toolchain-flag handling has a single source of truth for every
  package). A transient `--skip-loader` build-only mode was added while the
  loader probe was red and REMOVED the same day once the underlying bugs were
  fixed; README step 2 is the plain `run`.
- Runtime import-semantics fixes that turned the full gate green (details +
  LLDB evidence in
  [docs/investigations/numpy-loader-probe-cext-reimport-load-once.md](../../investigations/numpy-loader-probe-cext-reimport-load-once.md)):
  cext load-once made PEP 489-faithful (module registered in the load-once
  cache BETWEEN creation and exec; name-keyed like sys.modules;
  `pcc_capi_module_exec` split into from_def/run_exec_slots), and CPython
  parent-before-child package initialization in both import roots
  (`py_compiled_module_ensure_parent_packages` in the compiled-module and
  native-extension importers).

## Verification [CONFIRMED]

- `PCC_RUN_PACKAGE_E2E_INTEGRATION=1 uv run pytest -q -n0 -m integration
  tests/integration/test_pcc_native_package_e2e.py` -> `2 passed` (fresh
  stage-1 pcc1 built the same day, 68s); re-run green after every runtime fix
  below (final run 86.27s).
- README steps re-verified end-to-end the same day: step 1 `bootstrap.sh
  --stage 1` exit 0; step 2 full `numpy_head_gate.py run` exit 0, `status
  PASS`, `entered_pyinit=True`, `entered_py_mod_exec=True`, `compile 137/0`,
  no libpython/LLVM; step 3 verbatim compile+run prints `2.4.4` / `[2, 3, 4]`,
  `otool -L` clean.
- Regressions after the runtime fixes: `tests/test_numpy_head_gate.py` 8
  passed; `tests/python/test_pcc_native_extension_loader.py` 86 passed;
  `tests/python/test_package_build_exec.py -k normaliz...` 1 passed.

## Boundary

- The runtime edits behind the green gate (`py_extension_loader.c`,
  `py_capi_shim.c`, plus the same-day `ImportError.msg` fix) still need the
  commit-level self-host bootstrap gates before DONE_STRONG — deferred at the
  user's direction, must run before commit.
- wheel proves the pure-Python rung; folding numpy's C-core build into the
  single `pcc -m pip install numpy --abi pcc-native` command (replacing README
  step 2's repo script) is the next generic rung.
- Separately discovered the same day (filed, not this slice):
  `BUG-P1-PCC1-LINKAGE-SCANNER-FALSE-LIBPYTHON-EDGE`.
