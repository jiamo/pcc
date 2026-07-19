# Investigation: five-GC matrix timeout contract drift

## Status

resolved — strict backend and stage2 manifests produced a final five-test summary

## Problem Description

The final `V-P1-VAL` five-GC bootstrap matrix was started with the task row's
900-second aggregate watchdog.  The run completed GC0 and reached GC1 stage3,
but the aggregate watchdog expired before a second pytest result.  There was no
pytest summary, so the run is diagnostic evidence only.

This is not a git/staging delay and current evidence does not reproduce the
previous thousand-second native-emitter regression.  The five files are
deliberately assigned to one `xdist_group` by `tests/python/gc/conftest.py`, so
their stage2/stage3 chains execute serially.  The timeout in the task row and
AGENTS example had drifted below the already calibrated authoritative
head-truth timeout.

## Repro

```text
gtimeout 900s env -u LC_ALL uv run pytest -q \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py

one pytest dot, then exit 124 at 15 minutes; no pytest summary
```

The watchdog reaped the process group.  A subsequent exact process scan found
no surviving pytest, bootstrap.sh, pcc1, pcc2, or pcc3 child.

## Test [CONFIRMED]

Profiles produced before timeout:

```text
GC0 stage2 wall: 160.836s
GC0 stage3 wall: 161.007s
GC1 stage2 wall: 266.734s
GC1 stage3: active for about four minutes when aggregate watchdog expired
```

The GC0 result is near the post-repair baseline recorded in
`self-bootstrap-146-module-ir-emission-regression.md` (142.936s and 142.930s
for stage2/stage3; one full backend passed in 287.69s).  It does not justify a
new semantic/runtime edit.

The contract mismatch is direct:

- `scripts/head_truth_manifest.py`: 1800 seconds for `self-five-gc-bootstrap`;
- `docs/goal/head-truth-manifest.json`: 1800 seconds;
- old `V-P1-VAL` required gate: 900 seconds;
- old AGENTS example: 700 seconds.

## Proposals

- No.1 Align local task/startup contracts with the measured head-truth envelope [CONFIRMED]
- No.2 Run one corrected-envelope matrix and do not repeat it blindly [CONFIRMED]
- No.3 Treat a second timeout as a measured performance blocker [CONFIRMED]
- No.4 Add content-addressed complete-backend resume [CONFIRMED]
- No.5 Add content-addressed stage2 resume and close the aggregate gate [CONFIRMED]

## No.1 Align local task/startup contracts with the measured head-truth envelope

### Code Change

Set the AGENTS five-GC example and `V-P1-VAL` required gate to the already
authoritative 1800-second aggregate timeout.  Do not change internal
per-stage semantics, xdist grouping, GC behavior, or compiler code.

### CONFIRMED

The two local contracts now match `scripts/head_truth_manifest.py` and the
checked head-truth manifest.  Task-board validation is the focused gate.

## No.2 Run one corrected-envelope matrix and do not repeat it blindly

### Code Change

None.  Run the exact matrix once with the corrected watchdog.  A final pytest
summary is required for green evidence.

### CONFIRMED

The corrected run emitted three pytest dots, proving complete GC0, GC1, and
GC2 fixed points, then timed out during GC3 stage2 at 1800 seconds.  GC4 had
not started.  There was again no pytest summary, and a post-timeout process
scan again found no surviving pytest/bootstrap/pcc child.

Complete profile wall totals from that run:

```text
GC0 stage2 + stage3: 169.982s + 175.656s = 345.638s
GC1 stage2 + stage3: 288.726s + 307.811s = 596.537s
GC2 stage2 + stage3: 339.558s + 303.819s = 643.377s
```

Those three completed backends alone consume 1585.552 seconds.  The remaining
214 seconds were insufficient for GC3 stage2; GC4 was necessarily unreachable
under the 1800-second serial envelope.

## No.3 Treat a second timeout as a measured performance blocker

### Code Change

If the corrected run times out, preserve all completed stage profiles, reap
the process group, and compare phase totals.  Do not widen the timeout again or
change GC/compiler semantics without a new evidence-backed performance slice.

### CONFIRMED

The corrected-envelope timeout is now the second independent aggregate
timeout and provides phase/result profiles for the completed backends.  Merely
raising the watchdog would repeat 26+ minutes of already-proven work and would
not address the user's reported gate cost.

The next slice must choose and prove one of two non-weakening designs:

1. reduce first-clean per-backend compile cost enough for the current 1800s
   aggregate contract; or
2. add a restart-safe, same-source/mode, content-addressed per-backend result
   manifest so an interrupted aggregate gate validates and resumes completed
   fixed points instead of rebuilding them.

Either design must still end in one aggregate five-backend pytest summary,
retain pcc2/pcc3 normalized identity and no-libpython checks for every backend,
and reject stale, partial, mismatched-mode, or timed-out artifacts.

## No.4 Add content-addressed complete-backend resume

### Code Change

Add an atomic complete-backend success manifest to
`tests/python/test_pcc_bootstrap_full.py`.  Its input fingerprint covers source,
shared pcc1, runtime archive, platform/machine, GC backend, and bootstrap mode.
Its output fingerprint covers all three binaries and both successful stage
result records.  Validation rechecks no-libpython and normalized pcc2/pcc3
identity.  Forced rebuild, partial results, source/mode changes, or output
tampering must miss.

### CONFIRMED

Focused tests covered complete reuse, output tamper, source mismatch, missing
stage3 result, timed-out stage3 result, and forced rebuild.  The first resumed
matrix revalidated GC0/1/2, completed GC3, and then reached GC4 stage3 before
the aggregate watchdog expired.  Four complete backend results survived; GC4
did not receive a false complete manifest.  The watchdog reaped the process
group and the residual scan was empty.

## No.5 Add content-addressed stage2 resume and close the aggregate gate

### Code Change

Add a second atomic checkpoint for a successful stage2.  In addition to the
same source/mode inputs it records the exact frontend/self-backend job plan and
CPU IDs, pcc1/pcc2 hashes, and the successful stage2 result record.  On a hit,
clear only stale stage3 profiles and invoke `bootstrap.sh --from-stage 3`.

### CONFIRMED

The focused cache/scheduling gate passed:

```text
10 passed, 6 deselected in 0.25s
```

It covers execution-plan drift and directly proves that a verified stage2 hit
invokes only stage3.  GC4's already successful stage2 was accepted by the new
validator with the measured plan (`frontend_jobs=4`, `self_backend_jobs=12`,
CPU IDs 0..11).  The closing aggregate run emitted four cache-validated dots,
performed only GC4 stage3, and completed:

```text
5 passed in 368.45s (0:06:08)
```

The final residual process scan was empty.

## Report

This investigation denied both easy stories: the first 900-second timeout was
not a compiler correctness regression, and aligning it to the previously
authoritative 1800-second timeout was still insufficient.  The actual measured
boundary was serial aggregate cost: GC0/1/2 alone took 1585.552 seconds.

The resolved design does not weaken or skip bootstrap evidence.  It moves the
restart boundary to content-addressed, revalidated backend/stage completion so
an aggregate interruption no longer throws away 20+ minutes of proven work.
The required aggregate now has a final five-test summary, and `V-P1-VAL` may be
promoted.  A cold five-backend run is still not claimed to finish within 1800
seconds; reducing clean compile cost remains separate performance work.
