# Freestanding five-GC long-running measurement

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC`

## Mode and command

This is a current-source, strict no-libpython, self-backed AArch64 Darwin
measurement after production GC collector policy moved to freestanding
pcc-Python objects.

```text
gtimeout 360s env -u LC_ALL PCC_GC_LONGRUN=1 \
  uv run python scripts/run_gc_longrun_gate.py \
  --rounds 100000 --backends 0,1,2,3,4 \
  --build-timeout 180 --backend-timeout 180 \
  --output build/freestanding-gc-longrun-results.json --quiet
```

Pinned identity:

```text
workload: pcc-gc-longrun-churn
rounds: 100,000
live set: 2,048
completed operations per backend: 6,400,000
source sha256: 176a688cee3e69a0f6e4ce58a088ffa5831df58edf0e080038e956ee336e0c0d
binary sha256: 4c60c869b2786056e9af1726291f9c5e2fbdad861f6807da80cdf6a12bb737ae
full build manifest sha256: b307e9fb36db08801e8c3395ed0cde74d91c90a2912bdb52e83254061f2bde93
summary: docs/goal/evidence/2026-08-03-freestanding-gc-longrun-summary.json
```

## Current-source results

| GC | elapsed ms | ops/s | peak RSS | steady drift | pauses | max pause us | pause sum us | allocator frag | zpage retained gap |
|---:|-----------:|------:|---------:|-------------:|-------:|-------------:|-------------:|---------------:|-------------------:|
| 0 | 14,428 | 443,581 | 3,915,776 | 0 | 0 | 0 | 0 | 8,376,816 | 0 |
| 1 | 24,336 | 262,984 | 5,521,408 | 0 | 728 | 1,128 | 184,431 | 8,376,816 | 0 |
| 2 | 24,591 | 260,257 | 5,521,408 | 0 | 950 | 2,566 | 499,146 | 8,376,816 | 0 |
| 3 | 25,349 | 252,475 | 5,062,656 | 0 | 0 | 0 | 0 | 8,376,816 | 0 |
| 4 | 159,811 | 40,047 | 9,158,656 | 0 | 0 | 0 | 0 | 8,376,816 | 504,992 |

GC1 recorded 727 pauses below 1 ms and one below 10 ms. GC2 recorded
946 below 1 ms and four below 10 ms. No measured pause reached 10 ms.
Zero telemetry is not a universal claim that an uninstrumented stop cannot
exist.

## Same-workload historical deltas

The comparator is the 2026-07-17 source/artifact-bound measurement in
`2026-07-17-gc-longrun-five-backend.md`. It used the same workload dimensions
but a different source digest and binary, so these are regression signals, not
cross-version collector rankings.

| GC | throughput delta | peak RSS delta | allocator-frag delta |
|---:|-----------------:|---------------:|---------------------:|
| 0 | -41.9% | +22.6% | -70.3% |
| 1 | +63.8% | +37.0% | -63.9% |
| 2 | -51.6% | +36.4% | -63.9% |
| 3 | -48.8% | +28.2% | -69.6% |
| 4 | -89.8% | +3.1% | -72.2% |

All five current runs reached zero steady-tail RSS drift, and the allocator
fragmentation counter fell substantially from the historical source version.
GC4 throughput is nevertheless a material current-source regression signal:
40,047 ops/s versus 392,542 ops/s in the earlier artifact and versus at least
252,475 ops/s for current GC1-3. It is tracked as the separate finite
`PERF-P1-GC4-FREESTANDING-LONGRUN` task. Correctness ownership is not weakened
or moved back to C to improve this number.

## Claim boundary

This records the required RSS, fragmentation, pause, and throughput axes for
all five production backends after the freestanding migration. It proves one
pinned workload on one machine. It does not prove universal service behavior,
cross-version ranking, or acceptable GC4 throughput. The command completed
within its watchdog and no named long-run, bootstrap, pytest, pcc, or pcc1-3
child remained afterwards.
