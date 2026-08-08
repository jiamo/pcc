# LIBC-P3 freestanding runtime closure — 2026-08-13

## Claim boundary

The current production pcc-Python runtime archive is a provenance-verified
187-member archive with a schema-v2 C-API inventory.  No hand-written C or
vendored-libc object is present.  Current-source final-link acceptance proves
the supported Darwin boundary is the documented named libSystem ABI and that
the supported x86_64 Linux production Python runtime is a static zero-libc ELF
with no interpreter, dynamic dependencies, or undefined symbols.

The Linux gate also executes the resulting program and covers list/dict/GC,
integer parsing, self-backend TLS relocation/layout, provenance, member
ownership, and the final link map.  Separate adjacent gates cover the
freestanding process entry and the C-frontend freestanding libc route.

## Current-source evidence

```text
make -B -C pcc/py_runtime libpy_runtime_pcc_py.a ...
PASS: 187 members; adjacent .capi_syms has 444 symbols

python -m pcc.tools.runtime_archive_provenance verify ...
PASS

tests/test_runtime_archive_provenance.py
tests/test_runtime_archive_consumers.py
68 passed in 55.04s

tests/python/test_runtime_archive_isolation.py
13 passed in 6.75s

tests/python/test_freestanding_runtime_no_c_closure.py
120 passed in 135.29s

tests/python/test_freestanding_runtime_link_acceptance.py
5 passed in 4.31s

tests/integration/test_self_backend_x86_64_linux.py::
  test_linux_x86_64_full_production_python_runtime_is_static_zero_libc
1 passed in 303.89s; compiled program status 0

tests/integration/test_self_backend_x86_64_linux.py::
  test_linux_x86_64_freestanding_python_start_is_static_zero_libc
1 passed in 11.78s

tests/integration/test_self_backend_x86_64_linux.py::
  test_linux_x86_64_c_frontend_freestanding_libc_is_static_and_python_owned
1 passed in 12.38s
```

All pytest invocations used `-x -n0`; integration nodes explicitly enabled the
integration marker.  The five-minute Linux production run used a durable log
and produced a final pytest summary.  A first invocation that omitted
`-m integration` was deselected and is deliberately not counted as evidence.

## Honest remaining boundary

This row is `DONE_WEAK`, not `DONE_STRONG`.  Its final required gate is shared
with the repository-wide closeout: fail-first default and integration suites,
the five-GC matrix, and a deliberate sequential current-source
`pcc1 -> pcc2 -> pcc3` fixed point.  Those gates must run once after the
remaining implementation rows are source-complete; repeating them while code
is still changing would invalidate their content-addressed artifacts and turn
the full matrix into a diagnostic loop.
