# Investigation: in-flight 32bfed70 relocation-rework regressions (pcc-Python port relocation + backend-#4 collect)

## Status
confirmed-regressions / belongs-to-active-rework (filed 2026-06-18; not fixed —
these are the concurrent 32bfed70 relocation rework's in-progress state)

## Problem Description
Several previously-**resolved** relocation behaviors regressed. They cluster
around commit **32bfed70** ("…GC-root, relocation, zpage & value-class-payload
rework"), which is still in flight (uncommitted/untracked files from concurrent
work were present during this session).

- `tests/python/test_runtime_substrate_spike.py` (4 failing): the **pcc-Python
  PORT** relocation no longer relocates —
  `test_pcc_python_relocating_step_copies_simple_object` gets `step=0,forwards=0`
  (expected `step=2,forwards=1`); plus `..._relocate_copy_rejects_oversized_copy`,
  `..._relocate_copy_consumes_relocation_entry`,
  `..._archive_staleness_ignores_libpython_bridge`. 34/38 substrate tests still
  pass, so only the relocation-specific port functions regressed. These exact
  behaviors have **resolved** investigations
  (`gc-backend4-step-relocates-simple-object.md`,
  `gc-backend4-pcc-py-relocate-copy-size.md`,
  `python-substrate-spike-test-sweep.md`), i.e. they worked and regressed.
- `test_t4_weakref_native_acceptance.py::test_t4_weakref_callable_and_dealloc_clear_native`:
  backend #4 explicit collect finalizes a held (rc=1) FRESH_ALLOC weakref —
  **bisected** to 32bfed70 (passes at cb5d37e8). See
  `gc4-weakref-fresh-alloc-rc1-finalized-on-explicit-collect-32bfed70-regression.md`.

The C-runtime relocation itself still works (the C-side gc4 tests pass); it is the
pcc-Python PORT (`py_substrate.py`, compiled into `libpy_runtime_pcc_py.a`) and the
backend-#4 explicit-collect reachability that regressed.

## Why filed, not fixed
`py_substrate.py` and the backend-#4 collect were reworked in 32bfed70 and the
rework is in flight (concurrent work still touching this area). Patching the port
relocation or the collect reachability now would (a) be deep relocation surgery
and (b) collide with the active rework. The right path is for the relocation
rework to land and re-green these resolved behaviors. A blanket rc>0 finalize
guard is unsafe (would block cycle collection — see the t4_weakref file).

## Likely same family (not separately bisected this session)
- `tests/python/gc/test_pcc_bootstrap_full_gc2.py` / `_gc3.py` (stage2→3 fail):
  gc0 self-host passes, so this is backend-#2/#3-specific — plausibly the same
  GC rework, but NOT verified here (each is a ~130s+ full bootstrap).

## Report
Filed as a consolidated tracking note for the relocation-rework owner. No code
changed. Evidence: resolved-then-failing substrate investigations + the t4_weakref
cb5d37e8 worktree bisect. Lua self-backend (`test_lua.py` self ×4) is a separate
*experimental*-backend gap (even `print(1+1)` fails on the self-built Lua), not
part of this relocation rework.
