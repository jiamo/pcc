# Investigation: self-backend sparse SSA cache memory explosion

## Status

resolved locally 2026-07-20

## Problem Description

The fixed-six-worker non-integration suite made a Python worker grow past
20 GiB and eventually terminate without a pytest result.  A later focused run
was manually stopped at roughly 50 GiB.  This was not ordinary xdist overhead:
one self-backend zlib test could reproduce the growth in a single worker.

## Repro [CONFIRMED]

An RSS-capped run of
`tests/c/test_zlib.py::test_zlib_runtime_with_self_backend_system_link_depends_on`
crossed 1,079,721,984 bytes after 5.05 seconds.  The interrupt traceback stopped
at `pcc/backend/self_backend_parse.py:661`, while extending
`_DOT_NUMERIC_SSA_NAME_CACHE`.

The same cap under the normal six-worker scheduler identified the same node in
`gw0` at 1,088,684,032 bytes.  This separates the pcc parser defect from a
general pytest or nested-parallelism leak.

## Root Cause [CONFIRMED]

`decode_ssa_name()` cached numeric LLVM SSA names in Python lists indexed by
the numeric spelling of the name.  Optimized real-project IR may contain a
sparse name such as `%.<large integer>`.  The parser therefore allocated every
unused list entry up to that integer.  Memory use scaled with the largest SSA
suffix instead of the number of SSA names.

Both the plain numeric and dot-numeric caches had the same unsafe dense-index
design.

## Fix [CONFIRMED]

Replace both numeric-name lists with dictionaries keyed by the parsed integer.
The cache still interns repeated spellings, but a sparse name consumes one
entry.  A focused regression uses suffix `1000000`: the old implementation
created 1,000,001 entries and the new implementation creates one.

## Validation

- sparse-cache regression: `1 passed in 0.14s`
- original zlib self-backend node: `1 passed in 4.44s`, peak RSS 128,548,864
  bytes under the same 1 GiB guard
- complete zlib file: `4 passed in 7.00s`, peak RSS 147,308,544 bytes
- complete self-backend unit file: `279 passed in 5.69s`

The fix changes no test selection, worker count, compiler mode, or fallback
boundary.
