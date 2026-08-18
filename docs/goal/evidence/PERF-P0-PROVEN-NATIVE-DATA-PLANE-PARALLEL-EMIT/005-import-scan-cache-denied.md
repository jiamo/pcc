# Source-scoped import scan cache denied

## Baseline and owner

Accepted No.72 completes a source-frozen GC0 Stage2 in 566.528s against
Stage1 274.56s. Pcc2 is executable and libSystem-only. Stage2 process-tree
peak is 18.532GB; root pcc1 reaches 6.991GB before/through frontend workers.

A corrected byte-weight `malloc_history` call tree attributes 1.863GiB of
live allocation stacks; 4.4GiB of earlier VM mappings lack stacks and remain
unattributed. The attributed paths show repeated import/closure text scans.
Host call-count sizing over the exact 218-module closure reports:

```text
_without_type_checking_imports calls       1510
total bytes scanned                   50,044,882
distinct source texts                         221
unique bytes                           7,216,809
amplification                              6.93x
```

## Candidate

One path-keyed cache was scoped to a single
`_prepare_multi_source_compile_closure` call. It retained raw, ordinary
TYPE_CHECKING-masked and package-only AttributeError-masked variants under
separate keys, then died before workers. No global/id/mtime cache existed; a
regression proved the next top-level closure observed changed file contents.

Real closure calls fell 1510 -> 433 (-71.3%), bytes 50.0MB -> 14.3MB and
amplification 6.93x -> 1.98x, while the module set stayed 218. Focused
dependency/import/recursive/provider/AST-reuse gates passed 46 with one
explicitly baseline-red textwrap policy node deselected. Frozen No.72 source
fails that node identically because current policy admits a compiled textwrap
provider while the stale test expects exclusion.

Control/candidate Stage1 manifests contain 1,137 files and differ only in
`pcc/py_frontend/pipeline_dependency_closure.py`. Both use CPython 3.15.0rc1,
GC0, the same `624e1de9...` runtime and libSystem-only linkage.

```text
metric                     control                 candidate
pcc1 sha                   ebde05bb...             7c176805...
Stage1 wall                274.56s                 264.09s
Stage1 CPU                1078.93s                1049.58s
Stage1 instructions       177.341B                167.262B
```

Those single construction receipts are supporting signal, not paired proof.

## Frontend-only verdict

The pre-registered gate required largest process <=5GB and tree <=14GB before
a full Stage2. The candidate was interrupted on first threshold breach:

```text
baseline complete Stage2 frontend-era maxima
  largest process          6.991GB
  process tree            18.532GB

candidate frontend-only, interrupted at 113.477s
  largest process          5.715GB   (-18.2%, but >5GB line)
  process tree            16.640GB   (-10.2%, but >14GB line)
  return code                    -15 / status INTERRUPTED
  frontend profile/output       absent by deliberate early stop
  leftover children             none
```

The cache is a real improvement but fails both declared resource lines, and
the frontend produced no final artifact. No Stage2 ran. The candidate and its
dedicated test were removed by forward patch; the production file is
byte-identical to accepted No.72 SHA-256
`19dd5751ea5ace674a0c48f86f615163e778f0e67bbfdbd357cdbb9702034e00`.

The next design must execute closure/import scanning in a short-lived native
pcc1 worker and return only a deterministic compact source/module manifest.
Worker exit, not an unsafe allocator trim, is what can return high-water slabs
to the OS before frontend worker overlap.
