# Investigation: self-host oracle cold warmup cascades across xdist fixtures

## Status

resolved

## Problem Description

A complete non-integration run after a `pcc/` source change reported one
functional failure and 135 setup errors in
`tests/python/test_self_host_oracle_diff.py`. Every error showed a
`pcc1 --python-libpython=off --backend self pcc/__main__.py` subprocess
exceeding the fixture's 600-second per-stage timeout.

The source change correctly invalidated `self_host_source_key()` and selected a
new cold artifact directory. The pcc1/pcc2/pcc3 fixtures are session-scoped per
xdist worker but publish one source-addressed stage chain under file locks.
Because the oracle file had no shared xdist group, several workers could enter
the same fixture path. A failed lock owner was followed by another worker
retrying the same cold stage, while every test on a worker cached that one
session-fixture failure as its own setup error.

This is a test-scheduling cascade, not 135 independent Python semantic
failures. It follows the suite-level lane work in
[`nonintegration-heavy-xdist-lane-oversubscription.md`](nonintegration-heavy-xdist-lane-oversubscription.md)
but owns the self-host oracle fixture boundary separately.

## Repro

The reported complete run ended with:

```text
1 failed, 9444 passed, 135 errors in 2018.61s
```

All 135 errors depended on the shared self-host stage fixtures. Artifact
publication times for source key `df0808920c6659bf3b49ad3c` were:

```text
pcc1  16:56:07
pcc2  17:20:03
pcc3  17:24:34
```

The approximately 24-minute pcc1-to-pcc2 interval is consistent with repeated
600-second fixture attempts before a successful publication.

## Test [CONFIRMED]

The test-infrastructure contract now requires the self-host oracle, runtime
oracle, and reduced self-GC metadata work to share `pcc_heavy_self`. Before the
change it failed with:

```text
AttributeError: module 'tests.python.test_self_host_oracle_diff'
has no attribute 'pytestmark'
```

## Proposals

- No.1 Put every self-host oracle item on the bounded self-heavy lane
  [CONFIRMED]
- No.2 Increase the 600-second per-stage timeout [DENIED]

## No.1 Put every self-host oracle item on the bounded self-heavy lane

### Code Change

Add a module-level `xdist_group(name="pcc_heavy_self")` marker. One worker now
owns the session-scoped cold stage chain, and the warmup remains first in
collection order. After publication, all oracle cases reuse the same immutable
pcc1/pcc2/pcc3 artifacts.

### CONFIRMED

The structural scheduling contract passed, and a genuinely cold source key
`8ecf6e53720705464d83e0da` completed pcc0-to-pcc1-to-pcc2-to-pcc3 under `-n0`:

```text
1 passed in 283.89s
```

The complete infrastructure contract passed `19 passed in 0.70s`. The complete
six-worker non-integration suite was not rerun, so its task-board claim remains
open.

## No.2 Increase the per-stage timeout

### DENIED

The isolated cold chain completes inside the existing limits. Raising the
timeout would hide duplicated builders and CPU contention rather than enforce
single ownership.

## Report

Proposal No.1 is confirmed for the fixture cascade. The self-host oracle now
shares the existing bounded self-heavy lane, and the cold focused stage chain
passes without changing any timeout. Complete-suite closure remains separate
unfinished evidence.
