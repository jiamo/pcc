# PKG-P2-REINSTALL-FASTPATH — host install path short-circuits; the pcc1 mirror does not yet

Mode: host `pcc.package.install`. The pcc1-side native installer in
`cli_bootstrap.py` is a **separate** implementation and is untouched — see
"What is not done" below.

## What was missing

`install_package()` never compared the resolved artifact against what was
already installed. Every re-run redid the whole pipeline: extract, copy into
site, `linkage_report` over every binary, and (with `--build-source`) the
native build. There was no artifact digest in `pcc-package.json` to compare
against — the manifest recorded `source_path` and wheel tags but nothing
identifying the bytes.

## Change

- `_artifact_sha256()` — content digest of a wheel/sdist. Directory sources
  return `None` on purpose: hashing a source tree either costs as much as the
  reinstall it would save, or degrades to a size/mtime approximation that can
  report "already satisfied" for changed content. A wheel is one immutable
  file, so its digest is cheap *and* exact, and that is the only case the fast
  path claims.
- `artifact_sha256` and `install_action` recorded in the manifest.
- `_already_satisfied()` matches on (name, artifact sha256, abi mode) plus a
  liveness check on the recorded payloads and install root, so a manifest
  whose files were deleted does not report success.
- The match happens before `inspect_package` / `_copy_or_extract` /
  `linkage_report` / `_ensure_meson_build_outputs`, i.e. before all the work.
- `force=True` (and `--force` on the CLI) keeps reinstall/upgrade able to redo
  everything.

## Why the test does not use a stopwatch

A timing assertion would pass a fast path that had silently stopped matching
and merely got slower. Instead the second install runs with
`_copy_or_extract`, `linkage_report` and `_ensure_meson_build_outputs`
replaced by functions that fail the test, so skipping them is proven
structurally.

Negative cases are covered too, because a reinstall fast path that fires when
it should not is worse than none: changed bytes under the same filename, a
different abi mode, deleted payloads, and directory sources all still
reinstall.

## Evidence

```text
tests/python/test_package_reinstall_fastpath.py                       7 passed
tests/python/test_package_install.py                                 23 passed
tests/python/test_package_import_path.py                             33 passed
```

## What is not done

The measured 168s case was `pcc1 -m pip install numpy`, and pcc1 runs the
native installer built into `cli_bootstrap.py`, which assembles its manifest
as hand-written JSON text and is a second implementation of this flow. The
fast path above does not reach it. Mirroring it there is the remaining work,
and it needs the pcc-Python constraints (no `dict.get`, no cross-module
constant imports) plus a pcc1 gate — so the 168s number is *not* claimed as
fixed by this slice.
