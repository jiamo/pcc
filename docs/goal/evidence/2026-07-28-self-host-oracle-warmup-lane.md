# Self-host oracle cold warmup lane repair

Date: 2026-07-28

Task: `TEST-P0-COLD-SELF-HOST-WARMUP-BUDGET`

Mode boundary: host pytest with repository xdist loadgroup scheduling; focused
cold pcc0-to-pcc1-to-pcc2-to-pcc3 self-backend/no-libpython warmup on Darwin
arm64.

Failure:

- A new `pcc/` source fingerprint correctly selected an empty self-host oracle
  cache.
- The session fixture existed once per xdist worker while artifacts were shared
  through file locks.
- Without a common xdist group, a failed stage owner could be followed by
  another worker retrying the same 600-second stage, and each dependent test on
  a worker reported the cached fixture failure.

Change:

- Every self-host oracle item now shares `pcc_heavy_self` with the reduced
  self-GC/runtime-oracle lane.
- One worker owns the cold stage chain; timeout values are unchanged.

Evidence:

- Scheduler contract: RED with missing `pytestmark`, then `1 passed in 0.09s`.
- Complete infrastructure file: `19 passed in 0.70s`.
- Empty source-key directory `8ecf6e53720705464d83e0da` initially contained
  no pcc1, pcc2, or pcc3.
- Focused cold warmup: `1 passed in 283.89s`, publishing all three stages inside
  the existing per-stage and outer watchdogs.

Supported claim: the known multi-worker fixture retry cascade is removed, and
the current-source cold stage chain passes under existing timeout budgets.

Not proven: the exact complete six-worker non-integration suite or integration
suite. The task remains unfinished until the broad required gate has a final
summary.
