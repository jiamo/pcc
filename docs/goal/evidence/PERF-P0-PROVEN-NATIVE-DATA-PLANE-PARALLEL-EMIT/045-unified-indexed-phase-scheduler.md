# 045 — unified indexed phases remove lane barriers and cut Stage2 18%

## Implemented boundary

The v2 deferred runner no longer executes five frontend/emit lane pairs.  It
keeps the old lane classifier only for the established artifact policy, then
runs three deterministic bounded phases:

```text
all 226 frontend manifests -> PIDX
31 large PIDX             -> ASM
195 small PIDX            -> PCO
```

Frontend admission uses a post-split formula fitted against every v48 worker,
not the denied combined frontend+emit AST model.  ASM/PCO admission uses the
exact sidecar byte size that exists before its phase.  Every result transition
is atomic and a v1 plan retains the legacy behavior.  Phase receipts now carry
their own elapsed wall.

The focused scheduler/codec/worker/inventory packet passed 59 tests before the
source-frozen build.  The v49 pcc1 source hash is
`8deca369f345c3ed564ff6297dcca45596f5839b9877836248374816b5e45534`;
pcc1 is
`7c5b1e3423cedfabf9779aa44b279a400db07cc96512e493f59e347d58fc0722`.
Stage1 completed in 161.43 s / 662.26 tree-CPU seconds / 4.803 GB peak and is
libSystem-only.

## GC0 Stage2 result

`build/indexed-sidecar-stage2-v49/stage2-record.json` completed:

```text
wall                         745.327 s
tree CPU                    2082.828 s (1901.509 user +181.319 sys)
peak process-tree RSS          7.696 GB under the 8 GiB breaker
pcc2 SHA-256                865764612bf2cd2c3ba9e6b1f448394f8d526938c4a678e71a367e2fb3df736b
linkage                     libSystem only; no libpython or LLVM
```

The runnable pcc2 `--help` canary passed.  Against v48, wall improves 18.2%
(911.658 ->745.327 s) and tree CPU improves 1.0% (2104.575 ->2082.828 s).
Against v41, wall improves 22.4% while tree CPU is still 1.8% higher because
the split pays a second pcc1 startup and codec for each module.  This remains
4.62x Stage1 and makes no fixed-point or GC1-4 claim.

Measured phases:

```text
phase       elapsed   width  admitted  denied  peak live  peak charged
frontend    139.405s    10      226       447    4.687GB      7.504GB
ASM emit    150.374s     8       31       549    4.547GB      7.498GB
PCO emit    240.778s    10      195       851    4.711GB      7.515GB
```

The offline scheduling model predicted 134+151+245=530 s; actual phase total
is 530.557 s.  Remaining end-to-end time is therefore attributable rather
than noise: about116 s in the compiled coordinator/export path and about99 s
in final host link/publication.

## Next measured lever

The first exact-sidecar PCO floor deliberately used a 1.5 GiB base and charges
288.43 GiB across 195 workers, while their measured peaks total98.32 GiB.
A complete v49 fit, with no module-name cases, is:

```text
ASM: 0.40 GiB + 0.07 GiB per sidecar MB
PCO: 0.30 GiB + 0.18 GiB per sidecar MB
```

Both cover every v49 worker by at least its measured peak ×1.05 +100 MB; PCO's
smallest extra slack is about56 MB after choosing the 0.30 GiB base.  With a
12-worker cap and the same 7 GiB soft/8 GiB hard envelope, replaying the exact
v49 durations predicts ASM133.3 s and PCO156.9 s, versus150.4/240.8.  This is
the next finite scheduler slice.  It still cannot close Stage2<=Stage1; after
it, the 116 s coordinator, 99 s linker and pcc1 per-operation emit CPU remain
architectural owners.

## Verdict

`[CONFIRMED]` for deterministic unified phase scheduling and material Stage2
wall improvement without output, linkage, CPU or memory regression.  The task
remains open: Stage2/Stage1 is4.62x and GC0 fixed point is not yet run.
