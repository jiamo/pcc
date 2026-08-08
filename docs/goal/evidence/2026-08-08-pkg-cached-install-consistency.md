# Evidence: cache state no longer changes the outcome of an install

Date: 2026-08-08
Task: PKG-P1-CACHED-SDIST-MESON-FALLBACK

## Defect

`pcc1 -m pip install numpy` produced a different result depending only on cache
state, on the same machine:

- fresh acquire: builds from sdist, `install_success: true`
- cache hit: `meson_setup blocked`, diagnostic `PCC-PKG-MISSING-MESON`,
  `install_success: false` — **even though the payload was copied into the
  target correctly**, so the failure was purely a reported one.

## Root cause

`_native_build_install_source_json` already had the right short-circuit
(`reason: existing_build_outputs`), but its guard
`_native_has_installable_meson_payload(name, source)` only inspected
`<source>/build/pcc-package/meson-build/<name>/` — the shape the *fresh* build
path leaves behind. A cached source tree has a different shape: it **is** the
built importable payload (`numpy/__init__.py` + `numpy/_core/*.pcc3-pcc_native-*.so`)
and carries no build system at all. The predicate could not see it, so the
installer re-entered the meson configure path on a tree with no `meson.build`
graph, found no `meson` on PATH, and recorded `blocked`.

The first diagnosis ("the cached path forgot the sdist's vendored-meson
fallback") was wrong and was reverted: the cached tree is not an sdist root, so
that fallback never applies there. `pcc/cli_bootstrap.py`'s shared
`_native_build_exec_json` is untouched.

## Fix

One predicate, one place: a source tree that has the package `__init__.py` and
already contains native artifacts is an installable payload.

## Verification

Clean cache, same spec installed twice:

```bash
pcc1 -m pip install numpy --target /tmp/s2a --cache-dir /tmp/c2   # exit 0
pcc1 -m pip install numpy --target /tmp/s2b --cache-dir /tmp/c2   # exit 0
```

```text
run1 | ok: True | install_success: True | resolved_from: index-url | build reason: None
run2 | ok: True | install_success: True | resolved_from: cache    | build reason: existing_build_outputs
```

The cache-installed payload is functional, not just present — compiled with
`pcc1` against `/tmp/pccnp_site5` under strict no-libpython:

```text
GC0: 2.4.6 [2, 3, 4] (exit=0)
GC4: 2.4.6 [2, 3, 4] (exit=0)
```

## Regression

`tests/python/test_package_extension_abi.py::test_cached_built_source_tree_counts_as_installable_meson_payload`
asserts both directions: an unbuilt tree is still built (not silently skipped),
and a tree with artifacts is recognized as an existing payload.

## Gates

- `gtimeout 1200s env -u LC_ALL uv run pytest -q tests/python/test_package_extension_abi.py` — **16 passed**
- `env -u LC_ALL uv run python scripts/goal_state.py validate` — OK
