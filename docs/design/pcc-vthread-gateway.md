# Design: pcc virtual-thread gateway and HTTP framework

Status: reference study and an implementation-only source pass are complete.
Focused/current-pcc1/GC/network gates have not run, so no gateway capability or
nginx-replacement claim exists yet. See the unverified checkpoint in
[`docs/goal/evidence/2026-08-13-vthread-gateway-source-implementation.md`](../goal/evidence/2026-08-13-vthread-gateway-source-implementation.md).

Reference pins and license labels:
[`docs/refs_docs/gateway-research/README.md`](../refs_docs/gateway-research/README.md).

## Decision

Pcc should own two new layers:

1. `pcc.gateway`: a virtual-thread-native network and gateway kernel. It owns
   listeners, nonblocking connections, buffers, HTTP protocol state, routing,
   reverse proxying, admission, deadlines, shutdown, and transport telemetry.
2. `pcc.web`: a typed declarative HTTP application framework above that kernel.
   It owns route declarations, parameter binding, request/response types,
   middleware, error mapping, application lifespan, and optional schema output.

An application imports `pcc.web`, declares routes, and starts the resulting
native binary directly. Nginx, Netty, Helidon, Go, h11, Starlette, and FastAPI
are concept and differential oracles. They are not compiled, embedded, launched,
linked, imported, or silently selected at runtime.

This follows the GUI precedent: absorb a coherent mechanism family into a
pcc-owned kernel and build the ergonomic framework above it. It does not mean
API compatibility or complete feature parity with the reference projects.

## Non-negotiable claim root

The feature is not complete because it works under host Python. Every landed
slice needs a current-stage gate with all relevant labels:

```text
compiler: current pcc1
backend: self
libpython: off
runtime owner: pcc-Python/freestanding production archive
GC: named backend; product canary eventually covers 0..4
network backend: Darwin kqueue, Linux epoll, or explicit poll fallback
protocol: HTTP/1.1 only until another protocol has its own gate
TLS: plaintext or named provider; never implicit
```

The existing million-virtual-thread result measures the production C scheduler.
It is valuable runtime evidence, but it does not prove the pcc1/pcc-Python
gateway. Pure host models and upstream programs are also oracle evidence only.

A valid `current pcc1` gate has three separate stages: the host compiler builds
`pcc1` from the visible source, that emitted `pcc1` compiles and links the
gateway application with `backend=self` and `libpython=off`, and the resulting
application binary serves the network workload. A host invocation such as
`uv run pcc --backend self` may be a focused oracle, but it cannot satisfy a
gateway task's pcc1 exit criterion by itself. Gate evidence must preserve the
pcc1 build identity, application link manifest and executed artifact identity.

## What exists in pcc today

### Virtual-thread substrate

- `pcc/virtual_thread.py` exposes `spawn`, carrier-pool control, yield, sleep,
  and fd parking.
- `pcc/py_frontend/codegen/native_virtual_thread.py` can create typed
  continuations for ordinary functions and resume generator state machines.
- `pcc/py_runtime/src/pcc_threads.c` has a real carrier pool, work queues,
  pooled ready/timer/I/O nodes, timer heap, kqueue/epoll/poll waitset integration,
  root handles, runtime-effect events, and pin metrics.
- `pcc/py_runtime/py/py_virtual_thread_runtime.py` now contains source for a
  bounded multi-carrier pool, rooted per-carrier queues, work stealing and pin
  accounting. This source has not yet passed current-pcc1 or GC0..4 gates.
- GC0..4 tests cover scheduler roots and relocation/update behavior. The real
  1M run is C-runtime evidence, not current-pcc1 execution evidence.

### Socket and stream substrate

- `pcc/py_runtime/py/freestanding_platform_socket.py` owns address parsing,
  numeric and `/etc/hosts` resolution, TCP listen/connect/accept, send/recv,
  shutdown, names, poll, and raw Linux syscall/named Darwin ABI routes.
- Listener, accepted and outbound descriptors now have a nonblocking
  observation ABI in source. Connect completion reads `SO_ERROR`, distinguishes
  would-block/EOF/hard failure, and preserves partial counts. None of this
  source has passed its current-pcc1 live gate.
- The production freestanding waitset and its C oracle now contain kqueue,
  Linux x86_64 epoll and an explicit poll fallback. The epoll source owns
  create/ctl/wait, one-shot/edge flags, fd-generation tokens, absolute-deadline
  EINTR handling and an eventfd interrupt. Linux AArch64 lowering and all live
  current-pcc1 gates remain open, so source presence is not a scalability
  claim.
- `pcc/py_stdlib/asyncio.py` can run small TCP stream and relay workloads, but
  it scans global server/task lists, sleeps when idle, awaits accepted handlers
  synchronously, and contains a relay-specific cooperative shortcut. It is a
  compatibility/oracle path, not the new gateway execution root.
- `pcc/py_stdlib/socket.py` is currently mostly constants and address helpers.
  `pcc/py_stdlib/ssl.py::SSLContext.wrap_socket` is unimplemented.
- `py_http_runtime.py` is a download-client surface, not an HTTP server.

`pcc.gateway` and `pcc.web` now also contain the unverified HTTP/gateway source
described by this design: retained buffers, bounded HTTP/1, declarative local
handlers, live reverse-proxy driving, asynchronous DNS, TLS listener driving,
immutable generations, admission and drain. They are not yet a proved
current-pcc1 virtual-thread HTTP server.

## First hard prerequisite: transparent parking in pcc1

The public framework should let a handler look sequential:

```python
def get_user(request: Request, user_id: int) -> Response:
    row = database.fetch(user_id)       # may park
    return Response.json(row)
```

The source implementation now propagates this effect through directly bound
functions and concretely proved local/sibling-module methods, and delegates
their child generators through rooted hidden slots. Dynamic receivers and
implicit parking dunder/property calls still fail closed. The implementation
has not been compiled or exercised by current pcc1 yet; the contract below is
therefore still a required gate rather than a completed claim.

The chosen route is a compiler-visible `may_park` effect:

1. Route, middleware, proxy, body-stream, DNS, TLS, and gateway lifecycle entry
   callbacks are roots of a closed-world effect analysis.
2. Calls to known parking operations propagate `may_park` through the compiled
   call graph.
3. Affected functions are lowered into heap-owned resumable frames at each
   parking point. Live object slots, raw values, exception/finally state, and
   resume PC are recorded in the existing continuation/root contract.
4. Socket readiness or a timer moves the virtual thread back to a ready queue;
   execution resumes after the blocking-looking operation.
5. A foreign/native call that can block but cannot be lowered is an explicit
   pinned region. Strict gateway mode may reject it at compile time; permissive
   mode records reason, duration, and carrier compensation. It is never silent.

`Event.wait`, `Condition.wait`, `Semaphore.acquire`, virtual-thread timers and
I/O observations are resumable effect roots. Plain `Lock.acquire` is not an
independent suspension root because Python property/descriptor invocation
cannot carry a continuation ABI. Gateway lifecycle/counter/snapshot locks are
therefore deliberately tiny synchronous carrier-pin regions: no lock-held code
may perform I/O, invoke an application/provider hook, or execute another park.
Pin telemetry must name and measure these regions. Methods already resumable
because they contain an Event/timer operation use the vthread lock path. This
source policy has not been exercised under contention or current pcc1.

This is Loom-like source ergonomics implemented with pcc's heap-frame model. It
does not require JVM stack chunks, and it must be implemented by pcc1 rather
than a host-only source transform.

## Layer map

```text
application code
  pcc.web App / Route / Request / Response / Middleware / Lifespan
        |
  pcc.gateway router / HTTP1 codec / proxy / admission / lifecycle
        |
  pcc.gateway Channel / BufferSegment / Deadline / Resolver / TLS provider
        |
  pcc virtual-thread may_park frames / structured scopes / scheduler roots
        |
  pcc-Python waitset + timer + socket + raw syscall/named host ABI
```

The callback pipeline inspired by Netty stays internal. Application code sees
typed sequential handlers, not channel callbacks, futures, or a user-managed
event loop.

## Execution model

### Listener and connection

- One acceptor virtual thread owns each listener. `accept` loops until it would
  block, parks on listener readability, then resumes.
- Each accepted connection gets one connection scope and one virtual thread.
  HTTP/1 keep-alive requests are handled serially in that connection by default;
  request pipelining may be parsed ahead but responses cannot reorder.
- A handler may create child virtual threads only through the connection's
  structured scope. Client close, deadline, server drain, or handler failure
  cancels owned upstream/DNS/body work before the connection root is released.
- The scheduler owns ready/timer/I/O references through the existing slot-based
  root handles. GC3/GC4 must be able to update every suspended slot.

### Carrier and platform policy

- A one-carrier pcc1 result is a functional claim only.
- Multi-carrier pcc-Python parity, bounded work stealing, exception TLS, and
  pin compensation are required before throughput claims.
- Darwin uses kqueue. Linux x86_64 has an epoll implementation in source;
  Linux AArch64 still fails closed. Poll remains an explicit compatibility
  fallback, and no backend is called scalable until its current-pcc1 gate runs.
- No busy-spin or fixed `usleep` loop is accepted as production readiness.

### Socket operations

Accepted and outbound sockets remain nonblocking. Each operation follows:

```text
try syscall -> progress/EOF/error
            -> EAGAIN: register exact fd+interest+deadline, park, retry
```

Connect completion checks the socket error after writability. Partial sends are
normal. EINTR retries without losing deadlines. Timeout, cancellation, EOF,
half-close, and hard error are distinct outcomes.

The former shared 1 KiB receive scratch is no longer used by the gateway ABI;
each observation receives caller-owned bounded storage. Its multi-carrier and
relocating-GC behavior remains unverified.

## Buffer and backpressure contract

Pcc needs a small internal channel/buffer layer before HTTP:

- pooled fixed-size `BufferSegment` owners plus bounded chains for larger data;
- `BufferView` retains its segment owner and cannot outlive it across a park;
- no raw pointer into a relocatable Python object survives a safepoint;
- inbound and outbound queues expose exact byte counts;
- each channel has configurable high and low water marks;
- crossing the high mark parks producers and pauses opposite-side reads;
- draining below the low mark wakes producers and resumes reads;
- close/cancel releases every queued segment and waiter exactly once;
- zero-copy or `sendfile` is a later optimization behind the same ownership
  contract, not a reason to expose borrowed storage.

The exact segment size and water marks remain measured configuration, not
constants frozen by this design.

## HTTP/1 codec contract

The first protocol is a pcc-Python sans-I/O state machine. Transport supplies
bytes; the codec emits typed events; the gateway controls I/O and deadlines.

Minimum events:

```text
RequestHead(method, target, version, headers)
BodyChunk(view)
RequestEnd(trailers)
ResponseHead(status, headers)
ResponseBody(view)
ResponseEnd(trailers)
ConnectionClosed(reason)
```

Required behavior includes incremental request lines/headers, bounded header
count and bytes, `Content-Length`, chunked bodies and trailers, keep-alive,
`Expect: 100-continue`, `HEAD`, response commit-once, and streaming bodies.
Request bodies are not concatenated into one unbounded `bytes` object.

For a body-bearing local route on the production native transport, dispatch
starts at `RequestHead` in one handler child. The connection continuation stays
the sole socket/parser owner and feeds a bounded, single-reader `BodyStream`.
The handler consumes it through the canonical typed
`request.read_body()` / `request.read_body_chunk()` boundary; empty reads park
on a rooted Event, feed/finish/cancel wake them, high/low watermarks bound unread
bytes, and the connection joins the child after `RequestEnd` without waiting for
another client byte. A stalled high-water reader is observed in bounded timer
slices against the absolute body deadline. The host/sans-I/O model deliberately
keeps its historical `RequestEnd` dispatch behavior.

The live connection owner asks the codec for at most one complete request at a
time and leaves later pipeline bytes in the codec-owned bounded buffer. It
flushes or commits the earlier response before decoding the next request. This
keeps a malformed later pipeline member from discarding already-decoded events
or reordering its protocol error ahead of a valid predecessor; the standalone
sans-I/O codec retains an explicit unlimited mode for differential tests.

Security rules fail closed:

- reject invalid control characters, whitespace, obsolete folding, NUL, and
  malformed CRLF;
- reject conflicting or ambiguous content lengths;
- reject unsafe `Transfer-Encoding`/`Content-Length` combinations and unknown
  request transfer codings;
- bound request line, headers, chunks, body, idle/header/body time, and queued
  output;
- normalize routing paths without changing the raw target retained for logging;
- generate parse errors without reflecting untrusted header content.

h11, nginx, Netty, Go, and RFC 9110/9112 supply differential cases, but the
security verdict is the pcc contract rather than majority behavior.

## `pcc.web` framework contract

The canonical v1 shape favors static declarations that pcc1 can freeze without
runtime signature inspection:

```python
from pcc.web import App, Request, Response, get, middleware_next, proxy

def health(request: Request) -> Response:
    return Response.json({"ok": True})

def request_id(request: Request, next_call):
    response = middleware_next(next_call)
    response.headers.append(("x-request-id", request.context["request-id"]))
    return response

app = App(routes=(
    get("/health", health),
    proxy("/api/{path*}", upstream="backend"),
), middleware=(request_id,))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

Decorator sugar can be added when it lowers to the same frozen records; it is
not a second routing system.

`MiddlewareNext` is an opaque, single-use continuation capability. The only
supported invocation boundary is `middleware_next(next_call)`. Calling
`next_call()` or its internal `_proceed()` method fails closed on both the host
model and a compiled application. This explicit helper is a directly bound
`may_park` edge: it keeps middleware code before and after the call in the
parent resumable frame while a downstream middleware or handler parks. Dynamic
dunder/method invocation is not an alternate middleware ABI.

The selected complete mechanism family is:

- method/host/path routing with exact, parameter, and tail segments;
- compile-time parameter binding to supported scalar/value types;
- typed `Request`, bounded headers/query/path parameters, streaming body, and
  cancellation/deadline context;
- `Response` constructors for bytes, text, JSON, streaming, redirect, and
  structured errors;
- ordered middleware with before/after/error behavior and exactly-once
  `middleware_next(next_call)`;
- application startup and shutdown callbacks;
- one error registry and deterministic 404/405/validation/500 behavior;
- an optional compile-time schema artifact derived from the same route table.

The first framework does not promise ASGI, WSGI, FastAPI, Starlette, Pydantic,
or decorator compatibility. An adapter may come later only if it preserves the
pcc1/self/no-libpython execution root.

## Reverse-proxy contract

Proxy routes use the same request/response streaming and backpressure path as
local handlers. The first finite proxy surface includes:

- named immutable upstream groups;
- round-robin selection with active/idle connection limits;
- keep-alive pooling and per-stage connect/header/body/idle deadlines;
- hop-by-hop header removal and trusted forwarding-header reconstruction;
- downstream disconnect cancellation;
- body and response streaming with bidirectional water-mark propagation;
- retry only before downstream response commit and only for configured safe
  methods/failure classes;
- deterministic 502/503/504 mapping and bounded error details.

The source driver now starts a statically matched proxy route at
`RequestHead`, streams each decoded body fragment upstream before
`RequestEnd`, preserves declared `Content-Length` or chunked framing and
trailers, and starts the response-header timeout only after the request is
fully written. It also probes downstream EOF while DNS, connect, upstream
write, and response-read operations are parked. The current implementation
does that with readiness parks capped at 25 ms; it is bounded and nonblocking,
but it is not a true atomic multi-fd wait and therefore is not idle-efficiency
or scalability evidence. Body-bearing local routes on a live native transport
now also start at `RequestHead` through the single-reader contract above;
host-model requests, bodyless local routes and middleware-controlled dispatch
retain the `RequestEnd` path. This source has not passed a current-pcc1 or GC
gate, so it is not execution evidence.

Numeric and `/etc/hosts` upstreams can form the first pcc1 canary. A no-nginx
general upstream claim requires a real asynchronous DNS resolver with TTL,
negative-cache, deadline, cancellation, UDP truncation/TCP fallback, and DNS
rebinding policy. DNS is not hidden behind a blocking host call.

HTTP upgrade/WebSocket, CONNECT tunneling, cache, compression, HTTP/2, HTTP/3,
QUIC, service discovery, and active health checking are later protocol/product
slices, not implied by the HTTP/1 reverse-proxy gate.

## TLS boundary

TLS is a transport stage under the same nonblocking `may_park` contract. The
current `ssl` port cannot wrap sockets, so plaintext evidence cannot be labeled
TLS-ready.

The first TLS slice uses a named external provider through a generic pcc
provider ABI, and every result must label that provider and link boundary. It must
support nonblocking handshake/read/write, SNI certificate selection, ALPN
reporting, certificate reload generations, close-notify, timeouts, and stable
error mapping. It does not make the external provider the gateway owner.

The finite v1 provider ABI is owned by `pcc.gateway.tls`. A provider registers
an immutable name, ABI version, link boundary, license identifier, security
boundary, and production-readiness label. It creates opaque certificate
contexts and connection sessions, then exposes only nonblocking
`handshake`/`read`/`write`/`close_notify` operations with fixed `OK`,
`WANT_READ`, `WANT_WRITE`, `SELECT_SNI`, `CLOSED`, or `ERROR` results. The
gateway selects SNI contexts, validates ALPN, applies absolute deadlines,
parks the virtual thread from readiness results, and owns all listener/HTTP/
scheduler/lifecycle state. The provider may not block, change socket flags,
enter the scheduler, dispatch HTTP, or mutate a published certificate
generation.

Certificate reload constructs every context for a replacement generation
before publication. New connections retain that generation; old contexts are
freed exactly once only after the publishing reference is retired and all old
channels release. A scripted provider can prove only ABI state/ownership. It
is permanently test-only and cannot satisfy an HTTPS gate. The repository now
contains an OpenSSL 3 adapter implementation, build entry, license/provenance
manifest and fixed C header under `pcc/gateway/native/`; it is an explicit
external C/libc/OpenSSL boundary and is not part of the future Linux zero-libc
claim. `GatewayServer` loads only an absolute provider-library path and rejects
missing symbols/capabilities rather than falling back to host `ssl.wrap_socket`,
`asyncio`, plaintext, or a fake provider. The adjacent manifest is source/build
provenance; the runtime does not yet authenticate a library digest against it,
so deployment artifact provenance remains an explicit product-gate boundary.
The adapter and live HTTPS path remain uncompiled and unverified.

## Routing, configuration, and lifecycle

Configuration is compiled into an immutable generation by default. Dynamic
reload, when implemented, builds and validates a new generation before an
atomic publish:

```text
NEW -> STARTING -> RUNNING -> DRAINING -> STOPPED
                    |             |
                    +----------> FAILED
```

New connections capture the current generation. Existing connections retain
the prior generation until they finish or the drain deadline expires. Listener
ownership, route tables, upstream pools, TLS contexts, middleware state, and
metrics have explicit generation retain/release rules.

`SIGTERM`/programmatic stop closes admission, cancels and observes the accept
owner, then asks active connection owners to terminate cooperatively. The
control thread may cancel request/body/handler waits and shut down a connection
descriptor, but it may not join a child or release request, admission, TLS or
continuation ownership on behalf of that connection. The listener descriptor
remains open until its parked accept registration is terminal, preventing
fd-number reuse under a stale wait registration. A separate task ledger keeps
each connection handle after its resource `finally` releases the connection
ledger; carriers stop only after the accept owner, resource ledger, and task
outcomes are all terminal. If the drain deadline is
exceeded, shutdown fails closed while preserving the carrier pool, listener,
ledger and roots; an embedding owner can retry `GatewayServer.shutdown()` after
cooperative termination, or terminate the isolated worker process. A future
reload signal cannot mutate live route tables in place. The C oracle and
pcc-Python runtime now contain a matched, retryable
waitset-dispose/reset lifecycle: only a successfully joined carrier pool closes
epoll/kqueue/eventfd ownership, while failure preserves parked I/O roots. The
restart path rebuilds registrations from those roots. This remains unverified
source until the repeated-lifecycle and current-pcc1/GC gates run.

## Observability and overload

The gateway exposes bounded, allocation-safe counters/events for:

- accepted/active/rejected connections and requests;
- ready/timer/I/O queue sizes and wake latency;
- per-reason virtual-thread pin counts and duration;
- parser errors and limit decisions;
- inbound/outbound queued bytes and water-mark parks;
- upstream selection, pool wait, retries, cancellation, and status class;
- graceful-drain state and forced cancellations;
- GC backend, pause counters, RSS, throughput, and latency percentiles in the
  long-running benchmark artifact.

Admission has independent listener connection, active request, queued request,
upstream active, and buffered-byte limits. Overload rejects or sheds work at a
named boundary; it does not wait until allocation failure.

## Ownership and five-GC law

Every managed connection owns a native-handle record plus managed slots for its
configuration generation, continuation, buffers, parser, request, response,
middleware context, and upstream lease. Scheduler and native completion queues
register updateable slots, not copied raw object addresses.

Completion, cancellation, timeout, parse failure, handler failure, proxy error,
client close, and server drain all converge on one idempotent teardown path.
Finalizers, weakrefs, resurrection, and moving collectors are not disabled to
make network gates pass.

## Claim ladder

| Level | Claim allowed |
|---|---|
| `G0` | Pinned references and reviewed design only. |
| `G1` | Current pcc1 strict self/no-libpython virtual-thread TCP echo; named waitset/GC; no carrier pin on idle I/O. |
| `G2` | Current pcc1 HTTP/1 origin server with incremental parser, limits, keep-alive, streaming, and security corpus. |
| `G3` | `pcc.web` typed routes, middleware, errors, lifespan, and streaming local handlers on G2. |
| `G4` | Current pcc1 HTTP/1 reverse proxy with bounded pools, deadlines, cancellation, header sanitation, and backpressure. |
| `G5` | Named TLS provider, async DNS, immutable reload generation, graceful drain, and GC0..4 product canary. |
| `G6` | Long-running comparative gateway evidence: throughput, p50/p95/p99, RSS, GC pauses, pinning, overload, malformed traffic, and graceful restart. |

Only `G5+` supports the bounded statement that a covered deployment can run
without nginx. None of these levels means all nginx modules, configuration, or
protocols. “Nginx replacement” remains prohibited without an explicit feature
matrix and a workload-specific claim.

## Downstream task graph

```text
REFERENCE-ARCHITECTURE
  -> VTHREAD-PARK-EFFECT
       -> PCC1-CARRIER-PARITY
       -> NONBLOCKING-SOCKET-WAITSET
            -> BUFFER-BACKPRESSURE
                 -> HTTP1-CODEC
                      -> WEB-FRAMEWORK
                      -> REVERSE-PROXY -> ASYNC-DNS
                 -> TLS-PROVIDER
  -> LIFECYCLE-OBSERVABILITY (joins framework/proxy/DNS/TLS)
       -> PCC1-PRODUCT-CANARY
```

Each row is finite in `docs/goal/task-board.yaml`. Broad performance or five-GC
gates run only after focused current-pcc1 functional gates are green and the
source is frozen.

## Explicit non-goals for the first product canary

- compiling or launching nginx;
- host-Python or libpython service execution;
- public Netty callbacks/futures or a user-managed event loop;
- ASGI/WSGI compatibility;
- HTTP/2, HTTP/3, QUIC, WebSocket, CONNECT, mail/stream proxy, cache, WAF, or
  arbitrary nginx configuration/module compatibility;
- a performance claim from the existing C-runtime 1M virtual-thread gate;
- weakening Python semantics, GC roots, or self-backend ownership for latency.
