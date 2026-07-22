# Package ABI UTF-8 source-read closure

## Claim

The package ABI source-contract test is independent of ambient worker locale.
Its repository-source assertions still verify the host/pcc1 wheel-tag and
capability contract, and the complete non-integration suite passes under the
measured watchdog. This does not change package ABI behavior.

## Change

- Specified `encoding="utf-8"` on all three repository-source reads in
  `test_package_abi_mode_labels.py`.
- Kept every existing source-contract assertion intact.

## Evidence

Pre-fix complete suite:

- 9402 passed, 114 skipped, 1 failed in 777.25s. The sole failure decoded
  `cli_bootstrap.py` as ASCII after locale state changed inside its xdist
  worker.

Post-fix gates:

- focused package ABI file: 5 passed in 0.12s.
- complete non-integration suite: 9403 passed, 114 skipped, 1 warning in
  706.50s (11m46s), exit code 0.

Both commands had explicit watchdogs. No assertion or test case was removed,
skipped, or weakened.
