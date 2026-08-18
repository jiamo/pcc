# Summary-worker Stage2: memory confirmed, wall unresolved

Task: `PERF-P1-STAGE2-COORDINATOR-IR-STREAMING`

Date: 2026-08-30 (Asia/Singapore)

## Frozen identities

- Candidate source snapshot:
  `build/native-data-plane-stage1-summary-workers-line-codec-v1/source-snapshot`
- Candidate source manifest SHA-256:
  `c30667c5b06086a20b933c94bb5cd44830047cc40d608c7b7cebe74304c1a4be`
- Candidate pcc1 SHA-256:
  `05ef30372935569167ea6a7a6280d9994855b5e225a299ba61cddaabe14b8e1f`
- Candidate runtime archive:
  `build/native-data-plane-stage1-summary-workers-line-codec-v1/runtime-bundle/libpy_runtime_pcc_py.a`
- Retained V79 comparison:
  `build/native-data-plane-v79-gc0-stage2-resource-v1`

The summary-worker integration is not present in the current worktree. This
evidence concerns the frozen candidate above and does not silently accept or
restore it.

## Canary correction

The first four-module class canary was not a valid discriminator. Both the
candidate pcc1 and accepted V104 pcc1 fail that same input at codegen with
`AttributeError: __init__`; the candidate's four summary workers had already
published their wires. Artifacts:

- candidate:
  `build/native-data-plane-summary-workers-line-codec-canary-v1`
- V104 control:
  `build/native-data-plane-summary-workers-line-codec-canary-control-v104`

A function-only four-module, two-level re-export canary exercises the real
summary-worker path and completes through native link and execution:

```text
artifact                 build/native-data-plane-summary-workers-line-codec-function-canary-v1
compile return code      0
outer wall               18.305 s
peak process-tree RSS    311,508,992 B
summary jobs/parallel    4 / 2
summary nodes/edges      3 / 1
program output           42
linkage                  libSystem only
```

The exact compile command and environment are persisted in that directory's
`process-tree-result.json`; the compiler profile is `compile.profile.json`.
This corrects the old attribution to package `__init__.py` summary handling.

## Complete frozen GC0 Stage2

The exact target command and full environment are persisted in
`build/native-data-plane-summary-workers-line-codec-gc0-stage2-v1/process-tree-result.json`.
It runs the candidate pcc1 with self backend, no libpython, GC0, frontend jobs
10, self-backend jobs 8, both object caches disabled, and the frozen candidate
source/runtime bundle:

```text
bash <frozen-source>/scripts/bootstrap.sh \
  --out-dir build/native-data-plane-summary-workers-line-codec-gc0-stage2-v1 \
  --backend self --from-stage 2 --stage 2 --reuse-stage1
```

Result:

```text
stage result             rc=0, 732.058 s
outer sampled result     COMPLETE, 733.567 s
pcc2 SHA-256             4772435883edb165ad37302197b89de39ca257c6b332d64663baabaf2c534b56
pcc2 executable          --help rc=0
pcc2 linkage             libSystem only
peak process-tree RSS    11,031,330,816 B
largest process observed 4,091,445,248 B
root while largest       3,775,266,816 B
peak process count       26
summary jobs/parallel    218 / 2
summary nodes/edges      4,731 / 7,789
```

The coordinator threshold of at most 8 GB is therefore satisfied: even the
largest process anywhere in the sampled tree stayed below 4.10 GB. This is a
real pcc1 -> pcc2 completion, not a partial/timed-out artifact.

## Time comparison and verdict

The retained V79 cache-off resource run completed Stage2 in 683.796 s
(outer 684.895 s), with 10,856,660,992 B tree peak. Candidate wall is 7.06%
slower and misses the pre-registered `V79 + 5%` ceiling of 717.986 s by
14.072 s. The human reports that ZDB testing was active during this candidate
run, so its wall time is explicitly contaminated and cannot reject the source.

The compute evidence also does not establish a code regression:

```text
                         V79             candidate       candidate / V79
user + sys CPU           3523.362 s      3437.255 s      0.9756
tree RSS                 10.857 GB       11.031 GB       1.0161
summary/export wall      56.375 s        45.013 s        -11.362 s
frontend codegen wall    147.076 s       161.967 s       +14.891 s
native emit wall         355.282 s       386.220 s       +30.938 s
runtime preparation      12.000 s        20.280 s        +8.280 s
```

The candidate retired less aggregate CPU while taking more wall time, and the
comparison was not an adjacent matched pair. Repository evidence rules forbid
calling that a source regression or relaxing the wall threshold from this one
number.

Verdict: `[CONFIRMED]` for the bounded-memory architecture and real GC0 Stage2
completion; `[CONTAMINATED/UNRESOLVED]` for performance acceptance. No
Stage3/fixed-point claim exists. Before changing source, run one adjacent,
cache-off, performance-lock-held V79/candidate Stage2 pair with unique outputs
and the same knobs after machine load is quiet. Accept the frozen integration
only if the candidate stays within 5% wall, below 8 GB per process, produces an
executable self/no-libpython pcc2, and does not regress aggregate CPU.
Otherwise leave it removed and localize the paired phase delta.
