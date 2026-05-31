# Investigation: Backend #3 minor bump arena allocation

## Status
resolved

## Problem Description
Implement the Backend #3 Tier 5 generational slice from `goal.md`: small
`pcc_gc_alloc()` objects under `PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR` should
use a real bump-pointer minor heap path instead of only updating
young/old flags and minor allocation telemetry.

## Repro
```
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_c_runtime_uses_minor_bump_arena -q -n0
```

Expected current failure before the fix: the probe exits successfully, but
stdout does not show a minor arena flag or arena refill/bump telemetry.

## Test [CONFIRMED]
`tests/test_gc_backend_generational.py::test_generational_backend_c_runtime_uses_minor_bump_arena`

Observed with:

```
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_c_runtime_uses_minor_bump_arena -q -n0
```

Current result: fails with stdout `['0', '8', '-1', '-1', '-1']` instead of
`['1', '8', '1', '8', '0']`. This confirms that Backend #3 currently records
minor allocations but does not route small `pcc_gc_alloc()` objects through a
minor bump arena or expose arena telemetry.

## Proposals
- No.1 Add a C-runtime minor arena behind backend #3 `pcc_gc_alloc()`     [CONFIRMED]

## No.1 Add a C-runtime minor arena behind backend #3 `pcc_gc_alloc()`
### Code Change
Add a single-domain bump arena for Backend #3 small allocations. The arena is
selected only when `pcc_gc_backend() == PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR`
and `size <= PCC_GC_MINOR_ALLOC_MAX`; otherwise allocation falls back to the
existing `calloc` path. Arena-allocated objects are tagged in the object flags,
tracked in the pluggable object list, and released back to arena ownership
instead of being passed to `free()`.
### CONFIRMED
The implementation adds a Backend #3-only minor arena allocation path under
`pcc_gc_try_minor_alloc()`, selected from `pcc_gc_alloc()` before the existing
`calloc` fallback. Small arena allocations set `PY_FLAG_GC_MINOR_ARENA`, update
minor arena refill/bump/fallback telemetry, and keep object memory owned by the
minor block. Object deallocators now release object bodies through
`pcc_gc_free_object_memory()`, so ordinary `malloc` objects still call `free()`
and minor-arena objects return ownership to their block without an invalid
free.

Confirmed with:

```
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_generational.py::test_generational_backend_c_runtime_uses_minor_bump_arena -q -n0
```

Observed result: `1 passed in 3.47s`.

## Report (only when the investigation is closing)
No.1 landed. It is intentionally scoped to the C runtime Backend #3 allocation
path and does not claim full OCaml-style promotion/copying or domain-local
multi-thread heap ownership. The gate now verifies the concrete behavior that
was missing before this investigation: small `pcc_gc_alloc()` objects are
allocated from a minor bump arena and expose arena telemetry.
