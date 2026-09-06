# Investigation: rollback rejects a broken promoted compiler

## Status
resolved

## Problem Description
Rollback rehashed the active candidate before restoring its predecessor. A
corrupted or missing promoted compiler therefore prevented recovery even when
the recorded predecessor was intact and the stable symlink was unchanged.

## Repro
`test_rollback_recovers_a_broken_candidate_when_predecessor_is_intact` promotes
a temporary fixture, corrupts or moves its new libexec/pcc1, then rolls back.

## Test [CONFIRMED]
The corrupt-binary case failed before the fix with an installed identity
mismatch on the new compiler.

## Proposals
- No.1 Verify the restored predecessor and identify the damaged current version by journal/link [CONFIRMED]

## No.1 Verify the restored predecessor and identify the damaged current version by journal/link
### Code Change
Rollback still fully verifies the predecessor and committed journal binding.
It matches the exact active symlink against the journal's after target without
requiring that damaged payload to pass integrity checks. External or changed
active links remain outside the authorized rollback.
### CONFIRMED
Corrupt and absent new binaries recover. Changed predecessors and an external
link selected after promotion are rejected. Crash-before/after-replacement
history cases remain covered by the focused installer tests.

## Report
All actions were confined to temporary test directories. No installed toolchain
or stable command was changed, and no release qualification is claimed.
