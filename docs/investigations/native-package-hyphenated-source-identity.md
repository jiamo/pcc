# Investigation: native package installation truncates hyphenated project identities

## Status
active

## Problem Description
The user requires pcc-gui, pcc-gateway and NumPy to install into one consistently
selected environment. Installing the actual gateway source with the fresh
pcc1 reports name=pcc instead of pcc-gateway. The native basename and artifact
project/version helpers split at the first hyphen, and directory identity
ignores pyproject project.name. This can collide unrelated installed packages.
The separate optional-provider build attempt is a different finding.

## Repro
`build/correctness-20260906-a/gateway-install-01.stdout.log` retains the actual
pcc1 install report. A reduced metadata-directory case is:

```sh
gtimeout 60s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 tests/python/test_package_source_identity.py
```

## Test [CONFIRMED]
The reduced test fails: native basename returns checkout while pyproject
project.name is example-tools (1 failed in0.08s). The actual gateway report
has name=pcc. Existing package schema unification is documented in
`package-manifest-schema-wheel-tag-source-of-truth.md`, read before this fix.

## Proposals
- No.1 Preserve directory identity and split archives at the version boundary [pending]

## No.1 Preserve directory identity and split archives at the version boundary
### Code Change
Implemented literal project metadata and artifact filename parsing in the
self-host-safe package schema. All three native name/version entrypoints
consume it; local directories retain their complete basename without metadata.
Unsupported literal expressions and unsafe package identities fail closed.
Wheel field positions remain unchanged.

### Pending
Run minimized name/version tests, existing generic extension/source installation
checks and a pcc1-compiled helper canary before rebuilding the whole compiler.

## Update — focused results

10 identity and invalid-name tests pass in0.12s. The new helper source compiled
and executed through pcc1 in11.60s, producing exact project name/version output.
The first helper-canary attempt imported internal pcc.package_schema as an
application module and reached the existing ImportError boundary. The corrected
harness copies the unchanged helper source as a local module, exercising its
body rather than changing public import policy. This is a helper-language gate;
rebuilt native CLI installation remains pending. Existing wheel/extension
regression checks are running before the next package-build change.

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

## Update — structural metadata parser and fixture identity

Review expanded the literal parser to bound quoted/dotted keys, comments,
composites, multiline strings and table scope. Unrelated NumPy array-table
configuration stays opaque; unsupported relevant metadata rejects instead of
introducing a wrong project name. The exact pre-follow-up helper source
compiled and executed through the receipt-bound older pcc1 in 13.37s, matching
host output for real NumPy metadata and the supported/rejected forms. Receipt:
`build/correctness-20260906-a/package-policy-native-03/verification.json`.
Later policy changes and the final CLI still need fresh native evidence.

The expanded host package suite exposed two old fixtures that inferred
`demo_pkg` solely by stripping `-0.1` from a directory basename. Directory
names now remain complete when metadata is absent. Those fixtures now declare
their intended distribution name in `[project]`, while the new explicit
hyphenated-directory regression retains the full fallback-name assertion.
The host package/build/reinstall packet then passed 80 tests with 9 native
cases deferred; it did not restore the lossy directory-name heuristic.
