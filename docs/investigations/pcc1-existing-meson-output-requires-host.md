# Investigation: existing pcc1 Meson output incorrectly requires host Python

## Status

resolved

## Problem Description

After the native file-open exception contract was repaired, the original
compiled-pcc1 package test stopped returning `None` and exposed a second
failure. A local source tree already contained a matching importable payload
under `build/pcc-package/meson-build/demo_pkg`, but the pcc-native install path
unconditionally invoked `PCC_HOST_PYTHON -m pcc.package.build_exec`. With
`PCC_HOST_PYTHON=/usr/bin/false`, installation returned a structured
`host_build_backend_failed` report and exit status 2 instead of installing the
existing output. The predecessor failure is documented in
[native-file-open-null-without-exception.md](native-file-open-null-without-exception.md).

## Repro

```bash
gtimeout 300s env -u LC_ALL PCC_REQUIRE_CURRENT_PCC1=1 uv run pytest -q -n0 \
  tests/python/test_package_install.py::test_pcc1_package_install_writes_manifest_without_host_python
```

After the predecessor fix and before this fix, the subprocess emitted
`reason: host_build_backend_failed`, `host_assisted: true`, and `ok: false`,
then exited 2.

## Test [CONFIRMED]

The compiled-pcc1 failure above was observed on 2026-07-22 after a current
stage1 rebuild. A host-level focused regression also directly exercises the
same build-report selector with host Python set to `/usr/bin/false`.

## Proposals

- No.1 Treat a matching existing Meson payload as a completed build [CONFIRMED]

## No.1 Treat a matching existing Meson payload as a completed build

### Code Change

Before delegating a pcc-native Meson source build, inspect the managed build
root for a directory whose normalized name matches the distribution and which
contains an importable package payload. Return an explicit
`build_backend: existing`, `host_assisted: false` report only for that shape.
Sources without a matching payload continue through the bounded host-assisted
builder.

### CONFIRMED

The focused selector regression passes:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_install.py::test_native_pcc1_existing_meson_outputs_do_not_require_host_python
```

Observed together with the native-open regression: `2 passed in 0.64s`.
Current-pcc1 and broader package gates remain before closure.

## Report

No.1 preserves the existing managed build payload as the install source and
avoids a false host-build requirement. It remains conservative: unmatched or
non-importable build directories still invoke the bounded host-assisted build
backend. A rebuilt current pcc1 passed the complete original package scenario
in 6.65s.
