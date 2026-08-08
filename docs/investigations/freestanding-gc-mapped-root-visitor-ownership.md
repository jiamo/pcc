# Investigation: freestanding mapped-root visitor production ownership

## Status
resolved

## Problem Description
`LIBC-P2-FREESTANDING-GC` still obtains continuation trace/rewrite and the
shared frame/continuation/scheduler/builtin-exception slot walker from the
large managed `py_gc_backend.py` object. Moving only the two public functions
would leave the graph rule in the collector; copying the walker would create
two rules that can drift. The finite boundary is therefore the whole visitor,
while mark, promotion, and relocation resolution remain single providers in
the collector until their own later migration.

Predecessor: `freestanding-gc-root-registry-production-ownership.md`.

## Repro
Run the ownership gate before the strict mapped-root module exists:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_mapped_roots.py
```

Expected pre-change result: the module is absent and the public trace/rewrite
symbols plus visitor helpers are still owned by `py_gc_backend.py`.

## Test [N/A]
This is an ownership migration rather than a newly reported crash. The gate
must prove exact LLVM/self closure, unique production ownership, GC0..4 C
oracle parity, backend-4 forwarded-root rewriting, and threaded registry
mutation versus trace/rewrite before closure.

## Proposals
- No.1 Move the complete mapped-root visitor into one strict module [CONFIRMED]

## No.1 Move the complete mapped-root visitor into one strict module
### Code Change
Create `freestanding_gc_mapped_roots.py` as the sole owner of mapped and
scheduler slot iteration, builtin-exception slot iteration, continuation
trace/rewrite, and the visitor mode switch. Export exactly three collector
providers for mark, promotion, and relocation resolution, and call them across
the strict cross-object ABI. Rewire every managed collector call site to the
same strict visitor.

### CONFIRMED
The strict module is the sole production owner of the two public continuation
operations and six shared visitor helpers. It calls three unique managed
collector providers plus the existing pcc-Python exception-cache slot provider;
no graph rule is duplicated.

Exact LLVM/self closure, unique archive ownership, GC0..4 C-oracle parity,
backend-4 relocation rewriting, threaded mutation versus trace/rewrite, and
the downstream managed collector paths are green. The final focused cluster
passed 56 cases.

The first stage1 attempt also exposed an ABI-cache design error: putting
raw-only seams in `RUNTIME_SIGNATURES` perturbed every module's generated IR.
An emit-only profile completed in 13.2 seconds, while native emission exhausted
the watchdog. Keeping those exact signatures solely in the strict cross-object
registry restored cache locality; current-source stage1 completed in 78.461
seconds with 301 object hits and 24 misses. That pcc1 compiled the strict module
and `nm` confirmed eight definitions and eleven raw imports.

## Report (only when the investigation is closing)
No.1 landed. Mapped-root iteration, scheduler/builtin-exception iteration and
continuation trace/rewrite now have one strict pcc-Python production owner.
Mark, promotion and relocation resolution deliberately remain single providers
in the collector and are later migration boundaries.
