# Isolation-only closure worker denied

## Candidate identity and focused gates

`pipeline_dependency_closure.py` is byte-identical to accepted No.72
(`19dd5751...`).  The frozen 1,137-file source differs only in pipeline,
frontend-parallel and worker-execution.  Candidate pcc1 is `708bc1c7...`,
uses CPython 3.15.0rc1 and runtime `624e1de9...`, and links only libSystem.

```text
focused worker/dependency/pipeline        102 passed
complete multi-file semantic gate          41 passed
strict worker-execution closure                 rc0
no-host canary output                           42
no-host canary closure worker / summaries     1 / 4
no-host canary linkage                  libSystem only
```

## Complete frontend-only gate

Artifact: `build/closure-worker-no88-frontend-v1`.

```text
status / return code                COMPLETE / 0
elapsed                             157.763s
modules / actions                   218 / 1,090
summary nodes / edges               4,737 / 7,797
largest process                     5,226,577,920 B
process-tree peak                  13,836,599,296 B
closure-worker phase                30.800s
stdout / stderr                     empty / empty
leftover children                   none
```

The tree line passes, but the <=4.5GB process line and <=25s closure line fail;
the summary graph also misses the exact registered reference.  No Stage2 ran.

No.87's cached worker had lower process/tree peaks (3.082GB / 11.648GB) but an
even slower 32.897s closure.  The two controlled variants therefore leave no
accepted point on this design: scan caching trades time for memory under pcc1,
while process isolation alone does not lower coordinator high water enough.
The complete closure-worker production/test surface is forward-removed before
the next Stage2 owner is selected.
