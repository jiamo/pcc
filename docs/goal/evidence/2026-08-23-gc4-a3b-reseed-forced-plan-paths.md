# GC4 A3b relocation-reseed forced plan paths

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b evidence sub-boundary confirmed; parent task remains
`IN_PROGRESS`.

## Claim boundary

The C and strict pcc-Python runtime roots now expose one default-inactive,
release/acquire diagnostic seam for relocation-reseed plan validation. It can
pause reseed after the locked required-page count and graph unlock but before
private node preparation, and can limit private evacuation-node allocations to
`-1` (unlimited), zero, or a finite count.

The deterministic pthread probe proves that selection growth from one to two
pages during that window causes reseed to revalidate and prepare the second
node before commit. A zero-node allocation budget proves reseed returns before
detaching the old two-page evacuation membership; restoring the default budget
then rebuilds two pages and 8320 bytes.

This is evidence for the already implemented plan/revalidation protocol, not a
bounded-scan claim. Reseed still walks the relocation and page lists without a
bound during required-count and commit. The probe is diagnostic-only and
defaults inactive; production callers must not configure it.

## Genuine RED

`test_relocation_reseed_has_deterministic_plan_window_and_failure_control` was
added first and failed on the absent probe ABI:

```text
1 failed in 0.34s
AssertionError: assert 'pcc_gc_backend4_reseed_plan_probe_config' in ...
```

## Implementation

- C and strict state own three raw atomic values: pause, observed-window state,
  and allocation limit. The exact raw globals are registered in the existing
  freestanding GC ABI inventory.
- Reseed checks the pause only after graph unlock. A requested pause publishes
  state with release ordering and safepoints until the controller clears it;
  it never waits while holding the graph lock.
- Private node preparation observes the diagnostic allocation limit before
  each allocation. A zero or exhausted budget returns the partial plan to the
  existing short-plan failure path, which finishes it without detaching the old
  list.
- `py_runtime.h` labels the interface as a deterministic diagnostic seam with
  inactive production defaults.

## Focused evidence

All pytest commands stopped at the first failure.

1. Both affected strict modules compiled under
   `--backend self --python-libpython=off --ir-scaffold=on --python-library`;
   the durable module receipt is
   `build/gc4-a3b-reseed-forced-closures.log`.

2. The final packet covers the source/ABI contract, deterministic C and strict
   plan growth plus allocation failure, prior C/strict four-thread reset/reseed
   stress, prior repeated two-page reseed, and raw-state ABI registration:

   ```text
   gtimeout 180s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short tests/python/test_gc_backend4_production.py::test_relocation_reseed_has_deterministic_plan_window_and_failure_control tests/python/test_gc_backend4_production.py::test_c_reseed_forces_plan_growth_and_allocation_failure tests/python/test_gc_backend4_production.py::test_strict_reseed_forces_plan_growth_and_allocation_failure tests/python/test_gc_backend4_production.py::test_c_concurrent_reset_reseed_revalidates_prepared_plan tests/python/test_gc_backend4_production.py::test_strict_concurrent_reset_reseed_revalidates_prepared_plan tests/python/test_gc_backend4_production.py::test_c_reseed_rebuilds_multiple_evacuation_pages_from_prepared_nodes tests/python/test_gc_backend4_production.py::test_strict_reseed_rebuilds_multiple_evacuation_pages_from_prepared_nodes tests/python/test_freestanding_gc_state.py::test_gc_state_storage_types_are_registered_in_runtime_abi 2>&1 | tee build/gc4-a3b-reseed-forced-final.log'
   8 passed in 132.14s
   ```

3. Python syntax, C syntax with `PCC_WITH_THREADS=0/1`, and
   `git diff --check` passed.

## Frozen identities

```text
a7e74ba618ba411f4d1f0815556b90c1cacfc107ddb7961f01085472f1caa00e  pcc/py_runtime/src/py_gc_backend.c
6e09b0b8ad95f8806e052e87361edd753c24f6735be3f0745302ce153cffabc8  pcc/py_runtime/include/py_runtime.h
359587b6b34d063f5c95b924c288107eaccf8b42afb817940101967f3778c0a2  pcc/py_runtime/py/py_gc_backend.py
7cf1304bb220b1397798ff70571c2693861af286441c94822366d75d3decae71  pcc/py_runtime/py/freestanding_gc_state.py
89a4e53ec6daae2d2c47f972eb684b55a70610c3c2342ec26fae670513c52439  pcc/py_frontend/codegen/runtime_abi.py
6391243b75a40d31f0388535c9bfe065554e6739922489fb4576a4413240feac  tests/python/test_gc_backend4_production.py
5328047ac9476de2b4a0f12e4f1dd6bfd91c79d54640565ecd9480175c09a9b2  build/gc4-a3b-reseed-forced-closures.log
975b1107e17d623d9d4ad7e122dffc3513b90fd095f34d809d2a948364e5271b  build/gc4-a3b-reseed-forced-final.log
```

## Next boundary

Bound or split relocation-reseed's required-count and commit walks without
carrying raw, non-owning relocation/page pointers across graph unlock. Preserve
the now-proven growth retry and allocation-failure behavior. A3c remains
blocked until this and the remaining GC3/callback/log holder inventory are
source- and pthread-green.
