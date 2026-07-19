# Five-GC longrun final-source artifact identity

Date: 2026-07-16

## Result

The final compiler source used for closure has source digest
`075df27f2c311131e6d5f85c2a9d4abe057b6eec7a70ced920254bba71b5e8cb`.
Its cached strict no-libpython/self-backend `longrun_churn` binary is:

```text
/Users/jiamo/.cache/pcc/gc-longrun/075df27f2c311131e6d5/longrun_churn
sha256 6a21700c210f1b9e589ba32b6433ce659b42443226f32272b48b24706ece9713
```

The successful five-backend manifest at
`docs/goal/evidence/2026-07-16-gc-longrun-results.json` was produced from
source digest
`1fe84ac066b8f7530684bc992386bf60f7cfa0b2d8403a725c338c11a99ef72f`
and records the exact same binary SHA-256:

```text
6a21700c210f1b9e589ba32b6433ce659b42443226f32272b48b24706ece9713
```

The intervening frontend fix changes nested-function object caching.  The
workload has no nested functions (only a class initializer and top-level
`main`), and the byte-identical native artifact proves that the executed
workload code and runtime archive did not change.

## Verification commands

```bash
gtimeout 30s env -u LC_ALL uv run python -c \
  'from scripts.run_gc_longrun_gate import source_digest; print(source_digest())'
gtimeout 10s shasum -a 256 \
  /Users/jiamo/.cache/pcc/gc-longrun/075df27f2c311131e6d5/longrun_churn
```

## Re-run contamination record

A final-source rerun completed GC0 in 8.227 seconds, then its per-backend
60-second watchdog expired during GC1 and the gate exited 2.  At that time the
machine had unrelated sustained load: a VM process used about 74.7% CPU,
WindowServer about 41.7%, iTerm about 38.9%, and load averages were
3.70/4.70/4.53.  No `longrun_churn`, runner, pytest, bootstrap, or pcc child was
left behind after the timeout.

That interrupted run is not recorded as performance evidence.  Repeating the
same byte-identical workload under known external contention would add noise,
not strengthen the measurement.  The successful manifest remains the metric
source, and this document is the auditable final-source-to-exact-artifact
bridge.

## Claim boundary

Binary identity carries the existing performance observations to the final
source state for this exact workload artifact.  It is not a fresh timing run,
does not erase the recorded external contention, and does not generalize the
measurements to other workloads or machines.
