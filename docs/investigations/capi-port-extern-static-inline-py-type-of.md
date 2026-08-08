# Investigation: C-API port modules extern() runtime names with no exported definition (py_type_of, py_int_rem)

## Status

active

## Problem Description

Commit 93cfbca5 (2026-08-07, "C-API shim closure: migrate all remaining Py*
symbols to pcc-Python") introduced extern() bindings in the py_capi_*_runtime
port modules to two names that no object file exports:

1. `py_type_of` — exists only as a `static inline` in
   pcc/py_runtime/src/py_internal.h:1029 (no linkable symbol). Externed by
   SIX port modules (arg, buffer, cext, import, seqiter, unicode_search).
2. `py_int_rem` — exists NOWHERE (the real exported modulo helper is
   `py_int_mod`, declared in py_runtime.h with Python sign semantics).
   Externed by py_capi_number_runtime.py and called on the
   PyNumber_Remainder path.

Incremental Makefile stamps hid both: the archive looked "up to date" while
any FULL rebuild + app link died with undefined `_py_type_of` (py_int_rem
was latent one link further). Found while chasing the numpy demo link chain
(numpy-dyn-reachability-selfbackend-link-gap.md).

## Repro

```bash
cd pcc/py_runtime
make -B libpy_runtime_pcc_py.a PCC=../../build/bootstrap/pcc1 \
  PYTHON=<llvmlite-capable python>
# then any pcc1 app compile importing the capi surface:
#   Undefined symbols: "_py_type_of", referenced from py_capi_*.o
```

## Test [CONFIRMED]

New static regression: tests/python/test_port_extern_symbols_resolve.py —
collects every `extern("py_*"/"pcc_*")` binding under pcc/py_runtime/py/ and
asserts each name is an exported symbol of the C runtime archive or the
pcc-Python port archive (session fixtures). First run failed exactly on
`py_int_rem` after the py_type_of fix landed, proving the test catches the
class. `1 passed in 92.91s` after both fixes.

## Proposals

- No.1 Exported wrapper pcc_py_type_of + rebind six externs   [CONFIRMED]
- No.2 Rebind py_int_rem extern to the real py_int_mod        [CONFIRMED]

## No.1 Exported wrapper pcc_py_type_of + rebind six externs

### Code Change

- pcc/py_runtime/src/py_capi_shim.c: exported
  `int64_t pcc_py_type_of(PyObject *o)` wrapping the header inline (the
  inline stays zero-cost for C callers; the wrapper gives the port tier a
  linkable symbol). Compiled into py_capi_compat.o (port archive) and the
  plain shim object.
- Six port modules rebind `extern("pcc_py_type_of", (c_ptr,), c_int64)`;
  local name `py_type_of` unchanged, so no call-site churn.

### CONFIRMED

Full `make -B` port archive rebuild completes; the numpy demo app link no
longer reports `_py_type_of`; test_port_extern_symbols_resolve no longer
flags it.

## No.2 Rebind py_int_rem extern to the real py_int_mod

### Code Change

py_capi_number_runtime.py: `py_int_rem = extern("py_int_mod", ...)` —
py_int_mod is the exported Python-sign-semantics modulo the remainder path
needs; only the extern's symbol string changes.

### CONFIRMED

test_port_extern_symbols_resolve went red-on-py_int_rem -> green with only
this change. Semantics: PyNumber_Remainder("%") maps to Python sign
semantics, which py_int_mod implements per its header contract.

## Open boundaries

- The linked investigation's fail-open runtime-rebuild warning and
  host-python discovery remain open (they made this class invisible).
- Behavioral coverage of the remainder path (a pcc-native extension calling
  PyNumber_Remainder) is indirect; the static test pins symbol resolution,
  not runtime values.
