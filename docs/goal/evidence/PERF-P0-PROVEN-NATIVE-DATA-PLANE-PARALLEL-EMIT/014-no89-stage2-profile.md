# Accepted No.89 cache-off GC0 Stage2 profile

## Frozen modes

- source/compiler: accepted No.89 snapshot and pcc1
  `b0c6844f40483a613fd15c1bf529ee6c1f2017b1c034532449143fc70be52e01`;
- runtime: accepted GC0 pcc-Python archive `624e1de9...`;
- backend/libpython: self / off, pcc2 links only libSystem;
- caches/jobs: frontend IR off, native object off, frontend 10, self/link 8;
- execution: Stage2 only, no Stage3 and no GC1--4;
- source cwd: read-only No.89 source snapshot;
- sampler: `scripts/run_process_tree_sample.py`, performance lock, 1-second
  interval, durable stdout/stderr/profile artifacts.

## Result

```text
Stage2 wall                    598.629s
compile wall                   583.515s
compiler profile               583.088s
user / sys                    2525.408s / 165.484s
aggregate CPU                 2690.892s
publish barrier                15.081s / rc0
process-tree peak              13,033,111,552 B
peak process count             27
pcc2 SHA                       b23b322aa37f11702f146a51f4de01737b9d71566d61f7a265e1479385fe9d6a
pcc2                           --help rc0 / libSystem-only
```

Same-source Stage1 is 275.13s, so Stage2/Stage1 is 2.1758x. Top phases are
native emit 254.675s, frontend 180.518s and owned link 76.493s.

Against the adjacent summary-worker control, backend IR-to-image improves
337.953→331.753s, while frontend grows 159.028→180.518s and ensure-runtime
12.687→19.404s. Therefore this run supports No.89's backend improvement but
does not support attributing the +20s total to it.

Artifacts:

- `build/no89-current-gc0-stage2-profile-v1/process-tree-result.json`
- `build/no89-current-gc0-stage2-profile-v1/process-tree-samples.tsv`
- `build/no89-current-gc0-stage2-profile-v1/bootstrap.stdout`
- `build/no89-current-gc0-stage2-profile-v1/bootstrap.stderr`
- `build/no89-current-gc0-stage2-profile-v1/profile/stage2.json`
- `build/no89-current-gc0-stage2-profile-v1/profile/stage2.result.json`
- `build/no89-current-gc0-stage2-profile-v1/pcc2`

This proves a source-frozen GC0 Stage2 and phase/resource baseline. It does not
prove a pcc2/pcc3 fixed point for No.89 and deliberately does not run GC1--4.
