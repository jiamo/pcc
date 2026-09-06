# Investigation: package dependency discovery consumes development environments

## Status
active

## Problem Description
A local gateway install plan reports unrelated build, black, mypy and tox
requirements from its development environment. Host discovery recursively
rglobs every METADATA/PKG-INFO, and the native mirror likewise walks all nested
directories. This mixes the artifact's requirements with development packages
and recursively expands an unrelated dependency graph.

## Repro
`build/correctness-20260906-a/package-plan-diagnostic.*` records a15.29s host
plan and repeated five-second stack samples inside artifact_requires_dist and
artifact_requires_dist_diagnostics. The result includes foreign build/black/
tox diagnostics despite this being the gateway source project.

## Test [CONFIRMED]
`tests/python/test_package_metadata_scope.py` supplies one legitimate egg-info
record plus fake .venv/build metadata. Before the candidate, the first test
fails with both foreign-dependency and build-only-dependency in the requirements
(1 failed in0.10s). Root/src layouts and a single unpacked sdist wrapper are
positive coverage for the required existing layouts.

## Proposals
- No.1 Share bounded artifact metadata locations between host and pcc1 [pending]

## No.1 Share bounded artifact metadata locations between host and pcc1
### Code Change
Candidate package_metadata_paths.py locates metadata directly at the artifact
root, immediate dist-info/egg-info directories, src layout and one unpacked
sdist wrapper. It never recursively descends into virtual environments, build
caches or vendored projects. Both host requirement readers and the native
reader consume this same list. Bounded traversal also avoids symlink recursion;
this does not claim a complete general dependency resolver or PEP517 frontend.

### Pending
Run focused metadata/name/build regressions, repeat the real host plan, and
compile/execute this filesystem helper with pcc1 before a fresh CLI build.

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

## Update — sole child directories are not sufficient wrapper evidence

### Repro [CONFIRMED]

The bounded scan still adopted foreign requirements when an artifact contained
`app.py` and a single `vendor/foreign.dist-info/METADATA` file. Both
`artifact_requires_dist()` and the host-executed native helper returned
`foreign-dependency`; the archive-member selector chose the same foreign
metadata. Having only one child directory did not establish that the directory
was an unpacked sdist wrapper.

The new regression failed before the fix:

```bash
gtimeout 30s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest \
  'tests/python/test_package_metadata_scope.py::test_direct_module_does_not_adopt_vendor_requirements[directory]' \
  -q -x -n0 --tb=short
```

Result: one failure in 0.10s, with the foreign dependency in both readers.

### No.2 Require a marked source wrapper and preserve root ownership [CONFIRMED]

The filesystem and archive-member policies now require a direct `PKG-INFO`,
`pyproject.toml`, `setup.py`, or `setup.cfg` inside the sole candidate wrapper.
A direct importable module at the artifact root also prevents wrapper
promotion, including when that child has its own valid project metadata.
The shared filename predicates include Python modules/stubs and native module
suffixes already recognized by the package installers. Existing direct-root
and `src` metadata still wins before wrapper discovery.

### Result and remaining boundary

```bash
gtimeout 30s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest \
  tests/python/test_package_metadata_scope.py -q -x -n0 --tb=short
```

All 26 cases passed in 0.47s. The packet exercises directory, ZIP and tar
inputs, unmarked child rejection, Python/native module ownership, and all
three project-config markers for valid `src` metadata in wrappers. Archive
members with leading `./` and extracted filesystem layouts agree. This is
host-executed helper evidence; no compiler or bootstrap ran. The updated helper
still requires the fresh pcc1 execution gate before a native-CLI claim.
