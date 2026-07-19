# PKG ladder slice: pip acquire-delegation contract + pcc1 phantom-install fix

## Claim

`pcc -m pip` now enforces the acquire/build/run contract of
[docs/design/pcc-package-model.md](../../design/pcc-package-model.md): an
unresolvable bare requirement name fails explicitly (`ok:false`, exit 2) with an
`acquire_hint` naming the exact host-tool next step, on BOTH the host shim and
the pcc1 native shim. A pre-existing pcc1 fake-success bug is fixed: pcc1 used
to "install" an unresolved name as an empty phantom site directory with
`ok:true`.

## Changes

- `docs/design/pcc-package-model.md` (new): acquire (delegated to host pip/uv)
  / build (owned, pcc-native via build_exec+meson replay) / run (owned,
  no-libpython loader) three-layer contract; `pcc -m pip` behavior table;
  end-to-end ladder definition.
- `pcc/package/pip_shim.py`: `_acquire_delegation_hint()` — fires only for a
  failed spec that never resolved (`source_path`/`installed_path` absent) and is
  not path-like; resolved-but-failed builds and mistyped local paths do NOT get
  the hint.
- `pcc/cli_bootstrap.py` (pcc1 native shim mirror): same hint fields in the
  install JSON; plus the real fix — `_native_install_manifest_json` returns the
  host-shaped `{"error": "package artifact not found locally or in pcc cache",
  "ok": false, "spec": ...}` early when `source is None` and the spec is not a
  local path, instead of fabricating an empty install root.
- Board: `PKG-P1-NATIVE-EXTENSION-LADDER` TODO_NEEDS_DESIGN -> TODO_READY with
  the design as latest_evidence and a finite remaining ladder (single-command
  E2E gate; second package; Xcode-SDK meson-replay drift noted below).

## Verification [CONFIRMED]

- Host: `pcc -m pip install definitely-not-a-pkg-xyz` -> ok:false + error +
  acquire_hint; `pcc -m pip install ./no-such-dir/x.whl` -> ok:false, NO hint.
  Regressions:
  `tests/python/test_package_import_path.py::test_pip_install_unresolvable_bare_name_gets_acquire_hint`
  and `::test_pip_install_pathlike_spec_failure_gets_no_acquire_hint`
  (`2 passed`).
- pcc1 (fresh stage1 + fresh full-bootstrap binary): same command -> ok:false,
  exit 2, hint present, and NO phantom site directory created (was: empty dir +
  ok:true).
- Existing pcc1 pip surface intact:
  `tests/python/test_package_import_path.py -k pcc1_pip_install` ->
  `14 passed` against the fixed pcc1.
- Full self-host bootstrap green twice with these cli_bootstrap.py edits
  (stage1->2->3; pcc2/pcc3 metadata-normalized byte-identical).

## Boundary / recorded blocker

- This slice does NOT provide the single-command E2E gate (local real source ->
  pcc-native build -> site -> pcc1 import); that remains the ladder's next rung.
- Toolchain drift recorded 2026-07-18: a fresh numpy meson replay fails on the
  current Xcode SDK — numpy's highway_qsort C++ flags set
  `-D_LIBCPP_ENABLE_ASSERTIONS=1`, which the new libc++ rejects
  ("_LIBCPP_ENABLE_ASSERTIONS has been removed"). The prebuilt head-truth site
  (L4/L5) remains usable; the from-cold numpy E2E gate needs this pinned or
  resolved first.
- `pcc-native import X` / local `pip install X` / `cpython-compat` / "PyPI
  support" remain four separate claims.
