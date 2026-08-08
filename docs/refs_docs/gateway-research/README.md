# Virtual-thread gateway reference pins

This note records the upstream source shapes reviewed for the pcc virtual-thread
gateway and HTTP-framework design. The full external repositories live under
`~/pcc_refs`; they are references and behavioral oracles only. No source below
is vendored, imported, linked, executed, or treated as the production owner of
`pcc.gateway` or `pcc.web`.

The production claim root is a current `pcc1` binary using `--backend=self` and
`--python-libpython=off`. Host Python, upstream test programs, and pcc's existing
C-runtime virtual-thread benchmark may serve as oracles, but cannot prove that
claim.

Here `pcc1` means the first native compiler emitted from the visible repository
source. It must itself compile/link the gateway application, after which that
application artifact runs the workload. Merely compiling the application with
the host `uv run pcc` driver does not meet this boundary.

## nginx

- Repository/revision: `nginx/nginx@8d9666701d61105d46baed32eba2939599132d7e`
- Local reference: `~/pcc_refs/nginx-full-depth1`
- License: BSD-2-Clause
- Reviewed anchors:
  - `src/event/ngx_event.c`: event/timer driving
  - `src/event/ngx_event_accept.c`: connection admission
  - `src/http/ngx_http_request.c`: incremental request lifecycle
  - `src/http/ngx_http_parse.c`: HTTP/1 parsing
  - `src/http/ngx_http_core_module.c`: phase engine and route selection
  - `src/http/ngx_http_upstream.c`: upstream state and retry/finalization
  - `src/http/modules/ngx_http_proxy_module.c`: reverse-proxy policy
  - `src/http/modules/ngx_http_limit_req_module.c`: request admission
  - `src/os/unix/ngx_process_cycle.c`: generation/reload/drain lifecycle

Pcc borrows the bounded connection/request/upstream lifecycle, immutable
configuration-generation idea, admission control, streaming proxy shape, and
graceful drain. It does not copy nginx's callback-based public programming
model, C module ABI, directive syntax, process model, or source.

## OpenJDK Loom

- Repository/revision: `openjdk/jdk@b9778ccb475891efd6347f7645b9a53c011f70fd`
- Local snapshot: `docs/refs_docs/gc-research/user-mode-scheduling/openjdk/`
- License: GPL-2.0-only WITH Classpath-exception-2.0
- Reviewed anchors:
  - `VirtualThread.java`, `Continuation.java`, `StackChunk.java`
  - `Poller.java`, `NioSocketImpl.java`, `SocketChannelImpl.java`
  - `Blocker.java`, `CarrierThread.java`, `LockSupport.java`
  - `continuationFreezeThaw.cpp`

Pcc borrows transparent park/unpark source ergonomics, heap-owned suspended
state, explicit mounted/unmounted ownership, poller integration, and visible
pinning boundaries. Pcc does not require JVM-compatible stacks, classes,
executors, or APIs.

## Go runtime and net/http

- Repository/revision: `golang/go@6a0f8b7c91664a458339a7f47a07dda512845fde`
- Local reference: `~/pcc_refs/go-net-http-depth1`
- License: BSD-3-Clause
- Reviewed anchors:
  - `src/runtime/netpoll.go`, `netpoll_kqueue.go`, `netpoll_epoll.go`
  - `src/internal/poll/fd_poll_runtime.go`
  - `src/net/http/server.go`: `Server.Serve`, `conn.serve`
  - `src/net/http/transport.go`: `Transport.roundTrip`
  - `src/net/http/httputil/reverseproxy.go`: `ReverseProxy.ServeHTTP`

Pcc borrows the blocking-looking per-connection handler shape backed by a
runtime poller, deadlines/cancellation, connection-state lifecycle, streaming
transport, hop-by-hop header removal, and client-disconnect propagation. It
does not copy goroutine stacks, Go interfaces, `context.Context`, or the
`net/http` API.

## Netty

- Repository/revision: `netty/netty@dd7c9dc102bda163cd4966b6e9a45c57f42ebfb9`
- Local reference: `~/pcc_refs/netty-full-depth1`
- License: Apache-2.0
- Reviewed anchors:
  - `transport/.../ChannelPipeline.java`, `DefaultChannelPipeline.java`
  - `transport/.../ChannelOutboundBuffer.java`, `WriteBufferWaterMark.java`
  - `buffer/.../ByteBuf.java`
  - `codec-http/.../HttpObjectDecoder.java`, `HttpServerCodec.java`
  - `handler/.../ssl/SslHandler.java`
  - NIO, epoll, and kqueue event-loop implementations

Pcc borrows a fixed inbound/outbound stage pipeline, explicit buffer ownership,
incremental decoder boundaries, high/low write-water marks, and TLS as a
transport stage. It deliberately rejects Netty's callback/future/event-loop
surface as the pcc application API; those mechanics remain below sequential
virtual-thread handlers.

## Helidon virtual-thread web server

- Repository/revision: `helidon-io/helidon@c5c14d66a98a3a9f438fd2708d2f9fb1b2222580`
- Local reference: `~/pcc_refs/helidon-full-depth1`
- License: Apache-2.0
- Reviewed anchors:
  - `webserver/.../LoomServer.java`
  - `webserver/.../ConnectionHandler.java`
  - `webserver/.../ListenerConfigBlueprint.java`
  - `webserver/.../http1/Http1Connection.java`
  - `webserver/.../http1/Http1ServerRequest.java`
  - `webserver/.../http1/Http1ServerResponse.java`
  - `webserver/.../http/HttpRouting.java`

Pcc borrows the direct virtual-thread-per-connection/request product shape,
separate connection and request admission limits, blocking stream ergonomics,
and explicit start/stop lifecycle. It does not inherit Helidon's Java service
registry, builder API, protocol SPI, or executor types.

## h11

- Repository/revision: `python-hyper/h11@62c5068c971579d61fa1b55373390e12f25fd856`
- Local reference: `~/pcc_refs/h11-full-depth1`
- License: MIT
- Reviewed anchors:
  - `h11/_connection.py`: transport-free connection state machine
  - `h11/_state.py`: legal local/remote transitions
  - `h11/_events.py`: request/response/data/end events
  - `h11/_readers.py`, `h11/_writers.py`: incremental codec boundaries

Pcc borrows the sans-I/O protocol split and explicit state/event vocabulary.
It does not promise h11 API compatibility or use h11 at runtime.

## Starlette

- Repository/revision: `encode/starlette@398e5a3430eb1ddd33e1d48d766efe41426e231f`
- Local reference: `~/pcc_refs/starlette-full-depth1`
- License: BSD-3-Clause
- Reviewed anchors:
  - `starlette/applications.py`
  - `starlette/routing.py`: `Route`, `Router`
  - `starlette/requests.py`: request/body stream
  - `starlette/responses.py`: ordinary and streaming responses
  - `starlette/middleware/base.py`: request middleware boundary

Pcc borrows a small application/router/request/response/middleware vocabulary
and a separate lifespan boundary. It does not make ASGI or asyncio the
production execution root and does not claim Starlette compatibility.

## FastAPI

- Repository/revision: `fastapi/fastapi@f336ff831c4af3d4f625c2593a27b1e0cae93eb7`
- Local reference: `~/pcc_refs/fastapi-full-depth1`
- License: MIT
- Reviewed anchors:
  - `fastapi/applications.py`: `FastAPI`, `add_api_route`
  - `fastapi/routing.py`: `APIRoute`, `APIRouter`
  - `fastapi/dependencies/utils.py`: dependency graph construction
  - `fastapi/openapi/utils.py`: schema derivation

Pcc borrows typed declarative route registration, compile-time parameter
binding, and an optional schema artifact. It does not depend on Pydantic,
runtime signature inspection, ASGI, FastAPI decorators, or FastAPI wire/API
compatibility.

## Absorption rule

The intended layering is:

```text
pcc.web typed application framework
        -> pcc.gateway HTTP/router/proxy/lifecycle kernel
        -> pcc virtual-thread effects + buffers + waitset
        -> freestanding pcc-Python socket/syscall substrate
```

The references validate mechanisms and test cases. They never become a silent
runtime fallback. Any copied source would require a separate attribution and
license review; this design assumes clean pcc-owned implementations derived
from documented behavior and independently written tests.
