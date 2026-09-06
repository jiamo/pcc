# Investigation: optional package C source data is inferred as an extension

## Status
active

## Problem Description
Installing the real pcc-gateway checkout tries to compile its optional
OpenSSL provider into a Python extension, although the project declares the
standard Hatchling Python wheel backend and no build hooks. It fails on
openssl/crypto.h and prevents the ordinary source package from installing.
Any single C file under an import package currently becomes an inferred
extension target. This is separate from the hyphenated-name defect.

## Repro
`build/correctness-20260906-a/gateway-install-01.*` records the fresh pcc1
attempt, including the C compile action and missing-header diagnostic.
The reduced test uses a normal Hatchling package with an optional C data file:

```sh
gtimeout 60s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 tests/python/test_package_declarative_source_install.py
```

## Test [CONFIRMED]
The reduced native installer test fails (1 failed in0.20s) because it calls
the C build function for the optional data file. The positive generic
setuptools/PyInit extension gate still passes before changing this policy.

## Proposals
- No.1 Respect a declarative Python packaging backend before inferring C targets [pending]

## No.1 Respect a declarative Python packaging backend before inferring C targets
### Code Change
Pending: share a conservative Hatchling/no-hook source-build classification
between host and native installers. Preserve C sources as package data, do not
execute build hooks or claim full PEP517 equivalence. Custom backend paths,
hooks and separate hatch configuration do not earn this shortcut. Meson/native
extension paths retain their existing build gates.

Reference: https://hatch.pypa.io/latest/config/build/ documents explicit build
hook configuration and file-selection contracts; https://hatch.pypa.io/latest/plugins/builder/wheel/
documents the wheel builder. This change is a pcc native source-overlay decision,
not a claim to implement every Hatch wheel transformation or arbitrary hook.

### Pending
Run native/host source-overlay tests, negative hook cases, the existing real
C-extension regression, a pcc1-compiled policy canary, then a rebuilt CLI against
the real external packages.

## Update — current shared package packet

The current host/native-helper packet passed21 tests in1.68s, preserving the
real setuptools/PyInit C-extension gate. Actual host source installation of
pcc-gateway and pcc-gui then passed readback of all19 and18 Python source files
respectively, under the same selected pcc environment. Build actions were empty
with reason declarative_python_source. Receipts are
`build/correctness-20260906-a/pcc-gateway-host-install-v2.json` and
`pcc-gui-host-install-v2.json`. Native CLI installation is still pending a fresh
compiler; helper canaries do not substitute for that boundary. Work is tracked
in https://github.com/allstoalls/pcc/issues/186.

## Update — build ownership review and archive parity

The declarative predicate alone was insufficient. Review reproduced two more
ownership gaps: an unknown declared Python-only backend returned success with
no build, and host installation of a ZIP source archive bypassed a declared
Hatch hook because only directories reached the build-policy check. The
smallest tests failed in 0.11s and 0.13s respectively; native archive hook
rejection already worked.

The shared policy now requires an owner for unknown declared PEP 517 backends,
retaining the existing setuptools/Meson routes. The host installer prepares
ZIP/tar source trees before applying the same directory policy and keeps that
tree alive through build and payload publication. Wheel handling and the
original archive hash/source path are preserved; reinstall reuse is checked
before extraction. Unsupported metadata and hooks reject before writing the
installation site. Ordinary source overlays are still a bounded package
installation contract, not full Hatch/PEP 517 build equivalence.

`package-build-scope-05.stdout.log` and its JSONL report under
`build/correctness-20260906-a/` record 80 passed, 9 deselected in 2.78s.
The selected host packet covers successful ZIP/tar declarative installs,
hook/unknown-backend rejection, Meson build ownership, package selection and
reinstall identity. The 9 pcc1 cases were deferred explicitly until the final
compiler exists; they are not green evidence. Current native CLI and full
release qualification remain open.
