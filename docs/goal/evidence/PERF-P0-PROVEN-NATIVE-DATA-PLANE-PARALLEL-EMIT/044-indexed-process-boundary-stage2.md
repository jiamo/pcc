# 044 — indexed frontend/emit process boundary is exact; v48 scheduling remains under-filled

## Source and focused gates

The source-frozen v48 Stage1 receipt records bootstrap source
`c829534d6d07f6c94d2118639ff92c4dd290d3a1fdf89428f81177e3d568a0dc`
and pcc1
`647e5b6831fe183f5deec68c3d41cea7f02ea213d813eb1719e5351565c82620`.
Stage1 completed in 162.35 s / 667.12 tree-CPU seconds with a 5.036 GB
process-tree peak; the binary links only libSystem.

The sidecar is a versioned JSON-cold/raw-i64 codec for the complete direct
pre-stackprep kernel.  It rejects already-prepared slot/register state, mixes
no arena storage projections, and reconstructs no ParsedBlock or
InstructionRecord graph.  The frontend result is `PIDX`; after that process
exits, a fresh pcc1 publishes either ASM for the established large lanes or
PCO for the established small lane, then the host transition driver rewrites
the result atomically.  A v1 plan retains the old combined behavior; only a
v2 `pidx-pco-v1` plan selects the split.

Focused results before the stage run:

```text
codec/worker/scheduler/inventory packet       58 passed
complete 226-module contextual strict closure PASS
tiny v2 plan -> ASM -> link -> run             prints 42
```

Every retained tiny/py_ast/pipeline/runtime_abi/class_gen/cli_bootstrap PCO or
ASM compared byte-for-byte with its same-source text/host or v41 production
oracle.

## Sizing result

```text
shape             old production lane          split frontend         split emit          max split peak
tiny              0.14s / 0.12GB               0.14s / 0.15GB         0.17s / <0.15GB      0.15GB
py_ast PCO       24.59s / 2.44GB                5.55s / 0.61GB        20.66s / 2.10GB      2.10GB
pipeline ASM     17.67s / 2.67GB                9.30s / 1.24GB         7.67s / 1.39GB      1.39GB
runtime_abi ASM  34.92s / 4.20GB               15.39s / 1.29GB        21.27s / 3.29GB      3.29GB
class_gen ASM    33.04s / 4.04GB               17.18s / 2.04GB        16.79s / 2.40GB      2.40GB
cli_bootstrap    58.70s / 6.64GB               28.92s / 2.93GB        29.60s / 4.53GB      4.53GB
```

The medium/heavy/worst time stays approximately neutral while their maximum
live process drops 22-48%.  A diagnostic forced-PCO run is not the production
control: runtime_abi PCO crossed the 8 GiB breaker and cli_bootstrap PCO did
too; preserving the existing ASM policy for those lanes is required.

## Source-frozen GC0 Stage2

`build/indexed-sidecar-stage2-v48/stage2-record.json` is complete:

```text
wall                         911.658 s
tree CPU                    2104.575 s (1896.399 user + 208.176 sys)
peak process-tree RSS          7.675 GB under the 8 GiB breaker
pcc2 SHA-256                fa7b1bdaea80480adb98dd28c991a49b6290f61b82e1b9e43c958be6c875fc2b
linkage                     libSystem only; no libpython or LLVM
```

Versus v41 (960.951 s / 2046.211 tree-CPU seconds), wall improves 5.1% while
CPU regresses 2.85% from the extra fresh-process/codec work.  This is exact
structural and memory progress, not the Stage2<=Stage1 result: v48 remains
5.62x Stage1.

The complete 226-module receipt attributes 629.279 worker-seconds to frontend
and 1081.721 to emit.  Small frontend had 386 admission denials and small emit
847.  Charged frontend floors total 410.76 GiB versus 83.70 GiB of observed
peaks; the old AST/combined-process floors are now about 4.9x too conservative
for the post-split frontend phase.

## Discovered correctness defect

The first native sidecar encoded tuple-valued JSON arrays as `null`; a minimal
v45 pcc1 reproduces `json.dumps({"tuple": (1, 2)}) -> {"tuple": null}`.  The
sidecar correctly moved its construction wire to list-only native JSON values.
The generic language defect is separately retained as
`PY-P1-NATIVE-JSON-TUPLE-UNSUPPORTED-SEMANTICS`; this slice does not claim it
fixed.

## Verdict and next boundary

`[CONFIRMED]` for an exact process-exit ownership boundary and lower per-worker
memory.  `[OPEN]` for performance: v48 still executes five frontend/emit lane
pairs sequentially and uses pre-split frontend floors.  The next source slice
must run one unified frontend phase, one exact-sidecar-sized ASM phase and one
exact-sidecar-sized PCO phase.  The complete v48 frontend sample supports a
new phase-specific floor (0.75 GiB + 0.19 GiB per AST MB, covering every
worker by at least 5% +100 MB); this is new post-split evidence, not reuse of
the denied combined-process fit.  Receipt timing must include each phase's
elapsed wall.  Only a complete guarded Stage2 can accept that scheduler.
