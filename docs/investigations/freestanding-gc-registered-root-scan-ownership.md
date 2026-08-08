# Investigation: freestanding registered-root scan ownership

## Status
resolved

## Problem Description

`freestanding_gc_mapped_roots.py` already owns the one slot visitor used for
gray, promotion and rewrite, and strict modules already own frame,
continuation and scheduler registries. The managed `py_gc_backend.py` still
duplicates three loops over those registries: current-root gray seeding,
generational root promotion and backend-4 root rewriting.

This slice is scanning and dispatch only. It must not copy the collector's
mark, oldification/promotion or relocation-resolution implementations; those
remain the three unique providers called by the strict slot visitor.

## Repro

Run the ownership gate before adding the registered-root scanner:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_mapped_roots.py::test_mapped_roots_have_one_strict_visitor_and_three_collector_providers
```

Expected pre-change result: the new scanner symbol is absent and the managed
collector still contains direct frame/continuation/scheduler/cache traversal.

## Test [N/A]

This is an ownership migration. Closure requires exact LLVM/self object
symbols, unique production archive ownership, direct registered-root count and
all existing GC0..4 mapped-root/relocation/threaded plus collector downstream
gates.

## Proposals

- No.1 Consolidate all registered-root scan modes in the strict mapped-root object [accepted]

## No.1 Consolidate all registered-root scan modes in the strict mapped-root object

### Code Change

Add one raw ABI that walks frame, continuation, scheduler and builtin-exception
roots and dispatches the existing mode-based slot visitor. Managed gray,
promotion and rewrite phases call that ABI while retaining their object-list,
referent and epoch-retirement logic.

### Result

`pcc_gc_visit_registered_root_slots` now uniquely owns frame, continuation,
scheduler and builtin-exception-cache traversal for gray, promotion and
rewrite. The managed collector retains object-list and semantic operations but
contains no duplicated registered-root loop.

Exact LLVM/self/fresh-pcc1 closure, unique archive ownership, a direct
26-slot count, GC0..4 C differentials, relocation, threaded mutation and 183
downstream tests are green. Full commands and measured results are recorded in
`docs/goal/evidence/2026-08-03-freestanding-gc-registered-root-scan.md`.
