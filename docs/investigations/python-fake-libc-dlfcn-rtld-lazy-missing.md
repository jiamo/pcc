# Investigation: fake `dlfcn.h` omits `RTLD_LAZY` used by `py_http.c`

## Status
resolved

## Problem Description

The current-source runtime-oracle fixture fails while building
`libpy_runtime_pcc.a`. All 26 parameterized oracle cases report setup errors,
but they share one underlying compile failure: pcc cannot compile
`pcc/py_runtime/src/py_http.c` because its fake `<dlfcn.h>` declares
`RTLD_NOW`, `RTLD_LOCAL`, and `RTLD_GLOBAL` but not the standard
`RTLD_LAZY` flag used by the owned HTTPS transport.

This follows the earlier
[`python-fake-libc-netdb-socket-addrinfo-missing.md`](python-fake-libc-netdb-socket-addrinfo-missing.md)
`py_http.c` header-surface gap. It is a separate missing macro, not a socket
layout failure.

## Repro

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 -m integration \
  'tests/python/test_py_runtime_pcc_emit.py::test_pcc_emits_object_for_runtime_source[py_http.c]'
```

Expected before the fix: exit 1 with
`Error: use of undeclared identifier 'RTLD_LAZY'`.

The downstream fanout is:

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q \
  tests/python/test_runtime_oracle_diff.py
```

Expected before the fix: 2 passed and 26 setup errors from the one failed
`libpy_runtime_pcc.a` build.

## Test [CONFIRMED]

Both repros were observed on 2026-07-22. The single-source gate fails in
0.85 seconds with the exact missing identifier. The oracle file finishes in
46.05 seconds with 26 setup errors rooted at the same make target.

## Proposals

- No.1 Add the missing standard lazy-binding macro to fake `dlfcn.h`
  [CONFIRMED]

## No.1 Add the missing standard lazy-binding macro to fake `dlfcn.h`

### Code Change

Add a guarded `RTLD_LAZY` definition with value `1` beside the existing
`RTLD_NOW` definition in `utils/fake_libc_include/dlfcn.h`. Darwin and Linux
both use value `1` for this flag. Keep the change scoped to the missing macro;
the platform-specific values of the other flags require a separate audit and
are not needed to resolve this failure.

The runtime-oracle artifact key must also include
`utils/fake_libc_include/`. Those headers affect every pcc-emitted runtime
object; omitting them permits a stale archive to survive a header correction
and would turn the post-fix gate into cache-dependent evidence.

### CONFIRMED

- focused fake-libc/C tests: 19 passed in 0.65s;
- `py_http.c` pcc object emission: 1 passed in 1.20s;
- cold content-keyed runtime oracle: 28 passed in 117.43s;
- cache-key structural guards: 18 passed in 0.45s.

Before the change, the same single-source node failed in 0.85 seconds and the
runtime-oracle file reported 26 setup errors. No source or oracle case was
removed. The cache-key change ensures the successful oracle build consumed
the corrected fake header.

## Report

Proposal No.1 is confirmed. The generic fake header now supplies the standard
macro used by the runtime, and the immutable archive identity covers that
header surface. Replacing `RTLD_LAZY` in `py_http.c`, skipping the source, or
reusing an old archive was unnecessary and would have hidden the frontend
contract gap.
