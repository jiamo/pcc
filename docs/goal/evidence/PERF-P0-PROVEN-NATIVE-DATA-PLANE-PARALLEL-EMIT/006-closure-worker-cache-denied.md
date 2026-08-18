# Closure worker cache denied

## Frozen candidate

The source snapshot contains 1,137 files and differs from accepted No.72 in
exactly these production files:

- `pcc/py_frontend/pipeline.py`
- `pcc/py_frontend/pipeline_dependency_closure.py`
- `pcc/py_frontend/pipeline_frontend_parallel.py`
- `pcc/py_frontend/pipeline_frontend_worker_execution.py`

The candidate pcc1 SHA-256 is `4ec83a1839a858983ebe8915d6ece6d9f9bb4484d9c296eb4413afbe30a6d9d9`.
Its receipt names CPython 3.15.0rc1, GC0, self/no-libpython, runtime archive
`624e1de9...`, and libSystem-only linkage.

## Focused correctness and canary

Focused worker/cache/import/pipeline tests passed 116; the complete real
multi-file semantic file passed 41/41.  A four-module, two-level function
re-export canary then ran through the candidate pcc1 with host Python disabled.
The final Mach-O link was explicitly the system-cc oracle because the current
pcc-owned link driver is itself a host-Python transition script.

```text
multi_frontend_closure_worker       1
modules / summaries / actions       4 / 4 / 20
closure-worker phase                0.081s
program output                      42
linkage                             libSystem only
leftover children                   none
```

The retained failed canary receipts using `/bin/false` (absent on this macOS
host) and `/usr/bin/false` with `PCC_SELF_LINK=pcc` are harness/link-owner
evidence, not compiler failures.

## Complete frontend-only gate

Artifact: `build/closure-worker-frontend-v1`.

```text
status / return code                COMPLETE / 0
elapsed                             179.753s
modules / actions                   218 / 1,090
summaries                           218
summary nodes / edges               4,738 / 7,801
largest process                     3,081,879,552 B
process-tree peak                  11,648,319,488 B
closure-worker phase                32.897s
stdout / stderr                     empty / empty
leftover children                   none
```

Memory passes the pre-registered <=4.5GB process and <=14GB tree lines.  The
closure-worker phase fails the independently registered <=25s line; retained
No.72 performs the same in-process closure work in 20.922s.  The explicit scan
cache is therefore denied under pcc1 even though it helped CPython-host sizing.
No Stage2 ran and no threshold was moved.

Next slice: retain process isolation but restore the accepted uncached closure
implementation exactly, then rerun the same focused/canary/frontend sequence.
