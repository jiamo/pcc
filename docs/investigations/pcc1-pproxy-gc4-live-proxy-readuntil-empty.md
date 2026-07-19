# Investigation: pcc1 pproxy GC4 live proxy readuntil returns empty

## Status
active

## Problem Description
The user wants the no-libpython self-backend `pcc1` built in
`/Users/jiamo/my/pcc` to run `pproxy` from
`/Users/jiamo/python/python-proxy` with `PCC_GC_BACKEND=4`, without modifying
that `python-proxy` tree and without package-specific shortcuts.

The `pproxy --test` path already succeeds through the remote SOCKS server, but
the long-running local proxy command exits after a local HTTP client connects.
A temporary copy of `python-proxy` under `/tmp` showed the current failure
shape: after `HTTP.guess` reads and rolls back `b"GET "`, `HTTP.accept` calls
`await reader.read_until(b"\r\n\r\n")`, receives an empty bytes object, and the
compiled module exits.

### Update 2026-06-20

After the `StreamReader.read_until`, dynamic `list.pop(0)`, hoisted-name
collision, and `next(filter(...), default)` fixes, the canonical
`build/bootstrap-pytest-self/pcc1` no-libpython stage1 rebuild reaches a later
live-proxy failure. The exact live proxy command listens on `:8081`, and local
curl through it receives an HTTP 400 from the upstream path instead of an empty
reply. Temporary-copy instrumentation in `HTTP.connected` shows that the
forwarded request payload is malformed:
`b"G / H\r\nH\r\n\r\n"` instead of a normal request line and headers such as
`b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n"`.

### Update 2026-06-20 Google CONNECT

After the accepted-socket blocking and full-duplex stream relay fixes, the live
proxy served plain HTTP through both local HTTP-proxy and SOCKS5 modes. A real
HTTPS CONNECT validation against `https://www.google.com/` exposed a later
HTTP-proxy-only failure: SOCKS5 mode through `127.0.0.1:8083` reached Google
with `HTTP/2 200`, but HTTP proxy mode aborted the CONNECT tunnel. Temporary
copy instrumentation showed `HTTP.accept` created the CONNECT reply closure,
opened the remote connection, then failed inside `reply(...)` because the
captured client `writer` was `NoneType`.

The minimized shape is `accept(self, writer): async def reply(...):
writer.write(...); return await self.http_accept(reply)` plus
`http_accept(self, reply): return lambda writer: reply(...)`. Host pcc now
captures the `reply` parameter in the lambda and calls it through
`py_obj_call`. A rebuilt pcc1 still emitted a lambda body that rebuilt a
new `reply` wrapper around `__nested_reply` and used the lambda parameter as
the nested reply's `writer` capture.

## Repro
```bash
cd /Users/jiamo/python/python-proxy
PCC_GC_BACKEND=4 /Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc1 \
  -m pproxy -l http+socks5://:8081 -r socks://100.118.195.46:8087
```

In another shell:

```bash
/usr/bin/curl -sS -v -x http://127.0.0.1:8081 --max-time 30 \
  http://example.com/ -o /tmp/pcc1-proxy-http.out
```

Expected: an HTTP response through the local proxy.

Observed: curl reports `Empty reply from server`; the pcc1 process prints
`Error: pcc1 compiled module run failed`.

Temporary-copy instrumentation under `/tmp/pcc-pproxy-debug.9Jt5xp/python-proxy`
showed:

```text
PCCDBG HTTP.guess header b'GET '
PCCDBG HTTP.guess buffer-len 4
PCCDBG proto.guess matched http
PCCDBG HTTP.accept enter
PCCDBG HTTP.accept read_until 0
Error: pcc1 compiled module run failed
```

Later temporary-copy instrumentation under
`/tmp/pcc-pproxy-live-debug.RptF2o/python-proxy` showed the current live
failure:

```text
PCCDBG http.connected newpath /
PCCDBG http.connected payload b'G / H\r\nH\r\n\r\n'
```

## Test [CONFIRMED]
The live proxy/curl repro above has been observed on 2026-06-19. Later
temporary-copy instrumentation moved the live failure beyond `read_until` and
`headers.pop(0)` to pproxy's scheduler:
`next(filter(lambda o: o.alive and o.match_rule(host, port), rserver), None)`.
The focused regression
`tests/python/test_native_map_filter.py::test_filter_lambda_method_next_default_no_libpython`
first failed with `TypeError: native function got too many positional arguments`,
then with `NameError: name 'filter' is not defined`, and now passes after the
generic hoist-name and `next(filter(...), default)` fixes.

After a canonical rebuild, `pproxy --test http://example.com -r
socks://100.118.195.46:8087` succeeds with HTTP 200 through the same remote
SOCKS server. The exact long-running proxy command starts and listens, but a
local HTTP client now receives Cloudflare `400 Bad Request` because the
compiled `HTTP.connected` path sends a truncated forwarded request payload.

For the current CONNECT failure, the live proxy is started on port 8083 and
validated with:

```bash
/usr/bin/curl -sS -v -x http://127.0.0.1:8083 --max-time 45 \
  https://www.google.com/ -o /tmp/pcc1-proxy-8083-google-http.out
```

Observed before the No.11 fix: `curl: (56) Proxy CONNECT aborted`, while
`curl --socks5 127.0.0.1:8083 https://www.google.com/` reached Google with
`HTTP/2 200`.

## Proposals
- No.1 `StreamReader.readuntil` / `_fill_once` loses already-rolled-back data     [DENIED]
- No.2 pproxy monkey-patched `rollback` via `_buffer.__setitem__(slice(...), s)` corrupts buffer state     [DENIED]
- No.3 compile-time surface lacks `StreamReader.read_until`, making the pproxy monkey-patched await shape unstable     [pending]
- No.4 DynType `pop` dispatch routes `list.pop(0)` to `dict.pop` under pcc1     [CONFIRMED]
- No.5 hoisted nested-name prefix match resolves builtin `filter` to `__nested_filter_cond`     [CONFIRMED]
- No.6 `next(filter(predicate, iterable), default)` has no no-libpython lowering     [CONFIRMED]
- No.7 nested async `HTTP.connected` formats captured request strings as first characters     [CONFIRMED]
- No.8 native `urllib.parse.ParseResult` lacks `hostname`     [CONFIRMED]
- No.9 native asyncio accepted sockets inherit nonblocking state     [CONFIRMED]
- No.10 blocking asyncio channels deadlock full-duplex SOCKS5 forwarding     [CONFIRMED]
- No.11 lambda free-var analysis treats a forwarded function parameter as the hoisted nested helper under pcc1     [CONFIRMED]

## No.1 `StreamReader.readuntil` / `_fill_once` loses already-rolled-back data
### Code Change
Pending. First reduce with a program that constructs a `StreamReader`, feeds
`b"GET /abc\r\n\r\n"`, reads four bytes through the pproxy-style patched
`read_w`, rolls those bytes back through the pproxy-style patched `rollback`,
and then awaits `read_until(b"\r\n\r\n")`.

### pending
If the reduced program returns `b""`, the fix belongs in the no-libpython
`asyncio` buffer/readuntil path and should be gated by a focused stdlib test.

### DENIED
Standalone `StreamReader` reductions with both `feed_data(...)` and a real
`asyncio.open_connection(...)` socket fd returned the full header after
`read_w(4)` plus rollback. Temporary pproxy instrumentation showed
`reader.read_until(...).result()` contained the full HTTP header; the data was
not lost in `_fill_once` or `_ByteBuffer.take_until`.

## No.2 pproxy monkey-patched `rollback` via `_buffer.__setitem__(slice(...), s)` corrupts buffer state
### Code Change
Pending.

### pending
If the reduced program only fails when using the monkey-patched
`self._buffer.__setitem__(slice(0, 0), s)` rollback form, the fix should be a
generic `_ByteBuffer.__setitem__` / `slice` builtin compatibility fix, not a
pproxy branch.

### DENIED
The reduced pproxy-style monkey patch with
`self._buffer.__setitem__(slice(0, 0), s)` returned the expected bytes under
pcc1 for in-memory buffers and for a socket reader connected to a CPython
one-shot server.

## No.3 compile-time surface lacks `StreamReader.read_until`, making the pproxy monkey-patched await shape unstable
### Code Change
Added `StreamReader.read_until(...)` as a native stdlib alias for
`readuntil(...)` and extended the focused no-libpython asyncio probe to await
that alias directly. This is a generic compatibility surface: pproxy monkey
patches the same name, but code compiled before the patch should still see a
real method shape.

### pending
Focused host gate passed:
`tests/python/test_native_asyncio_stdlib_no_libpython.py::test_asyncio_stream_reader_pproxy_probe_helpers_no_libpython`.
The pcc1 rebuild and live pproxy gate are still pending.

## No.4 DynType `pop` dispatch routes `list.pop(0)` to `dict.pop` under pcc1
### Code Change
Added a dynamic `pop` runtime tag guard in `dict_lowering.py`: list receivers
use `py_list_pop`, dict receivers use `py_dict_pop` / get+delete for defaults,
and other receivers fall through to generic method dispatch. This prevents the
dict dynamic method fast path from stealing `list.pop(0)` when pcc1 loses the
static list type.

### CONFIRMED
Before the fix, host pcc emitted `py_list_pop(xs, 0)` for `xs.pop(0)`, while
the current pcc1 emitted `py_dict_pop(xs, 0)` and the resulting binary raised
`KeyError`. Added
`tests/python/test_python_list_methods_parity.py::test_dynamic_pop_dispatches_list_and_dict_by_runtime_tag`
and
`tests/python/test_pcc1_python_smoke.py::test_pcc1_dynamic_list_pop_index_does_not_dispatch_as_dict`.
The host focused tests pass; the pcc1 smoke is red until pcc1 is rebuilt from
the fixed source.

## No.5 hoisted nested-name prefix match resolves builtin `filter` to `__nested_filter_cond`
### Code Change
Restricted the fallback nested-function lookup in `name_lowering.py` so
`Name("foo")` may match `__nested_foo_<numeric collision suffix>`, but not
arbitrary names such as `__nested_foo_bar`. This preserves the collision
suffix behavior while preventing builtin or ordinary names from being captured
by longer nested function names.

### CONFIRMED
The minimized scheduler shape emitted IR where `filter(filter_cond, rserver)`
created `%filter.func = py_func_new_named(@user_p_filter_native_adapter, ...)`,
and that adapter called `@user_p___nested_filter_cond`. Runtime therefore
executed `filter_cond(filter_cond, rserver)` and raised
`TypeError: native function got too many positional arguments`. After the name
resolution fix, the same focused test no longer emitted that wrong callable and
failed at the next true gap: `NameError: name 'filter' is not defined`.

## No.6 `next(filter(predicate, iterable), default)` has no no-libpython lowering
### Code Change
Added a generic `next(filter(predicate, iterable), default)` lowering in
`iterator_builtin_lowering.py`: create an iterator, call the predicate with
each item through `py_obj_call`, return the first truthy item, and on
StopIteration clear the exception and return the default. This is not
pproxy-specific; it covers the builtin `next`/`filter` semantic shape that the
pproxy `fa` scheduler uses.

### CONFIRMED
After No.5, the focused regression exposed the real missing builtin surface as
`NameError: name 'filter' is not defined`. With the new `next(filter(...))`
lowering, `tests/python/test_native_map_filter.py` passes, including
`test_filter_lambda_method_next_default_no_libpython`.

## No.7 nested async `HTTP.connected` formats captured request strings as first characters
### Code Change
Method-body parameter binding in `class_gen.py` now honors
`_closure_boxed_params`: when hoisting rewrites a class-method parameter used
by a first-class nested closure into a one-element list cell, the method entry
block initializes that cell with the incoming argument before the rewritten
body or returned closure reads `name[0]`. `hoist_lowering.py` records class
method boxed-parameter owners under `Class.method` instead of the bare method
name.

### CONFIRMED
The temporary-copy live probe proves the malformed data appears before
`writer.write(...)`: `newpath` is correct (`/`), but the f-string payload is
`b"G / H\r\nH\r\n\r\n"`. This points at a generic captured-string,
f-string/encode, or module-runner multi-module lowering issue rather than a
remote SOCKS issue. CPython running the same `pproxy` source with the same
remote SOCKS server returns HTTP 200 through the local proxy.

The minimized class-method returned-nested-async-closure regression first
failed with the same `b"G / H\r\nH\r\n\r\n"` payload. After the fix,
`tests/python/test_async_await.py::test_nested_async_closure_formats_captured_strings`
passes under host pcc, adjacent async/closure/class focused gates pass, and a
rebuilt canonical pcc1 passes the matching pcc1 smoke regressions.

## No.8 native `urllib.parse.ParseResult` lacks `hostname`
### Code Change
Added generic derived `ParseResult` properties in `pcc/py_stdlib/urllib/parse.py`:
`username`, `password`, `hostname`, and `port`, all parsed from `netloc`.

### CONFIRMED
After No.7, the exact live proxy no longer sent the truncated HTTP request.
Temporary `/tmp` stream-handler instrumentation showed the next failure:
`AttributeError hostname` from `HTTP.http_accept` evaluating
`url.hostname`. Focused host gate
`tests/python/test_native_urllib_parse_no_libpython.py` now passes. A rebuilt
canonical no-libpython self-backend pcc1 passes
`tests/python/test_pcc1_python_smoke.py::test_pcc1_urllib_parse_result_derived_authority_attrs`,
and the exact live HTTP proxy path returns `HTTP/1.1 200 OK`.

## No.9 native asyncio accepted sockets inherit nonblocking state
### Code Change
`pcc/py_runtime/src/py_asyncio_io.c` now clears `O_NONBLOCK` on accepted
client sockets after accepting from the nonblocking listen fd. The listen fd
remains nonblocking so `_Server._accept_once()` can poll without stalling the
event loop, but stream reads on the accepted connection use blocking semantics
matching the current synchronous no-libpython asyncio subset.

### CONFIRMED
The SOCKS5 greeting path failed when the client waited for the server's
method-selection response before sending the request phase: the accepted fd
inherited nonblocking mode and `_fd_recv()` treated `EAGAIN` as
`OSError("TCP recv failed")`. The focused regression
`tests/python/test_native_asyncio_stdlib_no_libpython.py::test_asyncio_accepted_socket_waits_for_second_client_write_no_libpython`
now passes under `PCC_GC_BACKEND=4`.

## No.10 blocking asyncio channels deadlock full-duplex SOCKS5 forwarding
### Code Change
Added generic native asyncio stream-relay support:

- `py_coroutine_get_args()` exposes native coroutine argument tuples to the
  no-libpython asyncio shim.
- `py_asyncio_fd_relay()` performs select-based bidirectional fd forwarding in
  the C runtime.
- `asyncio.ensure_future()` detects two pending `StreamReader`/`StreamWriter`
  pipe coroutines whose fds are cross-connected and delegates that pair to the
  native fd relay. Non-stream tasks keep the existing behavior.

This is protocol-agnostic socket relay support; it does not special-case
`pproxy`, SOCKS5, or any package name.

### CONFIRMED
After No.9, SOCKS5 handshake and connect succeeded, but payload forwarding
still failed. Temporary `/tmp` instrumentation showed the first scheduled
`remote_to_local` channel blocked on the remote fd before the `local_to_remote`
channel could forward the client's HTTP request. The remote side eventually
closed and the client saw a reset/timeout. HTTP proxy mode avoided this only
because pproxy's HTTP accept closure pre-writes the captured request before
the two channels are scheduled.

The focused pproxy-like regression
`tests/python/test_native_asyncio_stdlib_no_libpython.py::test_asyncio_two_stream_channels_relay_full_duplex_no_libpython`
now passes. A rebuilt `build/bootstrap-pytest-self/pcc1` is no-libpython
(`otool -L ... | rg -i 'python|libpython'` produced no output), pcc1 focused
smokes pass, `pcc1 -m pproxy --test http://example.com -r
socks://100.118.195.46:8087` succeeds twice, and the exact live command:

```bash
PCC_GC_BACKEND=4 /Users/jiamo/my/pcc/build/bootstrap-pytest-self/pcc1 -m pproxy -l http+socks5://:8081 -r socks://100.118.195.46:8087
```

served both:

- HTTP proxy curl: `curl -x http://127.0.0.1:8081 http://example.com/`
  returned `HTTP/1.1 200 OK`.
- SOCKS5 proxy curl: `curl --socks5 127.0.0.1:8081 http://example.com/`
  reported `SOCKS5 request granted` and returned `HTTP/1.1 200 OK`.

## No.11 lambda free-var analysis treats a forwarded function parameter as the hoisted nested helper under pcc1
### Code Change
`lambda_helpers_lowering.py` now includes the current emitting function's
formal parameter names in the lexical shadow set used by `_lambda_free_vars`.
Those names must shadow module-level functions and hoisted nested helpers just
like `self.env` locals do. This keeps `http_accept(self, reply): return lambda
writer: reply(...)` from resolving `reply` as the older hoisted
`__nested_reply` helper when pcc1 performs the analysis.

### CONFIRMED
Host pcc now emits the correct IR for the minimized shape:
`__native_lambda_0` loads `reply` from the capture tuple and calls it through
`py_obj_call`. Before this proposal, rebuilt pcc1 emitted a lambda body that
rebuilt a new `reply` wrapper using the lambda argument as `__nested_reply`'s
`writer` capture, so the CONNECT reply saw `writer` as `NoneType`.

The first pcc1 rebuild after only the `self.env` shadow fix still failed the
focused pcc1 smoke and emitted an empty lambda capture tuple. Temporary
`PCCDBG_LAMBDA_FREE` instrumentation showed pcc1 ran `_lambda_free_vars` with
`envkeys ['__nested_reply', 'HTTP.http_accept']` and no current method params,
so method parameters had to be recovered from class-method metadata rather than
only from `env`.

The confirmed fix adds a method-function reverse lookup: when the current IR
function matches a registered class method, `_lambda_free_vars` adds that
method's formal args to the lexical shadow set. A rebuilt canonical
`build/bootstrap-pytest-self/pcc1` is no-libpython, emits the corrected lambda
IR (`reply.cap` plus `py_obj_call`), and passes the pcc1 closure smoke batch.
The background live proxy on port 8083 then served both:

- HTTP proxy CONNECT:
  `curl -x http://127.0.0.1:8083 https://www.google.com/` received
  `HTTP/1.1 200 Connection established` and Google `HTTP/2 200`.
- SOCKS5:
  `curl --socks5 127.0.0.1:8083 https://www.google.com/` reported
  `SOCKS5 request granted` and Google `HTTP/2 200`.
