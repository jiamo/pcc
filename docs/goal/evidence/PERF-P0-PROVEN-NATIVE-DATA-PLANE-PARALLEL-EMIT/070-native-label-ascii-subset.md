# Native label ASCII subset: v79 prerequisite

Date: 2026-09-06. Status: local source/IR/native gates passed; frozen v79
Stage1 running, whole-PCO and fixed-point qualification pending.

## Why this follows v78

V78 Stage1 succeeds in190.88s /746.10 timed-tree CPU with4.898GB tree peak.
Compiler SHA256: `0ffd49323a52d73ddaa73663ade8b183a52a50ef04180cd61fd242cb85fd99bf`.
All eight real pcc1 reload/fence/generic ABI canaries pass in11.68s.
Only the label driver differs from v77 in the source manifests.

The full same-input PCO pairs preserve exact bytes and lower RSS4.3%.
Control/candidate CPU is15.55/15.54s and15.33/15.47s, with instruction counts
229.559/230.110B and229.360/230.094B. The remaining +0.24–0.32% instruction
signal was kept open. Receipts: `build/native-fragment-v78-pyast-comparison.json`.
No v78 Stage2 was launched.

## One exact subset, both validation boundaries retained

The observed93,250 validator calls per py_ast worker now accept ordinary
ASCII identifiers before entering the existing extended-symbol parser.
ASCII plus isidentifier is a strict subset of the original label grammar;
names containing dot/dollar and all malformed names retain the original path
and exact diagnostics. Validation still occurs before interning and before
label-only publication. No mutable-name cache or validation bypass was added.

Independent review found no grammar/diagnostic/publication finding. The new
test observed red when a plain label entered _is_symbol. An independent
ASCII grammar oracle covers all128 character values in multiple positions,
plus Unicode, whitespace, directives and injected instructions.

## Terminal local gates

- Arena/fragment/grammar:23 passed/0.14s,
  `build/native-label-ascii-fast-host.log`.
- Existing encoder/driver subset:11 passed/1.11s,12 integration nodes
  deselected by the default marker; `build/native-label-ascii-fast-encoder.log`.
  Actual native label execution below covers the changed publication path.
- Full context:1 passed/52.07s,
  `build/native-label-ascii-fast-context.log` and corresponding `-ir/`.
- Actual eight-module self/no-libpython label executable:1 passed/19.16s,
  `build/native-label-ascii-fast-build/pytest.log`. Exact offsets/section
  lengths and malformed-name rejection/close execute. Its source receipt
  differs from the preceding candidate only in arm64_encode.

## Native N/2N cost

All runs hold the repository performance lock, complete rc0 and print exact
outputs. Neither test nor context jobs overlap the measurements.

| Arm | Labels | CPU | Instructions | Max RSS |
| --- | ---: | ---: | ---: | ---: |
| control | 50,000 | 0.15s | 2,467,255,364 | 39,731,200 |
| candidate | 50,000 | 0.13s | 2,205,730,549 | 28,508,160 |
| candidate | 100,000 | 0.26s | 4,365,638,439 | 51,134,464 |
| control | 100,000 | 0.33s | 4,884,981,987 | 73,580,544 |

Incremental instructions per label fall48,355 ->43,198 (-10.7%). Short CPU
readings are centisecond-rounded; exact outputs and deterministic instruction
deltas establish the local result, not an end-to-end speed claim. Receipts:
`build/native-label-ascii-fast-{control,candidate}-n{50000,100000}/`.

## Frozen whole-worker boundary

Source SHA256:
`33ab4dd647d50939e89632c352aa6bf44f912a13b7293083fa49874ca6e3a47d`.
Snapshot: `/private/tmp/pcc-native-fragment-v79`.
Readiness: `build/native-fragment-v79-readiness.json`.
Unchanged GC0/threads-off, frontend7/backend2/link8,8GiB guard and360/410/440s
stage/guard/outer timeouts. Expected Stage1 capacity160–220s uses the measured
v78=190.88s envelope. All source owners are stable.

After Stage1, actual pcc1 canaries and the same complete PCO input must pass
before Stage2. Latest complete Stage2 remains v76=484.762s. Full helper
lists/placeholders, producer/instruction text, normal ASM publication,
verifier/CFG/def-use and Stage2<=Stage1 remain open.

## v79 compiler and full worker readback

Stage1 succeeds190.37s /739.68 timed-tree CPU; process-tree peak5,069,848,576B.
Compiler SHA256: `39519f208a31116bad93e654e64974257089efe5bd94d729c1a2c04ebfea1d40`.
Only arm64_encode changes relative to the v78 source manifest. All eight
actual pcc1 reload/fence/generic ABI canaries pass in11.92s:
`build/native-fragment-v79-pcc1-canaries.log`.

Matched complete PCO pairs (control then candidate, candidate then control):

| Pair | v76 CPU | v79 CPU | v76 instructions | v79 instructions |
| --- | ---: | ---: | ---: | ---: |
| 1 | 15.39s | 15.51s | 229.455B | 229.581B |
| 2 | 15.36s | 15.42s | 229.380B | 229.563B |

All four outputs have exact SHA256
`2f0f6fa3e03c655403a28b0976efc8f33d6234c07519898125f0e846f257dd56`.
Max RSS falls1,121,828,864 ->approximately1,052,800,000B (-6.2%). CPU remains
0.4–0.8% higher and instructions0.05–0.08% higher. This is no speed win;
the near-flat compute envelope plus reduced retained storage permits the
required full-stage structural qualification, whose acceptance stays pending.
No added work is labeled a correctness tax. Receipt:
`build/native-fragment-v79-pyast-comparison.json`.

## Stage2 qualification running

Current source matches the frozen compiler. Readiness:
`build/native-fragment-v79-stage2-readiness.json`. A single GC0 pcc1->pcc2
run now uses `run_pcc_stage2_from_receipt.py`, the shared performance lock,
8GiB cap,600s stage watchdog and prior v76 prediction state. Expected480–560s
is grounded in the complete v76 Stage2/Stage3 envelopes. Output:
`build/native-fragment-stage2-v79/`; live log:
`build/native-fragment-v79-stage2-run.log`.
Stage3 is not launched before successful Stage2. No new full-stage result or
fixed point is claimed while the terminal receipt is pending.
