# Investigation: module-global finalizers do not run at shutdown

## Status
resolved

## Problem Description
`tests/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown` is the
last remaining GC xfail. A module-global object with `__del__` should still
run its finalizer after `main()` completes, writing `module_del_ran` to
stderr. The compiled program currently exits successfully and prints
`main_done`, but stderr is empty.

## Repro
Focused gate with xfail disabled:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown \
  -q -n0 --runxfail -ra
```

Expected current result before the fix:

```text
AssertionError: assert 'module_del_ran' in ''
```

The original 14-node GC xfail closure list currently reports:

```text
1 failed, 13 passed in 13.18s
```

The full GC gate currently reports:

```text
194 passed, 1 xfailed in 163.38s
```

## Test [CONFIRMED]
Observed locally on 2026-05-08:

- `test_module_global_del_at_shutdown` fails under `--runxfail`.
- The generated program returns `0`.
- stdout contains `main_done`.
- stderr is empty, so `Holder.__del__` is never run for the module-global
  `global_holder`.

## Proposals
- No.1 Emit module teardown functions that release object globals     [CONFIRMED]

## No.1 Emit module teardown functions that release object globals
### Code Change
Module-scope assignments store owned object references into `.modvar.*`
globals so user functions can read them later. The generated `main()` now
calls per-module teardown functions after user top-level code:

- every module emits `_pcc_py_module_fini_<mod>()`;
- the teardown function is idempotent via `.pcc.module.fini.<mod>`;
- object-valued module globals are loaded, cleared to `NULL`, then released;
- entry `main()` calls its own teardown first, then sibling module teardowns
  in reverse initialization order.

This gives module-global objects the same final release path as function
locals while keeping secondary modules alive until program shutdown.

### CONFIRMED
The xfail marker was removed from
`tests/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown`.

Focused command:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_finalizer_corner.py::test_module_global_del_at_shutdown \
  -q -n0 -ra
```

Observed result:

```text
1 passed in 0.69s
```

Containing file and GC closure gates:

```text
tests/test_gc_finalizer_corner.py: 10 passed in 5.83s
original 14-node GC xfail closure list: 14 passed in 13.12s
tests/test_gc_*.py: 195 passed in 156.71s
```

## Report (only when the investigation is closing)
Proposal No.1 landed. The confirmed root cause was missing generated module
teardown for object-valued globals, not runtime finalizer dispatch. The
module-global `Holder` now receives its last release after `main()` completes,
so `Holder.__del__` writes `module_del_ran` to stderr.

Bootstrap/fallback gates after the change:

```text
tests/test_llvm_capi_ir_parity.py tests/test_llvm_capi_end_to_end.py: 23 passed in 0.13s
tests/test_fallback_baseline.py tests/test_ir_py_fallback_baseline.py: 11 passed in 59.03s
tests/test_py_multi_file_compile.py tests/test_py_multi_file_bootstrap_shim.py: 70 passed in 133.08s
```
