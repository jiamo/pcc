# Investigation: host and pcc1 package manifests duplicate schema and wheel tags

## Status

resolved

## Problem Description

Host package metadata/install code and the no-libpython pcc1 CLI independently
parsed wheel filenames and independently assembled mode/claim fields. Matching
outputs were accidental rather than enforced by one self-host-safe contract.

## Repro

Source inspection showed three wheel splitters in `pcc/package/metadata.py`,
`pcc/package/install.py`, and `pcc/cli_bootstrap.py`, plus separate host/pcc1
execution-mode and native-claim calculations.

## Test [CONFIRMED]

`test_host_and_pcc1_share_wheel_tag_and_capability_contract` compares host and
pcc1-facing results and source-guards all three callers through the shared
parser. The existing host and current-pcc1 package claim suites gate behavior.

## Proposals

- No.1 Add a self-host-safe package schema module [CONFIRMED]

## No.1 Add a self-host-safe package schema module

### Code Change

Add `pcc/package_schema.py` with the manifest identity/version, wheel parser,
execution-mode mapping, and capability profile. Use it from host metadata,
install/linkage, and pcc1's native JSON paths.

### CONFIRMED

Host tests pass 8/8. A strict self/no-libpython pcc1 rebuilt from current source
passes all 5 pcc1 package mode/install gates. Exact evidence is recorded in
`docs/goal/evidence/2026-07-11-package-manifest-schema-source.md`.

## Report

Proposal No.1 landed. The common module avoids host-only dependencies and was
compiled into pcc1 without libpython fallback. JSON serialization remains
different by necessity (host `json` versus pcc1 hand serialization), but both
consume the same schema values, wheel fields, and capability decisions.

