# Five-GC finite long-running workload evidence

Date: 2026-07-17

Task: `G-P0-GCPERF`

## Pinned workload and artifact

Mode: strict no-libpython, self-backed, AArch64 Darwin.

```text
workload: pcc-gc-longrun-churn
rounds: 100,000
live set: 2,048
completed operations per backend: 6,400,000
binary sha256: 6a21700c210f1b9e589ba32b6433ce659b42443226f32272b48b24706ece9713
manifest: docs/goal/evidence/2026-07-16-gc-longrun-results.json
status: MEASURED
```

The successful manifest source digest is
`1fe84ac066b8f7530684bc992386bf60f7cfa0b2d8403a725c338c11a99ef72f`.
The later closure source digest
`075df27f2c311131e6d5f85c2a9d4abe057b6eec7a70ced920254bba71b5e8cb`
produced the same binary SHA-256.  The artifact-identity bridge is recorded in
`docs/goal/evidence/2026-07-16-gc-longrun-final-source-identity.md`.

## Results

Throughput below is computed from the manifest's completed operations and
elapsed milliseconds; all other values are recorded directly by the runtime.

| GC | elapsed ms | ops/s | peak RSS | steady drift | pause count | max pause us | pause sum us | malloc frag | zpage span |
|---:|-----------:|------:|---------:|-------------:|------------:|-------------:|-------------:|------------:|-----------:|
| 0 | 8,381 | 763,632 | 3,194,880 | 0 | 0 | 0 | 0 | 28,237,328 | 0 |
| 1 | 39,867 | 160,534 | 4,030,464 | 0 | 726 | 670 | 146,288 | 23,198,272 | 0 |
| 2 | 11,899 | 537,860 | 4,046,848 | 0 | 949 | 1,099 | 308,885 | 23,197,968 | 0 |
| 3 | 12,973 | 493,332 | 3,948,544 | 16,384 | 0 | 0 | 0 | 27,558,784 | 0 |
| 4 | 16,304 | 392,542 | 8,880,128 | 0 | 0 | 0 | 0 | 30,158,224 | 770,048 |

GC1's 726 pauses were 724 below 1 ms and 2 below 100 us.  GC2's 949
pauses were 948 below 1 ms and 1 below 10 ms; none reached 10 ms.  Backends
whose telemetry reports zero pauses are not interpreted as proving the
absence of every possible stop outside this instrumented workload.

GC4 ended with `zpage_capacity_bytes=770,048`,
`zpage_used_bytes=265,056`, and `zpage_free_capacity_bytes=4,096`.  Its steady
RSS drift was zero for this 6.4M-operation run.  This is the task's explicit
finite bounded-retention result.  It does not prove that retained spans can be
returned to libc: the direct release experiment remains unsafe because stale
addresses can outlive owner-index membership.

## Correctness gates

```text
entire five-GC production contract
168 passed in 55.65s

strict fallback baselines
25 passed in 263.48s

final shared five-GC self-backend bootstrap matrix
5 passed in 1500.11s (0:25:00)
```

The bootstrap matrix was run once and shared with other closures.  No full GCC
suite was run.

## Claim boundary

This closes one finite, source/artifact-bound long-running workload with all
required axes and semantic parity.  It is a profile, not a collector ranking,
and does not claim cross-machine results, universal steady-state behavior, or
safe physical reclamation of every GC4 retained span.
