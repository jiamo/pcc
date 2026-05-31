# Investigation: `fake_libc_include/netdb.h` and `sys/socket.h` lack `addrinfo` / `sockaddr` so pcc can't compile `py_http.c`

## Status
resolved

## Problem Description

`tests/python/test_py_runtime_pcc_emit.py::test_pcc_emits_object_for_runtime_source[py_http.c]`
failed with:

```
Error: '__struct_addrinfo'
```

`pcc/py_runtime/src/py_http.c` uses `struct addrinfo`, `struct sockaddr`,
`getaddrinfo`/`freeaddrinfo`, `socket`/`connect`/`send`/`recv`, and the
`AF_UNSPEC` / `SOCK_STREAM` macros, via `<netdb.h>` and
`<sys/socket.h>`. pcc's CPP layer maps standard system header includes
to the project's `utils/fake_libc_include/` stubs, but those two
headers were empty (just `_fake_defines.h` + `_fake_typedefs.h`
includes), so the parser saw `struct addrinfo` with no declaration in
scope and dropped to a `__struct_addrinfo` env lookup that failed with
a raw `KeyError`.

## Repro

```bash
env -u LC_ALL uv run pytest \
  'tests/python/test_py_runtime_pcc_emit.py::test_pcc_emits_object_for_runtime_source[py_http.c]' \
  -q -n0
```

Pre-fix: pcc exits 1 with `Error: '__struct_addrinfo'`.

## Test [CONFIRMED]

Same pytest case; pre-fix fails, post-fix passes. Full
`tests/python/test_py_runtime_pcc_emit.py` runs 67 / 67.

## Proposals

- No.1 Add minimal `struct addrinfo` / `struct sockaddr` /
  `AF_*` / `SOCK_*` / socket-API decls to the relevant fake-libc
  headers                                                       [CONFIRMED]

## No.1 Add fake-libc socket decls
### Code Change

`utils/fake_libc_include/netdb.h`:
- minimal `struct addrinfo` (field layout matches both glibc and Darwin)
- `struct sockaddr` + `socklen_t`
- `getaddrinfo` / `freeaddrinfo` decls

`utils/fake_libc_include/sys/socket.h`:
- `AF_UNSPEC` / `AF_INET` / `AF_INET6` / `SOCK_STREAM` / `SOCK_DGRAM` macros
- `struct sockaddr` + `socklen_t` (guarded to avoid double-decl with netdb)
- `socket` / `connect` / `send` / `recv` decls

Both files have header-guard macros so including both still works.

### CONFIRMED
- `tests/python/test_py_runtime_pcc_emit.py` 67 passed (was 1 failure
  on `[py_http.c]`).
- Wider C + corpus + bootstrap baselines unchanged: tests/c/`test_c_parser.py`
  + `test_unsigned_loads.py` + `test_llvm_capi_*.py` + corpus 265 passed;
  `test_fallback_baseline.py` + `test_bootstrap_gate_baseline.py` +
  `test_ir_py_fallback_baseline.py` 17 passed / 4 skipped.

### Why these decls are safe
fake-libc is consulted by the *parser* to resolve type shapes; the
real numeric values and function bodies come from the system compiler's
libc at link time. The added struct fields match the canonical
BSD/Darwin layout so any pcc-side sizeof/offsetof reasoning stays
consistent with what the link-time libc will use. The actual `AF_*`
values are placeholders for the parser — system headers will override
them at compile time for non-pcc builds, and for pcc builds the
constants are only used to pass through to `socket` / `getaddrinfo`,
which translate them via the real libc.

## Report
Landed. py_http.c now compiles cleanly under pcc's `--emit-obj`
runtime-source gate. If a future runtime addition reaches deeper into
the socket API (poll/select on sockets, `getsockopt`, etc.) the fake
headers will need matching extensions; today's surface is the minimum
that closes this regression.
