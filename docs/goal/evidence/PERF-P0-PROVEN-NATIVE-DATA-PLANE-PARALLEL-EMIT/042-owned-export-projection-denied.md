# 042 — owned JSON export projection denied and removed

Date: 2026-09-04

## Proposal

The native-export JSON decoder already owns the object graph returned by
`json.loads`.  The candidate reused its dictionaries in place and converted
only ABI-sensitive lists back to tuples, rather than recursively allocating a
second dictionary graph.  A second revision enabled that path only for a
native pcc1 worker so host Stage1 would retain its existing cost model.

This was intentionally narrower than the already-denied 224-file shard/index
design: it changed no wire partitioning and introduced no callback graph.

## Correctness and measurements

The focused export/worker packet passed **107** tests and the strict closure
passed.  Both representative worker outputs were exact.

```text
metric                     v28 control       native-only v30 candidate
tiny worker wall           1.59s             1.42s
tiny worker instructions   13.170B           12.229B
tiny worker max RSS        614MB             519MB

py_ast worker wall         26.78s            27.83s
py_ast worker CPU          26.67s            26.89s
py_ast worker instructions 367.722B          366.703B
py_ast worker max RSS      3.124GB           3.014GB
PCO SHA-256                9987edea...f13d3c0 in both arms
```

Stage1 was also consistently worse:

```text
v28 retained baseline      171.16s wall / 689.56s tree CPU
v29 unconditionally owned  190.99s wall / 735.81s tree CPU
v30 native-only routing    184.01s wall / 711.89s tree CPU
```

These are transfer observations rather than a formal alternating pair, but
they are sufficient to reject the candidate: the largest representative
worker has no CPU win, the memory reduction is only 3.5%, and both candidate
generations regress Stage1.

## Verdict

`[DENIED; removed]`.  The candidate source and candidate-only tests were
forward-removed.  The current compiler source identity is exactly the retained
v28 identity
`3e61ddacfcc8a7766aa0f4583f510b3532eba8ab466032d47021cc4f3d99c60f`,
so no rebuild is needed to prove restoration.  Do not retry dictionary reuse
as the Stage2 answer.  A new export-plane proposal must avoid decoding and
scanning unrelated modules, use one compiler-native indexed representation
rather than 224 JSON shards, and first prove the full pcc1 closure boundary.
