# Packed stack-map records, current PCO floor, and Stage2 transfer

## Accepted stack-map slice

The structured AArch64 stack-map writer previously created one 32-byte Python
`bytes` object for each final safepoint record.  Frozen `py_ast` contains
46,625 such records (1,492,000 payload bytes).  The pcc1 path now accumulates
their ten scalar fields in one native arena and emits one record blob per
function; CPython keeps the `Struct.pack` oracle path.

One adjacent v54 -> v57 pair on the same frozen `module_79.direct.pidx`
produced exact PCO SHA-256
`0ead76f72c90f479810e9c504de5f1b8021cddee7e3612ec983cf94916c19fb0`:

```text
metric                    v54 control          v57 candidate       C/B
wall                      18.13s               17.18s               0.948
user+system               18.01s               17.13s               0.951
instructions              274,738,213,976      262,001,579,728      0.954
cycles                     59,879,562,512       56,906,542,247      0.950
process-tree RSS           1,681,981,440 B      1,585,430,528 B      0.943
peak footprint             1,663,108,752 B      1,568,147,016 B      0.943
```

Native phase counters confirm transport RSS falls 1,173->1,078MB and mapped
allocator capacity 1,143->1,048MB.  This is an accepted 1.05x worker CPU/wall
and 5.7% RSS slice, not merely output-neutral refactoring.

The old largest PCO worker, `module_164`, also improves from the retained v51
16.515s / 2,163,834,880B to v57 12.49s / 1,473,069,056B, with exact output.

## Complete current PCO population

All 195 retained PCO sidecars were replayed with v57 under the performance
lock and 8 GiB circuit breaker.  The old top 20 and remaining 175 each
completed in about 59s; every PCO was byte-identical.  Current maximum worker
RSS is 1,589,706,752B (`py_ast`).

The admission floor was therefore rebound from the complete population, not
from one representative:

```text
floor = 0.25 GiB + sidecar_bytes * (0.13 GiB / 1,000,000 bytes)
```

For every one of 195 workers the floor is at least measured peak x1.05 plus
100,000,000 bytes; the minimum margin is 11.6MB.  The independent 7 GiB soft
ceiling, live-RSS suspension ladder, and 8 GiB external breaker remain.  A
complete new-floor replay launched 195/195, produced exact outputs, completed
in 97.25s and peaked at 4,174,299,136B process-tree RSS with no suspension or
failure (old Stage2 PCO phase: 160.355s).

## Source-frozen v58 Stage1 and Stage2

v58 binds the accepted stack-map writer and measured floor into one immutable
source snapshot.

```text
Stage1 wall                 164.88s
Stage1 timed-tree CPU       673.69s
Stage1 sampled tree peak    4,815,749,120B
pcc1 SHA-256                1b3c4167437e5764800a8c4c429bda6e92b91dc82e0de3f1c3d64a1c23979859

Stage2 compile wall         535.345s
Stage2 publish barrier        9.599s
Stage2 total wall           544.963s
Stage2 timed-tree CPU      1999.275s
Stage2 sampled tree peak    7,730,724,864B
pcc2 SHA-256                33ce01198c31089787475f7eec7b76127083c0281f4bae9f933ca6f49da4e57a
```

The Stage2 process and sampler completed normally, `pcc2 --help` passed, and
linkage is libSystem-only (no libpython/LLVM).  Relative to v51 Stage2
595.457s / 2143.782 CPU seconds / 7.679GB, wall improves about 8.5%, CPU about
6.7%, while peak stays within the same 8 GiB envelope.  Stage2/Stage1 total
wall is still **3.305x**, so the task remains open.

Measured deferred phases:

```text
indexed frontend   128.534s   peak live 5.396GB
indexed ASM emit   122.044s   peak live 5.360GB
indexed PCO emit   105.146s   peak live 4.532GB
Mach-O link         61.378s   (assemble 12.982; prepare/link 41.615)
```

The pcc1 coordinator profile is 117.593s; its export phase is 74.577s with
compiled summary width two, versus Stage1 export 9.250s at width seven.  This
is the next bounded wall owner, but any width change must remain under the
same 8 GiB process-tree breaker because the coordinator itself reaches about
7.70GB near checkpoint publication.

## Gates and claim boundary

- 36/36 stack-map ABI tests.
- 92 focused stack-map/codec/native-object/structured tests.
- real `py_ast` and full 195-item PCO byte oracles.
- 227-module strict contextual closure.
- scheduler/stack-map focused suite: 135 passed.
- v58 Stage1 and Stage2 complete with source/runtime/linkage/resource receipts.

This does not prove Stage2 <= Stage1, pcc2/pcc3 fixed point, GC1--4 equality,
or host-free final linking.
