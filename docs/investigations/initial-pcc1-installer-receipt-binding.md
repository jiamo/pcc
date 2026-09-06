# Investigation: initial installer did not bind helper manifests to stage receipts

## Status
resolved

## Problem Description
The initial installer compared copied source files against the current source
manifest without checking that manifest's digest against the successful Stage1
build receipt. Stage2 record fields also lacked checks against the retained
process/result artifacts and the Stage1 source/runtime environment.

## Repro
Replace a source snapshot member and update its source-manifest entry while
keeping build-receipt.json unchanged. The old copy loop treated the replacement
manifest as authoritative. This was a static contract finding during issue #186.

## Test [N/A]
New focused installer regressions cover a valid complete bundle, jointly changed
source/manifest, changed runtime and Stage1 result, changed Stage2 process/result,
and a consistently rewritten Stage2 record selecting another runtime owner.
The former acceptance path was identified by static review.

## Proposals
- No.1 Validate retained stage evidence before creating an installation [CONFIRMED]

## No.1 Validate retained stage evidence before creating an installation
### Code Change
verify_bootstrap reuses the compile A/B build-receipt validator for source,
runtime and Stage1 result identities. It verifies runtime bundle files and
Stage2 command, environment, input/output compiler, result and process evidence.
Copied evidence retains the validated hashes. Path traversal and symlinked
runtime/record members fail closed.
### CONFIRMED
The focused installer cases pass. A read-only validation of retained v84
Stage1/Stage2 receipts succeeds with compiler SHA256
`c1f4342696e9d45b36deb17434160f702f082dd2b558973f2d75964802ab4090`.
That check also exposed the separately fixed producer/validator mismatch in
[stage1-receipt-metric-contract-rejected.md](stage1-receipt-metric-contract-rejected.md).

## Report
Validation still depends on retained stage artifacts. No new compiler build,
installation, promotion or release is proven; the historical installed v84
toolchain was left untouched.
