# Investigation: package campaigns branch on a package-named profile

## Status

resolved

## Problem Description

Host and pcc1 campaign paths contained explicit `numpy-core-l6` comparisons
and independent filename/task/feature mappings. That made an integration
target a package-code special case and allowed the two paths to drift.

## Repro

`rg 'profile (==|!=) "numpy-core-l6"' pcc/package/campaign.py
pcc/cli_bootstrap.py` found root, metadata, area, description, validation, and
selection branches in both implementations.

## Test [CONFIRMED]

The no-special-case gate now rejects equality/inequality branches on the
package-named profile. `test_numpy_core_l6_profile_selects_documented_subset`
also compares host and native report area, description, selection rule, and
task counts through the generic registry.

## Proposals

- No.1 Move campaign behavior into the shared capability registry [CONFIRMED]

## No.1 Move campaign behavior into the shared capability registry

### Code Change

Put root parts, default/effective area, description, selection rule, and
file-to-task/feature records in `pcc.package_schema.CAMPAIGN_PROFILES`. Host and
pcc1 paths look up profile data without package-name branching.

### CONFIRMED

Host/source campaign gates, adjacent package gates, and a strict current-source
pcc1 no-host campaign gate pass. Evidence is in
`docs/goal/evidence/2026-07-11-generic-package-campaign-profile.md`.

## Report

Proposal No.1 landed. The legacy profile name remains only as a data-registry
key and CLI input for compatibility; it no longer controls compiler/runtime/
package logic through name comparisons. This does not claim NumPy execution.

