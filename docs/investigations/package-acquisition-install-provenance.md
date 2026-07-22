# Investigation: acquired package install loses index provenance

## Status

resolved

## Problem Description

The compiled pcc1 owned-acquisition path correctly recorded its Simple API URL,
hash, cache path, and acquisition mode. It then passed only the immutable local
artifact path to the installer. The installer re-resolved that path as a direct
local spec and emitted `resolved_from: direct`, losing the real index origin.
This was the third independent boundary exposed while restoring
`test_pcc1_package_install_writes_manifest_without_host_python`; its preceding
Meson-output failure is tracked in
[pcc1-existing-meson-output-requires-host.md](pcc1-existing-meson-output-requires-host.md).

## Repro

```bash
gtimeout 300s env -u LC_ALL PCC_REQUIRE_CURRENT_PCC1=1 uv run pytest -q -n0 \
  tests/python/test_package_install.py::test_pcc1_package_install_writes_manifest_without_host_python
```

Before the provenance handoff change, every install operation succeeded, but
the final assertion observed `direct` instead of `index-url` in
`pip_index_plan["installs"][0]["resolved_from"]`.

## Test [CONFIRMED]

The current-pcc1 failure was observed on 2026-07-22 after the native-open and
existing-Meson-output fixes. The command reached the owned HTTP acquisition,
installed the wheel, and failed only at the provenance assertion.

## Proposals

- No.1 Carry acquisition provenance alongside the immutable artifact path [CONFIRMED]

## No.1 Carry acquisition provenance alongside the immutable artifact path

### Code Change

Track acquired artifact paths and their `index-url` origin across local
dependency ordering, then pass an explicit origin override into the install
manifest builder. Apply the same contract to the host Python pip shim so both
frontends retain acquisition truth while continuing to report the actual local
cache path separately.

### CONFIRMED

The host acquisition/install regression and existing-Meson selector pass:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_package_network_acquisition.py::test_pip_install_owned_acquires_then_installs_generic_package \
  tests/python/test_package_install.py::test_native_pcc1_existing_meson_outputs_do_not_require_host_python
```

Observed result: `2 passed in 0.73s`. Current-pcc1 and broader gates remain
before closure.

## Report

No.1 retains both facts instead of choosing one: acquisition/install reports
keep `resolved_from: index-url`, while `artifact_path` and `source_path` remain
the real immutable local cache path. Both the host shim and compiled-pcc1 shim
use the same contract. A rebuilt current pcc1 passed the complete original
package scenario in 6.65s.
