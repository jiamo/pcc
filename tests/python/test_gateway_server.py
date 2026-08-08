"""Gateway listener/connection, proxy, DNS and native-TLS wiring contracts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import pcc.gateway.server as gateway_server_module
import pcc.virtual_thread as virtual_thread
import pcc.web.models as web_models

from pcc.gateway.config import BufferLimits, Http1Limits, ListenerConfig
from pcc.gateway.lifecycle import (
    AdmissionLimits,
    GatewayLifecycle,
    STATE_FAILED,
    STATE_STOPPED,
)
from pcc.gateway.dns import (
    DNS_A,
    DNS_IO_OK,
    DNS_IO_WOULD_BLOCK,
    DnsIoResult,
    DnsResolverConfig,
    DnsServer,
    HostsTable,
    Resolver,
)
from pcc.gateway.proxy import UpstreamEndpoint, UpstreamGroup
from pcc.gateway.proxy import ProxyTimeouts, RetryPolicy
from pcc.gateway.proxy_http1 import UpstreamConnectionPool
from pcc.gateway.server import (
    PCC_IO_READ,
    PCC_IO_WRITE,
    PCC_SOCKET_CONNECTED,
    PCC_SOCKET_EOF,
    PCC_SOCKET_PROGRESS,
    PCC_SOCKET_WOULD_BLOCK,
    GatewayConfig,
    GatewayConnection,
    GatewayError,
    GatewayHooks,
    GatewayServer,
    NativeVirtualThreadScheduler,
    UnsupportedGatewayFeature,
)
from pcc.gateway.tls import (
    PCC_NATIVE_TLS_PROVIDER_NAME,
    TLS_CLOSED,
    TLS_OK,
    TLS_PROVIDER_ABI_VERSION,
    TLS_WANT_READ,
    TLS_WANT_WRITE,
    TlsCertificate,
    TlsConfig,
    TlsProviderError,
    TlsProviderRegistry,
    TlsResult,
)
from pcc.web import App, BodyStream, Request, Response, get, post, proxy
from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]
PCC1_PRODUCT_SOURCE = (
    REPO / "tests" / "fixtures" / "gateway" / "current_pcc1_gateway_app.py"
)


class RecordingHooks(GatewayHooks):
    def __init__(self) -> None:
        self.events = []

    def connection_opened(self, connection) -> None:
        self.events.append(("connection-opened", connection.fd))

    def connection_closed(self, connection, reason: str) -> None:
        self.events.append(("connection-closed", reason))

    def request_started(self, connection, request) -> None:
        self.events.append(("request-started", request.target))

    def request_finished(self, connection, request, response) -> None:
        self.events.append(("request-finished", response.status))

    def backpressure_changed(
        self, connection, enabled: bool, queued_bytes: int
    ) -> None:
        self.events.append(("backpressure", enabled, queued_bytes))

    def deadline_exceeded(self, connection, phase: str) -> None:
        self.events.append(("deadline", phase))

    def dns_started(self, connection, host: str) -> None:
        self.events.append(("dns-started", host))

    def dns_finished(
        self, connection, host: str, address_count: int, error: str
    ) -> None:
        self.events.append(("dns-finished", host, address_count, error))

    def tls_handshake_started(self, connection, channel) -> None:
        self.events.append(("tls-handshake-started", channel.provider_name))

    def tls_handshake_completed(self, connection, channel) -> None:
        self.events.append(("tls-handshake-completed", channel.alpn))

    def tls_handshake_failed(self, connection, error_name: str) -> None:
        self.events.append(("tls-handshake-failed", error_name))

    def tls_closed(self, connection, graceful: bool) -> None:
        self.events.append(("tls-closed", graceful))


class FakeTransport:
    native_virtual_threads = False
    local_body_streaming = False

    def __init__(self) -> None:
        self.clock_ms = 100
        self.listener_fd = 10
        self.accepts = []
        self.reads = []
        self.waits = []
        self.idle_waits = []
        self.closed = []
        self.shutdowns = []
        self.written = bytearray(b"")
        self.write_limit = 7
        self.block_next_write = False

    def now_ms(self) -> int:
        return self.clock_ms

    def listen(
        self, host: str, port: int, reuse_port: bool, backlog: int = 128
    ) -> int:
        self.listen_args = (host, port, reuse_port, backlog)
        return self.listener_fd

    def accept(self, listener_fd: int):
        if self.accepts:
            return self.accepts.pop(0)
        return PCC_SOCKET_WOULD_BLOCK, -1, ""

    def read(self, fd: int, limit: int):
        if self.reads:
            return self.reads.pop(0)
        return PCC_SOCKET_WOULD_BLOCK, b""

    def write(self, fd: int, data: bytes):
        if self.block_next_write:
            self.block_next_write = False
            return PCC_SOCKET_WOULD_BLOCK, 0
        count = min(self.write_limit, len(data))
        self.written.extend(data[:count])
        return PCC_SOCKET_PROGRESS, count

    def wait(self, fd: int, events: int, timeout_ms: int) -> None:
        self.waits.append((fd, events, timeout_ms))
        self.clock_ms += timeout_ms

    def shutdown(self, fd: int) -> int:
        self.shutdowns.append(fd)
        return 0

    def close(self, fd: int) -> int:
        self.closed.append(fd)
        return 0

    def idle_wait(self, delay_ms: int) -> None:
        self.idle_waits.append(delay_ms)
        self.clock_ms += delay_ms


class ProxyFakeTransport(FakeTransport):
    """Deterministic two-sided transport; it never borrows host networking."""

    def __init__(self) -> None:
        super().__init__()
        self.open_results = [70]
        self.connect_results = [PCC_SOCKET_CONNECTED]
        self.upstream_reads = []
        self.upstream_written = bytearray(b"")
        self.opened_endpoints = []
        self.on_wait = None

    def open_upstream(self, endpoint, address="") -> int:
        self.opened_endpoints.append((address or endpoint.host, endpoint.port))
        if not self.open_results:
            return -1
        return self.open_results.pop(0)

    def connect_observe(self, fd: int) -> int:
        if self.connect_results:
            return self.connect_results.pop(0)
        return PCC_SOCKET_CONNECTED

    def read(self, fd: int, limit: int):
        if fd >= 70:
            if self.upstream_reads:
                return self.upstream_reads.pop(0)
            return PCC_SOCKET_WOULD_BLOCK, b""
        return super().read(fd, limit)

    def write(self, fd: int, data: bytes):
        if fd >= 70:
            count = min(9, len(data))
            self.upstream_written.extend(data[:count])
            return PCC_SOCKET_PROGRESS, count
        return super().write(fd, data)

    def wait(self, fd: int, events: int, timeout_ms: int) -> None:
        self.waits.append((fd, events, timeout_ms))
        if self.on_wait is not None:
            self.on_wait(fd, events)
        self.clock_ms += timeout_ms


class ReadyFakeTransport(FakeTransport):
    """A readiness notification returns before its absolute deadline."""

    def wait(self, fd: int, events: int, timeout_ms: int) -> None:
        self.waits.append((fd, events, timeout_ms))
        self.clock_ms += 1


class ProxyDnsTransport(ProxyFakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.next_dns_handle = 50
        self.dns_receive_results = []
        self.dns_closed = []

    def open(self, protocol, server):
        handle = self.next_dns_handle
        self.next_dns_handle += 1
        return DnsIoResult(DNS_IO_OK, handle=handle)

    def connect(self, handle, server):
        return DnsIoResult(DNS_IO_OK, handle=handle)

    def send(self, handle, data, offset):
        return DnsIoResult(
            DNS_IO_OK, handle=handle, count=len(data) - offset
        )

    def receive(self, handle, max_bytes):
        if not self.dns_receive_results:
            return DnsIoResult(DNS_IO_WOULD_BLOCK, handle=handle)
        result = self.dns_receive_results.pop(0)
        result.handle = handle
        return result

    def close(self, handle):
        if 50 <= handle < 70:
            self.dns_closed.append(handle)
            return 0
        return super().close(handle)


class FakeScheduler:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.spawned = []
        self.cancelled = []
        self.accept_thread = None
        self.cancel_completes = True
        self.result_value = None
        self.result_calls = []
        self.join_calls = []
        self.events = []
        self.outcome_values = []
        self.outcome_value = virtual_thread.OUTCOME_PENDING
        self.exception_value = None

    def start(self, carrier_count: int) -> int:
        self.started = carrier_count
        self.events.append(("start", carrier_count))
        return carrier_count

    def stop(self) -> int:
        self.events.append(("stop",))
        self.stopped += 1
        return 0

    def spawn_accept(self, server):
        self.spawned.append(("accept", server))
        self.accept_thread = len(self.spawned)
        return self.accept_thread

    def spawn_connection(self, server, connection):
        self.spawned.append(("connection", server, connection))
        return len(self.spawned)

    def cancel(self, thread) -> bool:
        self.cancelled.append(thread)
        self.events.append(("cancel", thread))
        if (
            thread == self.accept_thread
            and self.cancel_completes
            and self.outcome_value == virtual_thread.OUTCOME_PENDING
        ):
            self.outcome_value = virtual_thread.OUTCOME_CANCELLED
        return True

    def join(self, thread):
        self.join_calls.append(thread)
        return self.result_value

    def result(self, thread):
        self.result_calls.append(thread)
        return self.result_value

    def outcome(self, thread) -> int:
        self.events.append(("outcome", thread))
        if self.outcome_values:
            return self.outcome_values.pop(0)
        return self.outcome_value

    def exception(self, thread):
        return self.exception_value


def test_native_scheduler_exposes_join_and_terminal_result(monkeypatch) -> None:
    calls = []

    def join(thread):
        calls.append(("join", thread))
        return "joined"

    def result(thread):
        calls.append(("result", thread))
        return "returned"

    monkeypatch.setattr(virtual_thread, "join", join)
    monkeypatch.setattr(virtual_thread, "result", result)
    scheduler = NativeVirtualThreadScheduler()

    assert scheduler.join("accept-owner") == "joined"
    assert scheduler.result("accept-owner") == "returned"
    assert calls == [
        ("join", "accept-owner"),
        ("result", "accept-owner"),
    ]


class ForbiddenProcessControl:
    def install(self) -> None:
        raise AssertionError("disabled process control must not install")

    def poll(self):
        raise AssertionError("disabled process control must not poll")

    def uninstall(self) -> None:
        raise AssertionError("disabled process control must not uninstall")


class StopOnceProcessControl:
    def __init__(self) -> None:
        self.installed = 0
        self.polled = 0
        self.uninstalled = 0

    def install(self) -> None:
        self.installed += 1

    def poll(self):
        self.polled += 1

        class StopPoll:
            reload_requested = False
            stop_requested = True

        return StopPoll()

    def uninstall(self) -> None:
        self.uninstalled += 1


class ClosePathTlsChannel:
    """TLS owner double for finalization failure injection only."""

    def __init__(self, close_notify_results=()) -> None:
        self.handshake_complete = False
        self.failed = False
        self.released = False
        self.alpn = "http/1.1"
        self.close_notify_results = list(close_notify_results)
        self.close_calls = 0
        self.raise_close_notify = False
        self.raise_close = False

    def handshake(self, now_ms: int, deadline_ms: int):
        self.handshake_complete = True
        return TlsResult(TLS_OK)

    def close_notify(self, now_ms: int, deadline_ms: int):
        if self.raise_close_notify:
            raise RuntimeError("TLS close-notify provider failed")
        if self.close_notify_results:
            return self.close_notify_results.pop(0)
        return TlsResult(TLS_CLOSED)

    def close(self):
        if self.released:
            return TlsResult(TLS_CLOSED)
        self.released = True
        self.close_calls += 1
        if self.raise_close:
            raise RuntimeError("TLS session close failed")
        return TlsResult(TLS_CLOSED)


class FailingWaitTransport(FakeTransport):
    def wait(self, fd: int, events: int, timeout_ms: int) -> None:
        self.waits.append((fd, events, timeout_ms))
        raise RuntimeError("TLS close wait failed")


class FailingOwnerTransport(FakeTransport):
    def shutdown(self, fd: int) -> int:
        self.shutdowns.append(fd)
        raise RuntimeError("socket shutdown failed")

    def close(self, fd: int) -> int:
        self.closed.append(fd)
        raise RuntimeError("socket close failed")


class ListenerWiringTlsProvider:
    """No-crypto listener test double; never evidence for an HTTPS claim."""

    name = "listener-wiring-test"
    abi_version = TLS_PROVIDER_ABI_VERSION
    native_abi = "pcc-tls-native-provider-v1"
    implementation_id = "listener-wiring-no-crypto-test-only"
    link_boundary = "test-object:no-native-crypto;no-libpython"
    license_id = "test-code-only"
    security_boundary = "state-machine-only:no-tls-wire"
    # GatewayServer enforces the production label.  This double uses it only
    # inside this test module so the product path cannot grow a bypass flag.
    production_ready = True

    def __init__(self) -> None:
        self.reads = []
        self.handshake_results = [TlsResult(TLS_OK)]
        self.written = bytearray(b"")
        self.contexts_freed = 0
        self.connections_freed = 0

    def create_server_context(self, config, certificate):
        return {"certificate": certificate.identifier}

    def new_connection(self, context, fd):
        return {"context": context, "fd": fd}

    def handshake(self, session):
        return self.handshake_results.pop(0)

    def set_server_context(self, session, context):
        session["context"] = context
        return TlsResult(TLS_OK)

    def selected_alpn(self, session):
        return "http/1.1"

    def read(self, session, output, limit):
        if not self.reads:
            return TlsResult(TLS_CLOSED)
        data = self.reads.pop(0)
        count = min(limit, len(data))
        output[:count] = data[:count]
        return TlsResult(TLS_OK, count=count)

    def write(self, session, data, length):
        self.written.extend(data[:length])
        return TlsResult(TLS_OK, count=length)

    def close_notify(self, session):
        return TlsResult(TLS_CLOSED)

    def free_connection(self, session):
        self.connections_freed += 1

    def free_context(self, context):
        self.contexts_freed += 1


def _declared_tls_provider_artifact(provider, path, digest, max_bytes) -> None:
    provider.library_path = path
    provider.expected_library_sha256 = digest
    provider.verified_library_sha256 = digest
    provider.library_max_bytes = max_bytes


def _connection(app, config=None, transport=None, hooks=None):
    if config is None:
        config = GatewayConfig()
    lifecycle = GatewayLifecycle(config, config.admission)
    lifecycle.start()
    lifecycle.started()
    assert lifecycle.admit_connection()
    generation = lifecycle.acquire_generation()
    connection = GatewayConnection(
        app,
        -1 if transport is None else 41,
        transport,
        lifecycle,
        generation,
        config,
        hooks,
        "127.0.0.1",
    )
    return connection, lifecycle, generation


def _release_connection(connection, lifecycle, generation) -> None:
    connection.close("test-complete")
    generation.release()
    lifecycle.release_connection()


def _proxy_connection(
    app,
    transport,
    config=None,
    hooks=None,
    resolver=None,
    dns_transport=None,
):
    connection, lifecycle, generation = _connection(
        app, config, transport, hooks
    )
    pools = {}
    for name, group in app.upstreams.items():
        pools[name] = UpstreamConnectionPool(
            group, close_connection=transport.close
        )
    connection.proxy_pools = pools
    connection.resolver = resolver
    connection.dns_transport = dns_transport
    return connection, lifecycle, generation


def _dns_a_response(query_id: int, name: str, address=(192, 0, 2, 80)) -> bytes:
    from pcc.gateway.dns import build_query

    query = build_query(query_id, name, DNS_A)
    output = bytearray(query[:2])
    output.extend(b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00")
    output.extend(query[12:])
    output.extend(b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x1e\x00\x04")
    output.extend(bytes(address))
    return bytes(output)


def test_fragmented_http_dispatch_and_keep_alive_pipeline() -> None:
    def health(request: Request):
        return Response.text("healthy")

    def echo(request: Request):
        return Response.bytes(request.read_body())

    app = App(routes=(get("/health", health), post("/echo", echo)))
    connection, lifecycle, generation = _connection(app)

    assert connection.feed_data(b"GET /hea") == 0
    assert connection.take_output() == b""
    events = connection.feed_data(
        b"lth HTTP/1.1\r\nHost: local\r\n\r\n"
        b"POST /echo HTTP/1.1\r\nHost: local\r\nContent-Length: 4\r\n\r\npcc1"
    )
    assert events == 5
    output = connection.take_output()
    assert output.count(b"HTTP/1.1 200 OK") == 2
    assert b"healthy" in output
    assert output.endswith(b"pcc1")
    assert lifecycle.metrics.get("requests_started") == 2
    assert lifecycle.metrics.get("requests_active") == 0
    assert lifecycle.metrics.get("buffered_bytes") == 0
    _release_connection(connection, lifecycle, generation)


def test_connection_close_request_does_not_dispatch_pipelined_followup() -> None:
    calls = []

    def handler(request: Request):
        calls.append(request.target)
        return "ok"

    app = App(routes=(get("/first", handler), get("/second", handler)))
    connection, lifecycle, generation = _connection(app)
    events = connection.feed_data(
        b"GET /first HTTP/1.1\r\nHost: local\r\nConnection: close\r\n\r\n"
        b"GET /second HTTP/1.1\r\nHost: local\r\n\r\n"
    )
    assert events == 2
    assert calls == ["/first"]
    assert connection.take_output().count(b"HTTP/1.1 200 OK") == 1
    _release_connection(connection, lifecycle, generation)


def test_live_one_message_decode_preserves_first_response_before_bad_pipeline() -> None:
    calls = []

    def handler(request: Request):
        calls.append(request.target)
        return "first-ok"

    app = App(routes=(get("/first", handler),))
    connection, lifecycle, generation = _connection(app)
    data = (
        b"GET /first HTTP/1.1\r\nHost: local\r\n\r\n"
        b"GET /broken HTTP/1.1\r\nBad Header\r\n\r\n"
    )

    assert connection.feed_data(data, 1) == 2
    first = connection.take_output()
    assert first.startswith(b"HTTP/1.1 200 OK")
    assert first.endswith(b"first-ok")
    assert calls == ["/first"]

    assert connection.feed_data(b"", 1) == -1
    second = connection.take_output()
    assert second.startswith(b"HTTP/1.1 400 Bad Request")
    assert calls == ["/first"]
    _release_connection(connection, lifecycle, generation)


def test_routing_normalization_failure_is_a_named_http_400() -> None:
    app = App(routes=(get("/", lambda request: "unreachable"),))
    connection, lifecycle, generation = _connection(app)

    assert connection.feed_data(
        b"GET /../../escape HTTP/1.1\r\nHost: local\r\n\r\n"
    ) == -1
    output = connection.take_output()
    assert output.startswith(b"HTTP/1.1 400 Bad Request")
    assert b"path escapes routing root" in output
    assert lifecycle.metrics.get("parser_errors") == 1
    _release_connection(connection, lifecycle, generation)


def test_response_backpressure_high_low_transitions_are_observable() -> None:
    hooks = RecordingHooks()
    config = GatewayConfig(
        buffers=BufferLimits(
            segment_bytes=32,
            low_watermark=16,
            high_watermark=64,
            connection_bytes=1024,
        ),
    )
    app = App(routes=(get("/large", lambda request: b"x" * 200),))
    connection, lifecycle, generation = _connection(app, config, hooks=hooks)

    connection.feed_data(b"GET /large HTTP/1.1\r\nHost: local\r\n\r\n")
    assert len(connection.output) > config.buffers.high_watermark
    assert ("backpressure", True, len(connection.output)) in hooks.events
    output = connection.take_output()
    assert output.endswith(b"x" * 200)
    assert any(event[:2] == ("backpressure", False) for event in hooks.events)
    assert lifecycle.metrics.get("backpressure_parks") == 1
    _release_connection(connection, lifecycle, generation)


def test_streaming_response_is_incremental_and_bounded_by_connection_watermark() -> None:
    config = GatewayConfig(
        buffers=BufferLimits(
            segment_bytes=32,
            low_watermark=32,
            high_watermark=64,
            connection_bytes=128,
        ),
    )
    source_chunks = [b"a" * 91, b"b" * 77]
    app = App(
        routes=(
            get("/stream", lambda request: Response.stream(source_chunks)),
        )
    )
    connection, lifecycle, generation = _connection(app, config)

    connection.feed_data(b"GET /stream HTTP/1.1\r\nHost: local\r\n\r\n")
    assert connection.pending_stream_request is not None
    assert len(connection.output) <= config.buffers.connection_bytes
    wire = bytearray(connection.take_output())
    while connection.pending_stream_request is not None:
        connection._drive_streaming_response()
        assert len(connection.output) <= config.buffers.connection_bytes
        wire.extend(connection.take_output())

    assert b"Transfer-Encoding: chunked" in wire
    assert wire.endswith(b"0\r\n\r\n")
    assert wire.count(b"a") >= 91
    assert wire.count(b"b") >= 77
    assert lifecycle.metrics.get("requests_active") == 0
    _release_connection(connection, lifecycle, generation)


def test_connection_transport_parks_read_and_write_without_blocking_carrier() -> None:
    hooks = RecordingHooks()
    transport = FakeTransport()
    transport.block_next_write = True
    transport.reads = [
        (PCC_SOCKET_WOULD_BLOCK, b""),
        (
            PCC_SOCKET_PROGRESS,
            b"GET / HTTP/1.1\r\nHost: local\r\nConnection: close\r\n\r\n",
        ),
    ]
    app = App(routes=(get("/", lambda request: "ok"),))
    connection, lifecycle, generation = _connection(
        app, transport=transport, hooks=hooks
    )

    connection.run()
    assert bytes(transport.written).endswith(b"ok")
    assert transport.waits[0][1] == PCC_IO_READ
    assert any(wait[1] == PCC_IO_WRITE for wait in transport.waits)
    assert transport.closed == [41]
    assert hooks.events[0] == ("connection-opened", 41)
    assert hooks.events[-1] == ("connection-closed", "response-complete")
    generation.release()
    lifecycle.release_connection()


def test_partial_request_deadline_cancels_and_returns_408() -> None:
    hooks = RecordingHooks()
    transport = FakeTransport()
    config = GatewayConfig(
        http1=Http1Limits(idle_timeout_ms=100, body_timeout_ms=5),
    )
    transport.reads = [
        (
            PCC_SOCKET_PROGRESS,
            b"POST / HTTP/1.1\r\nHost: local\r\nContent-Length: 4\r\n\r\nx",
        ),
        (PCC_SOCKET_WOULD_BLOCK, b""),
        (PCC_SOCKET_WOULD_BLOCK, b""),
    ]
    app = App(routes=(post("/", lambda request: "never"),))
    connection, lifecycle, generation = _connection(
        app, config, transport, hooks
    )

    connection.run()
    assert b"HTTP/1.1 408 " in bytes(transport.written)
    assert ("deadline", "request") in hooks.events
    assert lifecycle.metrics.get("requests_active") == 0
    generation.release()
    lifecycle.release_connection()


def test_proxy_plan_fails_closed_without_outgoing_transport() -> None:
    upstream = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),),
        upstreams=(upstream,),
    )
    connection, lifecycle, generation = _connection(app)
    connection.feed_data(b"GET /api/items HTTP/1.1\r\nHost: local\r\n\r\n")
    output = connection.take_output()
    assert b"HTTP/1.1 503 Service Unavailable" in output
    assert output.endswith(b"proxy transport unavailable")
    _release_connection(connection, lifecycle, generation)


def test_live_proxy_parks_connect_write_read_and_streams_response() -> None:
    hooks = RecordingHooks()
    transport = ProxyFakeTransport()
    transport.connect_results = [
        PCC_SOCKET_WOULD_BLOCK,
        PCC_SOCKET_CONNECTED,
    ]
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"POST /api/items HTTP/1.1\r\n"
        b"Host: front.example\r\nContent-Length: 4\r\n"
        b"Connection: close\r\n\r\npcc1",
    )]
    transport.upstream_reads = [
        (PCC_SOCKET_WOULD_BLOCK, b""),
        (
            PCC_SOCKET_PROGRESS,
            b"HTTP/1.1 201 Created\r\nContent-Length: 6\r\n"
            b"Connection: close\r\nX-Upstream: pcc1\r\n\r\nproxy!",
        ),
    ]
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9000),)
    )
    app = App(
        routes=(
            proxy(
                "/api/{path*}",
                "backend",
                method="POST",
                strip_prefix="/api",
            ),
        ),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(
        app, transport, hooks=hooks
    )

    connection.run()

    assert transport.opened_endpoints == [("127.0.0.1", 9000)]
    upstream = bytes(transport.upstream_written)
    assert upstream.startswith(b"POST /items HTTP/1.1\r\n")
    assert b"host: 127.0.0.1:9000\r\n" in upstream
    assert upstream.endswith(b"\r\npcc1")
    downstream = bytes(transport.written)
    assert downstream.startswith(b"HTTP/1.1 201 Created\r\n")
    assert downstream.endswith(b"proxy!")
    assert any(
        fd == 70 and events == PCC_IO_WRITE and 0 < timeout_ms <= 25
        for fd, events, timeout_ms in transport.waits
    )
    assert any(fd == 70 and events == PCC_IO_READ for fd, events, _ in transport.waits)
    assert ("request-finished", 201) in hooks.events
    assert group.active == 0
    assert lifecycle.metrics.get("upstream_active") == 0
    assert 70 in transport.closed
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_streams_request_body_before_request_end() -> None:
    transport = ProxyFakeTransport()
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"POST /api/upload HTTP/1.1\r\nHost: front\r\n"
        b"Content-Length: 13\r\nConnection: close\r\n\r\nupload-",
    )]
    transport.upstream_reads = [(
        PCC_SOCKET_PROGRESS,
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
        b"Connection: close\r\n\r\nok",
    )]
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend", method="POST"),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(app, transport)
    tail_released = [False]

    def release_tail_after_origin_progress(fd: int, events: int) -> None:
        if fd != connection.fd or tail_released[0]:
            return
        upstream = bytes(transport.upstream_written)
        assert b"content-length: 13\r\n" in upstream
        assert upstream.endswith(b"\r\nupload-")
        tail_released[0] = True
        transport.reads.append((PCC_SOCKET_PROGRESS, b"stream"))

    transport.on_wait = release_tail_after_origin_progress
    connection.run()

    assert tail_released == [True]
    assert bytes(transport.upstream_written).endswith(b"\r\nupload-stream")
    assert bytes(transport.written).endswith(b"ok")
    assert group.active == 0
    assert lifecycle.metrics.get("upstream_active") == 0
    generation.release()
    lifecycle.release_connection()


def test_early_proxy_transfers_request_admission_before_close() -> None:
    transport = ProxyFakeTransport()
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend", method="POST"),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(app, transport)

    connection.feed_data(
        b"POST /api/upload HTTP/1.1\r\nHost: front\r\n"
        b"Content-Length: 13\r\n\r\nupload-"
    )

    assert connection.pending_proxy_request is connection.current_request
    assert connection.current_admitted is False
    assert lifecycle.metrics.get("requests_active") == 1
    connection.close("client-closed-before-request-end")
    assert lifecycle.metrics.get("requests_active") == 0
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_observes_downstream_eof_during_upstream_header_wait() -> None:
    transport = ProxyFakeTransport()
    transport.reads = [
        (
            PCC_SOCKET_PROGRESS,
            b"GET /api/stall HTTP/1.1\r\nHost: front\r\n"
            b"Connection: close\r\n\r\n",
        ),
        (PCC_SOCKET_EOF, b""),
    ]
    transport.upstream_reads = [(PCC_SOCKET_WOULD_BLOCK, b"")]
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(app, transport)

    connection.run()

    upstream_waits = [
        timeout_ms
        for fd, events, timeout_ms in transport.waits
        if fd == 70 and events == PCC_IO_READ
    ]
    assert upstream_waits and max(upstream_waits) <= 25
    assert lifecycle.metrics.get("upstream_cancelled") == 1
    assert lifecycle.metrics.get("upstream_active") == 0
    assert group.active == 0
    assert 70 in transport.closed
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_resolves_hostname_before_open_and_preserves_authority() -> None:
    hooks = RecordingHooks()
    transport = ProxyDnsTransport()
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/value HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    transport.upstream_reads = [(
        PCC_SOCKET_PROGRESS,
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
        b"Connection: close\r\n\r\nok",
    )]
    resolver = Resolver(
        config=DnsResolverConfig((DnsServer("192.0.2.53"),), 1, 500)
    )
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("api.internal", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),), upstreams=(group,)
    )
    connection, lifecycle, generation = _proxy_connection(
        app,
        transport,
        hooks=hooks,
        resolver=resolver,
        dns_transport=transport,
    )
    transport.dns_receive_results = [
        DnsIoResult(DNS_IO_WOULD_BLOCK),
        DnsIoResult(
            DNS_IO_OK,
            data=_dns_a_response(1, "api.internal"),
            peer=DnsServer("192.0.2.53"),
        ),
    ]

    connection.run()

    assert transport.opened_endpoints == [("192.0.2.80", 9000)]
    assert b"host: api.internal:9000\r\n" in bytes(
        transport.upstream_written
    )
    assert any(fd == 50 and events == PCC_IO_READ for fd, events, _ in transport.waits)
    assert transport.dns_closed == [50]
    assert ("dns-started", "api.internal") in hooks.events
    assert ("dns-finished", "api.internal", 1, "") in hooks.events
    assert lifecycle.metrics.get("dns_queries") == 1
    assert lifecycle.metrics.get("dns_failures") == 0
    assert bytes(transport.written).endswith(b"ok")
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_dns_timeout_returns_504_and_releases_reservation() -> None:
    transport = ProxyDnsTransport()
    transport.clock_ms = 100
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/slow HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    resolver = Resolver(
        config=DnsResolverConfig((DnsServer("192.0.2.53"),), 1, 5)
    )
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("slow.internal", 9000),)
    )
    app = App(
        routes=(
            proxy(
                "/api/{path*}",
                "backend",
                timeouts=ProxyTimeouts(connect_ms=5),
            ),
        ),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(
        app, transport, resolver=resolver, dns_transport=transport
    )

    connection.run()

    assert b"HTTP/1.1 504 Gateway Timeout" in bytes(transport.written)
    assert b"upstream dns-timeout" in bytes(transport.written)
    assert transport.opened_endpoints == []
    assert group.active == 0
    assert transport.dns_closed == [50]
    assert lifecycle.metrics.get("dns_queries") == 1
    assert lifecycle.metrics.get("dns_failures") == 1
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_dns_adapter_exception_closes_driver_handle() -> None:
    class RaisingDnsTransport(ProxyDnsTransport):
        def receive(self, handle, max_bytes):
            raise RuntimeError("dns receive exploded")

    transport = RaisingDnsTransport()
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/value HTTP/1.1\r\nHost: front\r\nConnection: close\r\n\r\n",
    )]
    resolver = Resolver(
        config=DnsResolverConfig((DnsServer("192.0.2.53"),), 1, 500)
    )
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("api.internal", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),), upstreams=(group,)
    )
    connection, lifecycle, generation = _proxy_connection(
        app, transport, resolver=resolver, dns_transport=transport
    )

    connection.run()

    assert transport.dns_closed == [50]
    assert group.active == 0
    assert lifecycle.metrics.get("dns_failures") == 1
    assert b"HTTP/1.1 502 Bad Gateway" in bytes(transport.written)
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_tries_each_resolved_address_within_one_connect_budget() -> None:
    transport = ProxyDnsTransport()
    transport.open_results = [-1, 72]
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/value HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    transport.upstream_reads = [(
        PCC_SOCKET_PROGRESS,
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
        b"Connection: close\r\n\r\nok",
    )]
    resolver = Resolver(
        config=DnsResolverConfig((DnsServer("192.0.2.53"),), 1, 500)
    )
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("api.internal", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),), upstreams=(group,)
    )
    connection, lifecycle, generation = _proxy_connection(
        app, transport, resolver=resolver, dns_transport=transport
    )
    # Add a second same-class address to the accepted cache before the proxy
    # consumes it; this isolates address failover from DNS wire parsing.
    resolver.cache.put(
        "api.internal",
        DNS_A,
        ["192.0.2.80", "192.0.2.81"],
        1000,
        99,
    )

    connection.run()

    assert transport.opened_endpoints == [
        ("192.0.2.80", 9000),
        ("192.0.2.81", 9000),
    ]
    assert bytes(transport.written).endswith(b"ok")
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_tries_next_resolved_address_after_connect_failure() -> None:
    transport = ProxyDnsTransport()
    transport.open_results = [70, 71]
    transport.connect_results = [-61, PCC_SOCKET_CONNECTED]
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/value HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    transport.upstream_reads = [(
        PCC_SOCKET_PROGRESS,
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
        b"Connection: close\r\n\r\nok",
    )]
    resolver = Resolver(
        config=DnsResolverConfig((DnsServer("192.0.2.53"),), 1, 500)
    )
    resolver.cache.put(
        "api.internal",
        DNS_A,
        ["192.0.2.80", "192.0.2.81"],
        1000,
        99,
    )
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("api.internal", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),), upstreams=(group,)
    )
    connection, lifecycle, generation = _proxy_connection(
        app, transport, resolver=resolver, dns_transport=transport
    )

    connection.run()

    assert transport.opened_endpoints == [
        ("192.0.2.80", 9000),
        ("192.0.2.81", 9000),
    ]
    assert 70 in transport.closed
    assert bytes(transport.written).endswith(b"ok")
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_falls_through_a_nodata_to_aaaa_hosts_address() -> None:
    transport = ProxyDnsTransport()
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/v6 HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    transport.upstream_reads = [(
        PCC_SOCKET_PROGRESS,
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
        b"Connection: close\r\n\r\nok",
    )]
    resolver = Resolver(
        config=DnsResolverConfig((DnsServer("192.0.2.53"),), 1, 500),
        hosts=HostsTable("2001:db8::80 api-v6.internal\n"),
    )
    resolver.cache.put("api-v6.internal", DNS_A, [], 0, 99, negative=True)
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("api-v6.internal", 9000),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),), upstreams=(group,)
    )
    connection, lifecycle, generation = _proxy_connection(
        app, transport, resolver=resolver, dns_transport=transport
    )

    connection.run()

    assert transport.opened_endpoints == [
        ("2001:db8:0:0:0:0:0:80", 9000)
    ]
    assert b"host: api-v6.internal:9000\r\n" in bytes(
        transport.upstream_written
    )
    assert bytes(transport.written).endswith(b"ok")
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_retries_connect_before_response_commit() -> None:
    transport = ProxyFakeTransport()
    transport.open_results = [-1, 71]
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/retry HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    transport.upstream_reads = [(
        PCC_SOCKET_PROGRESS,
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
        b"Connection: close\r\n\r\nok",
    )]
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("localhost", 9001),)
    )
    app = App(
        routes=(proxy(
            "/api/{path*}",
            "backend",
            retry=RetryPolicy(attempts=2),
        ),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(app, transport)

    connection.run()

    assert transport.opened_endpoints == [
        ("localhost", 9001),
        ("localhost", 9001),
    ]
    assert lifecycle.metrics.get("upstream_retries") == 1
    assert bytes(transport.written).endswith(b"ok")
    assert group.active == 0
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_stage_timeout_returns_504_and_releases_lease() -> None:
    transport = ProxyFakeTransport()
    transport.connect_results = [
        PCC_SOCKET_WOULD_BLOCK,
        PCC_SOCKET_WOULD_BLOCK,
    ]
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/slow HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9002),)
    )
    app = App(
        routes=(proxy(
            "/api/{path*}",
            "backend",
            timeouts=ProxyTimeouts(connect_ms=5),
        ),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(app, transport)

    connection.run()

    assert b"HTTP/1.1 504 Gateway Timeout" in bytes(transport.written)
    assert b"upstream connect-timeout" in bytes(transport.written)
    assert group.active == 0
    assert lifecycle.metrics.get("upstream_active") == 0
    assert 70 in transport.closed
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_cancellation_returns_502_and_counts_cancellation() -> None:
    transport = ProxyFakeTransport()
    transport.connect_results = [
        PCC_SOCKET_WOULD_BLOCK,
        PCC_SOCKET_WOULD_BLOCK,
    ]
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/cancel HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9003),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(app, transport)

    def cancel_on_park(fd: int, events: int) -> None:
        connection.pending_proxy_request.cancellation.cancel(
            "downstream cancelled"
        )

    transport.on_wait = cancel_on_park
    connection.run()

    assert b"HTTP/1.1 502 Bad Gateway" in bytes(transport.written)
    assert b"upstream cancelled" in bytes(transport.written)
    assert lifecycle.metrics.get("upstream_cancelled") == 1
    assert group.active == 0
    generation.release()
    lifecycle.release_connection()


def test_live_proxy_group_saturation_returns_503_without_opening_socket() -> None:
    transport = ProxyFakeTransport()
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"GET /api/busy HTTP/1.1\r\nHost: front\r\n"
        b"Connection: close\r\n\r\n",
    )]
    group = UpstreamGroup(
        "backend",
        (UpstreamEndpoint("127.0.0.1", 9004),),
        max_active=1,
    )
    held = group.acquire()
    app = App(
        routes=(proxy("/api/{path*}", "backend"),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(app, transport)

    connection.run()

    assert b"HTTP/1.1 503 Service Unavailable" in bytes(transport.written)
    assert transport.opened_endpoints == []
    held.release()
    assert group.active == 0
    generation.release()
    lifecycle.release_connection()


def test_proxy_attempt_cleanup_releases_every_owner_after_callback_failures(
    monkeypatch,
) -> None:
    transport = ProxyFakeTransport()
    transport.upstream_reads = [(
        PCC_SOCKET_PROGRESS,
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
        b"Connection: close\r\n\r\nok",
    )]
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9005),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(app, transport)
    request = Request(
        "GET",
        "/api/value",
        headers=(("host", "front.example"),),
        client_ip="192.0.2.10",
    )
    plan = app.dispatch_proxy_head(request)
    assert plan is not None

    closed = []

    def failing_close(handle: int) -> None:
        closed.append(handle)
        raise OSError("upstream close failed")

    pool = UpstreamConnectionPool(group, close_connection=failing_close)
    cancel_reasons = []
    original_cancel = gateway_server_module.ProxyExchange.cancel

    def failing_cancel(exchange, reason: str) -> None:
        cancel_reasons.append(reason)
        original_cancel(exchange, reason)
        raise RuntimeError("exchange cancel failed")

    monkeypatch.setattr(
        gateway_server_module.ProxyExchange,
        "cancel",
        failing_cancel,
    )

    with pytest.raises(RuntimeError, match="exchange cancel failed"):
        gateway_server_module._proxy_exchange_attempt(
            connection, plan, pool, 1
        )

    assert cancel_reasons == ["attempt complete"]
    assert closed == [70]
    assert group.active == 0
    assert lifecycle.metrics.get("upstream_active") == 0
    _release_connection(connection, lifecycle, generation)


def test_proxy_completion_atomically_releases_early_request_on_primary_error(
    monkeypatch,
) -> None:
    class CleanupFailingBody(BodyStream):
        def __init__(self) -> None:
            super().__init__()
            self.cancel_calls = 0
            self.close_calls = 0

        def cancel(self) -> None:
            self.cancel_calls += 1
            super().cancel()
            raise RuntimeError("body cancel failed")

        def close(self) -> None:
            self.close_calls += 1
            super().close()
            raise RuntimeError("body close failed")

    class FailingFinishedHooks(RecordingHooks):
        def request_finished(self, connection, request, response) -> None:
            super().request_finished(connection, request, response)
            raise RuntimeError("request-finished hook failed")

    transport = ProxyFakeTransport()
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9006),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend"),),
        upstreams=(group,),
    )
    hooks = FailingFinishedHooks()
    connection, lifecycle, generation = _proxy_connection(
        app, transport, hooks=hooks
    )
    body = CleanupFailingBody()
    request = Request(
        "POST",
        "/api/value",
        headers=(("host", "front.example"),),
        body=body,
        content_length=4,
    )
    plan = app.dispatch_proxy_head(request)
    assert plan is not None
    assert lifecycle.admit_request()
    connection.current_request = request
    connection.current_admitted = True
    connection.pending_proxy = plan
    connection.pending_proxy_request = request

    def completed_attempt(connection, plan, pool, attempt: int):
        return 200, "", True

    monkeypatch.setattr(
        gateway_server_module,
        "_proxy_exchange_attempt",
        completed_attempt,
    )

    with pytest.raises(RuntimeError, match="request-finished hook failed"):
        gateway_server_module._run_gateway_proxy(connection)

    assert connection.pending_proxy is None
    assert connection.pending_proxy_request is None
    assert connection.current_request is None
    assert not connection.current_admitted
    assert connection.close_after_flush
    assert request.cancellation.cancelled
    assert body.cancelled
    assert body.cancel_calls == 1
    assert body.close_calls == 1
    assert lifecycle.metrics.get("requests_active") == 0
    assert connection.requests_completed == 1

    # A re-entrant/stale continuation observes that ownership was already
    # taken and cannot release admission or body state a second time.
    gateway_server_module._run_gateway_proxy(connection)
    assert body.cancel_calls == 1
    assert body.close_calls == 1
    assert lifecycle.metrics.get("requests_active") == 0
    assert connection.requests_completed == 1
    _release_connection(connection, lifecycle, generation)


def test_proxy_parser_4xx_takes_pending_owner_without_appending_502(
    monkeypatch,
) -> None:
    bodies = []

    class TrackingBody(BodyStream):
        def __init__(self, max_bytes: int = 16777216) -> None:
            super().__init__(max_bytes)
            self.cancel_calls = 0
            self.close_calls = 0
            bodies.append(self)

        def cancel(self) -> None:
            self.cancel_calls += 1
            super().cancel()

        def close(self) -> None:
            self.close_calls += 1
            super().close()

    monkeypatch.setattr(web_models, "BodyStream", TrackingBody)
    hooks = RecordingHooks()
    transport = ProxyFakeTransport()
    # This is observed while the early proxy waits for another chunk.  The
    # ordinary HTTP parser commits the exact 400 response.
    transport.reads = [(PCC_SOCKET_PROGRESS, b"not-hex\r\n")]
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9007),)
    )
    app = App(
        routes=(proxy("/api/{path*}", "backend", method="POST"),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _proxy_connection(
        app, transport, hooks=hooks
    )

    assert connection.feed_data(
        b"POST /api/value HTTP/1.1\r\nHost: front.example\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n"
    ) == 1
    assert connection.pending_proxy_request is connection.current_request
    assert not connection.current_admitted
    assert lifecycle.metrics.get("requests_active") == 1
    assert len(bodies) == 1

    gateway_server_module._run_gateway_proxy(connection)

    wire = connection.take_output()
    assert wire.count(b"HTTP/1.1 ") == 1
    assert b"HTTP/1.1 400 Bad Request" in wire
    assert b"502 Bad Gateway" not in wire
    assert not any(event[0] == "request-finished" for event in hooks.events)
    assert connection.pending_proxy is None
    assert connection.pending_proxy_request is None
    assert connection.current_request is None
    assert lifecycle.metrics.get("requests_active") == 0
    assert lifecycle.metrics.get("parser_errors") == 1
    assert bodies[0].cancel_calls == 1
    assert bodies[0].close_calls == 1
    assert group.active == 0
    _release_connection(connection, lifecycle, generation)


def test_listener_accept_spawns_one_virtual_thread_per_connection() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    transport.accepts = [(PCC_SOCKET_PROGRESS, 52, "10.0.0.8")]
    app = App(routes=(get("/", lambda request: "ok"),))
    server = GatewayServer(
        app,
        "127.0.0.1",
        8081,
        3,
        GatewayConfig(
            listeners=(ListenerConfig("127.0.0.1", 8081, backlog=321),),
        ),
        transport,
        scheduler,
    )

    server.start()
    assert transport.listen_args == ("127.0.0.1", 8081, False, 321)
    assert scheduler.started == 3
    assert scheduler.spawned[0] == ("accept", server)
    assert server.accept_once() == 1
    kind, owner, connection = scheduler.spawned[1]
    assert kind == "connection"
    assert owner is server
    assert connection.fd == 52
    assert connection.client_ip == "10.0.0.8"
    assert server.lifecycle.metrics.get("connections_active") == 1

    connection.close("test")
    server._connection_finished(connection)
    server.request_stop()
    assert server._drive_drain()
    assert server.lifecycle.state == STATE_STOPPED
    app.shutdown()


def test_listener_admission_rejects_before_spawn() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    transport.accepts = [(PCC_SOCKET_PROGRESS, 53, "10.0.0.9")]
    limits = AdmissionLimits(max_connections=0)
    server = GatewayServer(
        App(routes=(get("/", lambda request: "ok"),)),
        config=GatewayConfig(admission=limits),
        transport=transport,
        scheduler=scheduler,
    )
    server.start()

    assert server.accept_once() == -1
    assert transport.closed == [53]
    assert len(scheduler.spawned) == 1
    assert server.lifecycle.metrics.get("connections_rejected") == 1


def test_accept_construction_failure_releases_all_partial_owners(
    monkeypatch,
) -> None:
    class TrackedGenerationResource:
        def __init__(self) -> None:
            self.references = 1
            self.release_calls = 0

        def retain(self):
            self.references += 1
            return self

        def release(self) -> None:
            self.release_calls += 1
            self.references -= 1

    class FailingTlsChannel:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            raise RuntimeError("TLS channel close failed")

    class InjectedTlsManager:
        def __init__(self, channel) -> None:
            self.channel = channel
            self.new_channel_calls = 0

        def new_channel(self, fd: int, generation):
            self.new_channel_calls += 1
            return self.channel

    class FailingAcceptedCloseTransport(FakeTransport):
        def close(self, fd: int) -> int:
            self.closed.append(fd)
            if fd == 61:
                raise OSError("accepted fd close failed")
            return 0

    transport = FailingAcceptedCloseTransport()
    transport.accepts = [(PCC_SOCKET_PROGRESS, 61, "10.0.0.11")]
    app = App(routes=(get("/", lambda request: "ok"),))
    server = GatewayServer(
        app,
        config=GatewayConfig(install_signal_handlers=False),
        transport=transport,
        scheduler=FakeScheduler(),
    )
    resource = TrackedGenerationResource()
    generation = server.lifecycle.current
    generation.attach_resource(resource)
    channel = FailingTlsChannel()
    tls_manager = InjectedTlsManager(channel)
    server.tls_manager = tls_manager
    server.start()

    release_generation = generation.release

    def failing_generation_release() -> None:
        release_generation()
        raise RuntimeError("generation release failed")

    generation.release = failing_generation_release
    release_connection = server.lifecycle.release_connection

    def failing_admission_release() -> None:
        release_connection()
        raise RuntimeError("connection admission release failed")

    monkeypatch.setattr(
        server.lifecycle,
        "release_connection",
        failing_admission_release,
    )

    def failing_connection_constructor(*args, **kwargs):
        raise RuntimeError("connection construction failed")

    monkeypatch.setattr(
        gateway_server_module,
        "GatewayConnection",
        failing_connection_constructor,
    )

    with pytest.raises(RuntimeError, match="connection construction failed"):
        server.accept_once()

    assert tls_manager.new_channel_calls == 1
    assert channel.close_calls == 1
    assert generation.references == 1
    assert server.lifecycle.metrics.get("connections_active") == 0
    assert transport.closed == [61]

    server.request_stop()
    assert server._drive_drain()
    assert resource.release_calls == 1
    app.shutdown()


def test_tls_listener_accepts_handshakes_drives_http_and_releases_generation() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    provider = ListenerWiringTlsProvider()
    provider.reads = [
        b"GET / HTTP/1.1\r\nHost: tls.example\r\nConnection: close\r\n\r\n"
    ]
    registry = TlsProviderRegistry()
    registry.register(provider)
    listener = ListenerConfig(
        "127.0.0.1",
        8443,
        tls_provider=provider.name,
        tls_config=TlsConfig(
            default_certificate=TlsCertificate(
                "default", "cert-path", "key-path"
            ),
            alpn=("http/1.1",),
        ),
    )
    transport.accepts = [(PCC_SOCKET_PROGRESS, 54, "10.0.0.10")]
    hooks = RecordingHooks()
    app = App(routes=(get("/", lambda request: request.scheme),))
    server = GatewayServer(
        app,
        config=GatewayConfig(listeners=(listener,)),
        transport=transport,
        scheduler=scheduler,
        hooks=hooks,
        tls_registry=registry,
    )
    # No artifact was declared on this explicitly injected registry.  It is a
    # caller-owned trust boundary and must not look digest-authenticated in the
    # provider snapshot used by result claims.
    assert server.tls_manager.provider_info.library_path == ""
    assert server.tls_manager.provider_info.verified_library_sha256 == ""

    server.start()
    assert server.accept_once() == 1
    connection = scheduler.spawned[1][2]
    assert connection.tls_channel is not None
    connection.run()
    server._connection_finished(connection)

    assert bytes(provider.written).endswith(b"https")
    assert server.lifecycle.metrics.get("tls_handshakes_started") == 1
    assert server.lifecycle.metrics.get("tls_handshakes_completed") == 1
    assert server.lifecycle.metrics.get("tls_close_notify_completed") == 1
    assert provider.connections_freed == 1
    assert transport.written == b""
    assert ("tls-handshake-started", provider.name) in hooks.events
    assert ("tls-handshake-completed", "http/1.1") in hooks.events
    assert ("tls-closed", True) in hooks.events

    server.request_stop()
    assert server._drive_drain()
    server.tls_manager.close()
    assert provider.contexts_freed == 1
    app.shutdown()


def test_builtin_tls_registry_receives_declared_artifact_identity(
    monkeypatch,
) -> None:
    path = "/opt/pcc/lib/libpcc-tls-provider.so"
    digest = "a" * 64
    max_bytes = 1048576
    provider = ListenerWiringTlsProvider()
    provider.name = PCC_NATIVE_TLS_PROVIDER_NAME
    _declared_tls_provider_artifact(provider, path, digest, max_bytes)
    registry = TlsProviderRegistry()
    registry.register(provider)
    factory_calls = []

    def production_registry(library_path, expected_sha256, library_max_bytes):
        factory_calls.append((library_path, expected_sha256, library_max_bytes))
        return registry

    monkeypatch.setattr(
        gateway_server_module,
        "production_tls_registry",
        production_registry,
    )
    listener = ListenerConfig(
        "127.0.0.1",
        8443,
        tls_provider=PCC_NATIVE_TLS_PROVIDER_NAME,
        tls_config=TlsConfig("cert.pem", "key.pem"),
        tls_provider_library=path,
        tls_provider_library_sha256=digest,
        tls_provider_max_bytes=max_bytes,
    )
    server = GatewayServer(
        App(routes=(get("/", lambda request: "ok"),)),
        config=GatewayConfig(listeners=(listener,)),
        transport=FakeTransport(),
        scheduler=FakeScheduler(),
    )

    assert factory_calls == [(path, digest, max_bytes)]
    info = server.tls_manager.provider_info
    assert info.library_path == path
    assert info.expected_library_sha256 == digest
    assert info.verified_library_sha256 == digest
    assert info.library_max_bytes == max_bytes
    server.tls_manager.close()


@pytest.mark.parametrize(
    ("provider_field", "provider_value"),
    (
        ("library_path", "/opt/pcc/lib/replaced-provider.so"),
        ("expected_library_sha256", "b" * 64),
        ("verified_library_sha256", ""),
        ("library_max_bytes", 1048575),
    ),
)
def test_declared_tls_artifact_must_match_injected_provider_snapshot(
    provider_field,
    provider_value,
) -> None:
    path = "/opt/pcc/lib/libpcc-tls-provider.so"
    digest = "a" * 64
    max_bytes = 1048576
    provider = ListenerWiringTlsProvider()
    _declared_tls_provider_artifact(provider, path, digest, max_bytes)
    setattr(provider, provider_field, provider_value)
    registry = TlsProviderRegistry()
    registry.register(provider)
    listener = ListenerConfig(
        "127.0.0.1",
        8443,
        tls_provider=provider.name,
        tls_config=TlsConfig("cert.pem", "key.pem"),
        tls_provider_library=path,
        tls_provider_library_sha256=digest,
        tls_provider_max_bytes=max_bytes,
    )

    with pytest.raises(TlsProviderError, match="provenance"):
        GatewayServer(
            App(routes=(get("/", lambda request: "ok"),)),
            config=GatewayConfig(listeners=(listener,)),
            transport=FakeTransport(),
            scheduler=FakeScheduler(),
            tls_registry=registry,
        )
    assert provider.contexts_freed == 1


def test_tls_reload_freezes_declared_provider_digest_and_byte_limit() -> None:
    path = "/opt/pcc/lib/libpcc-tls-provider.so"
    digest = "a" * 64
    max_bytes = 1048576
    provider = ListenerWiringTlsProvider()
    _declared_tls_provider_artifact(provider, path, digest, max_bytes)
    registry = TlsProviderRegistry()
    registry.register(provider)

    def listener(library_path=path, sha256=digest, byte_limit=max_bytes):
        return ListenerConfig(
            "127.0.0.1",
            8443,
            tls_provider=provider.name,
            tls_config=TlsConfig("cert.pem", "key.pem"),
            tls_provider_library=library_path,
            tls_provider_library_sha256=sha256,
            tls_provider_max_bytes=byte_limit,
        )

    server = GatewayServer(
        App(routes=(get("/", lambda request: "ok"),)),
        config=GatewayConfig(listeners=(listener(),)),
        transport=FakeTransport(),
        scheduler=FakeScheduler(),
        tls_registry=registry,
    )
    server.lifecycle.start()
    server.lifecycle.started()
    replacements = (
        listener(library_path="/opt/pcc/lib/replaced-provider.so"),
        listener(sha256="b" * 64),
        listener(byte_limit=max_bytes - 1),
    )
    for replacement in replacements:
        with pytest.raises(UnsupportedGatewayFeature, match="TLS provider"):
            server.reload(GatewayConfig(listeners=(replacement,)))
    server.tls_manager.close()


def test_connection_open_hook_failure_still_closes_every_connection_owner() -> None:
    transport = FakeTransport()
    failure = RuntimeError("connection-open hook failed")

    class FailingOpenHooks(RecordingHooks):
        def connection_opened(self, connection) -> None:
            raise failure

        def connection_closed(self, connection, reason: str) -> None:
            super().connection_closed(connection, reason)
            raise RuntimeError("connection-close hook failed")

    hooks = FailingOpenHooks()
    connection, lifecycle, generation = _connection(
        App(),
        transport=transport,
        hooks=hooks,
    )

    with pytest.raises(RuntimeError, match="connection-open hook failed") as caught:
        connection.run()

    assert caught.value is failure
    assert connection.closed
    assert connection.close_reason == "transport-error"
    assert connection.fd == -1
    assert transport.shutdowns == [41]
    assert transport.closed == [41]
    assert hooks.events == [("connection-closed", "transport-error")]
    _release_connection(connection, lifecycle, generation)


def test_tls_close_notify_hook_failure_still_closes_session_and_socket() -> None:
    transport = FakeTransport()

    class FailingTlsClosedHooks(RecordingHooks):
        def tls_closed(self, connection, graceful: bool) -> None:
            super().tls_closed(connection, graceful)
            raise RuntimeError("TLS closed hook failed")

    hooks = FailingTlsClosedHooks()
    connection, lifecycle, generation = _connection(
        App(),
        transport=transport,
        hooks=hooks,
    )
    channel = ClosePathTlsChannel((TlsResult(TLS_CLOSED),))
    connection.tls_channel = channel
    connection.close_after_flush = True

    with pytest.raises(RuntimeError, match="TLS closed hook failed"):
        connection.run()

    assert connection.closed
    assert channel.released
    assert channel.close_calls == 1
    assert connection.tls_channel is None
    assert transport.shutdowns == [41]
    assert transport.closed == [41]
    assert ("tls-closed", True) in hooks.events
    assert ("connection-closed", "response-complete") in hooks.events
    _release_connection(connection, lifecycle, generation)


def test_tls_close_notify_wait_failure_still_closes_session_and_socket() -> None:
    transport = FailingWaitTransport()
    connection, lifecycle, generation = _connection(
        App(),
        transport=transport,
        hooks=RecordingHooks(),
    )
    channel = ClosePathTlsChannel((TlsResult(TLS_WANT_READ),))
    connection.tls_channel = channel
    connection.close_after_flush = True

    with pytest.raises(RuntimeError, match="TLS close wait failed"):
        connection.run()

    assert connection.closed
    assert channel.close_calls == 1
    assert connection.tls_channel is None
    assert transport.waits and transport.waits[0][1] == PCC_IO_READ
    assert transport.shutdowns == [41]
    assert transport.closed == [41]
    _release_connection(connection, lifecycle, generation)


def test_tls_close_notify_provider_failure_still_closes_all_owners() -> None:
    transport = FakeTransport()
    connection, lifecycle, generation = _connection(
        App(),
        transport=transport,
        hooks=RecordingHooks(),
    )
    channel = ClosePathTlsChannel()
    channel.raise_close_notify = True
    connection.tls_channel = channel
    connection.close_after_flush = True

    with pytest.raises(RuntimeError, match="TLS close-notify provider failed"):
        connection.run()

    assert connection.closed
    assert channel.close_calls == 1
    assert connection.tls_channel is None
    assert transport.shutdowns == [41]
    assert transport.closed == [41]
    _release_connection(connection, lifecycle, generation)


def test_connection_close_takes_owners_before_best_effort_callbacks() -> None:
    transport = FailingOwnerTransport()

    class ReentrantFailingHooks(RecordingHooks):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        def connection_closed(self, connection, reason: str) -> None:
            self.close_calls += 1
            connection.close("reentrant")
            raise RuntimeError("connection-close hook failed")

    class FailingOutputOwner:
        def __init__(self, owner) -> None:
            self.owner = owner
            self.close_calls = 0

        def __len__(self) -> int:
            return len(self.owner)

        def close(self) -> None:
            self.close_calls += 1
            self.owner.close()
            raise RuntimeError("output close failed")

    hooks = ReentrantFailingHooks()
    connection, lifecycle, generation = _connection(
        App(),
        transport=transport,
        hooks=hooks,
    )
    connection._queue_bytes(b"owned")
    output = FailingOutputOwner(connection.output)
    connection.output = output
    channel = ClosePathTlsChannel()
    channel.raise_close = True
    connection.tls_channel = channel

    with pytest.raises(RuntimeError, match="output close failed"):
        connection.close("failure-injection")

    assert connection.closed
    assert connection.close_reason == "failure-injection"
    assert connection.fd == -1
    assert connection.tls_channel is None
    assert connection.deferred_events == []
    assert output.close_calls == 1
    assert output.owner.closed
    assert channel.close_calls == 1
    assert transport.shutdowns == [41]
    assert transport.closed == [41]
    assert hooks.close_calls == 1
    assert lifecycle.metrics.get("buffered_bytes") == 0

    connection.close("second-close")
    assert output.close_calls == 1
    assert channel.close_calls == 1
    assert transport.shutdowns == [41]
    assert transport.closed == [41]
    assert hooks.close_calls == 1
    generation.release()
    lifecycle.release_connection()


def test_tls_handshake_parks_on_provider_read_and_write_interests() -> None:
    transport = ReadyFakeTransport()
    scheduler = FakeScheduler()
    provider = ListenerWiringTlsProvider()
    provider.handshake_results = [
        TlsResult(TLS_WANT_READ),
        TlsResult(TLS_WANT_WRITE),
        TlsResult(TLS_OK),
    ]
    provider.reads = [
        b"GET / HTTP/1.1\r\nHost: tls.example\r\nConnection: close\r\n\r\n"
    ]
    registry = TlsProviderRegistry()
    registry.register(provider)
    listener = ListenerConfig(
        "127.0.0.1",
        8443,
        tls_provider=provider.name,
        tls_config=TlsConfig("cert.pem", "key.pem"),
    )
    server = GatewayServer(
        App(routes=(get("/", lambda request: "ok"),)),
        config=GatewayConfig(
            listeners=(listener,), tls_handshake_timeout_ms=50
        ),
        transport=transport,
        scheduler=scheduler,
        tls_registry=registry,
    )
    transport.accepts = [(PCC_SOCKET_PROGRESS, 57, "10.0.0.13")]
    server.start()
    assert server.accept_once() == 1
    connection = scheduler.spawned[1][2]
    connection.run()
    server._connection_finished(connection)

    assert [entry[1] for entry in transport.waits[:2]] == [
        PCC_IO_READ,
        PCC_IO_WRITE,
    ]
    assert server.lifecycle.metrics.get("tls_handshakes_completed") == 1
    server.request_stop()
    assert server._drive_drain()
    server.tls_manager.close()


def test_tls_reload_publishes_new_cert_generation_and_pins_old_connection() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    provider = ListenerWiringTlsProvider()
    registry = TlsProviderRegistry()
    registry.register(provider)

    def tls_listener(certificate_path: str):
        return ListenerConfig(
            "127.0.0.1",
            8443,
            tls_provider=provider.name,
            tls_config=TlsConfig(certificate_path, certificate_path + ".key"),
        )

    server = GatewayServer(
        App(routes=(get("/", lambda request: "ok"),)),
        config=GatewayConfig(listeners=(tls_listener("old.pem"),)),
        transport=transport,
        scheduler=scheduler,
        tls_registry=registry,
    )
    transport.accepts = [
        (PCC_SOCKET_PROGRESS, 55, "10.0.0.11"),
        (PCC_SOCKET_PROGRESS, 56, "10.0.0.12"),
    ]
    server.start()
    assert server.accept_once() == 1
    old_connection = scheduler.spawned[1][2]
    old_tls_generation = old_connection.tls_channel.generation

    replacement = GatewayConfig(listeners=(tls_listener("new.pem"),))
    gateway_generation = server.reload(replacement)
    assert gateway_generation is server.lifecycle.current
    assert old_tls_generation.retired
    assert not old_tls_generation.destroyed
    assert server.lifecycle.metrics.get("tls_generation_reloads") == 1

    assert server.accept_once() == 1
    new_connection = scheduler.spawned[2][2]
    assert new_connection.tls_channel.generation is server.tls_manager.active
    assert new_connection.tls_channel.generation is not old_tls_generation

    old_connection.close("old-generation-drained")
    server._connection_finished(old_connection)
    assert old_tls_generation.destroyed
    new_connection.close("new-generation-drained")
    server._connection_finished(new_connection)
    server.request_stop()
    assert server._drive_drain()
    server.tls_manager.close()


def test_live_waitset_selection_is_deferred_to_native_runtime_start() -> None:
    for backend in ("auto", "poll", "kqueue", "epoll"):
        server = GatewayServer(
            App(),
            config=GatewayConfig(waitset_backend=backend),
            transport=FakeTransport(),
            scheduler=FakeScheduler(),
        )
        assert server.config.waitset_backend == backend


def test_disabled_process_control_never_installs_polls_or_uninstalls() -> None:
    transport = FakeTransport()
    server = GatewayServer(
        App(),
        config=GatewayConfig(
            install_signal_handlers=False,
            drain_timeout_ms=1,
        ),
        transport=transport,
        scheduler=FakeScheduler(),
        process_control=ForbiddenProcessControl(),
    )

    server.start()
    server.request_stop()
    assert server._drive_drain()
    assert server.process_control_installed is False


@pytest.mark.parametrize(
    ("outcome", "reason"),
    (
        (
            virtual_thread.OUTCOME_RETURNED,
            "gateway accept owner returned while server is running",
        ),
        (
            virtual_thread.OUTCOME_CANCELLED,
            "gateway accept owner was cancelled",
        ),
    ),
)
def test_run_fails_closed_when_accept_owner_stops_while_running(
    outcome: int,
    reason: str,
) -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    scheduler.outcome_value = outcome
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )

    with pytest.raises(GatewayError, match="^" + reason + "$"):
        server.run()

    assert server.lifecycle.state == STATE_FAILED
    assert server.lifecycle.failed_reason == reason
    assert transport.closed == [transport.listener_fd]
    assert scheduler.stopped == 1
    assert server.pool_started is False
    assert server.app_started is False


def test_run_preserves_accept_owner_exception_and_tears_down() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    failure = RuntimeError("accept loop exploded")
    scheduler.outcome_value = virtual_thread.OUTCOME_RAISED
    scheduler.exception_value = failure
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )

    with pytest.raises(RuntimeError, match="^accept loop exploded$") as caught:
        server.run()

    assert caught.value is failure
    assert server.lifecycle.state == STATE_FAILED
    assert server.lifecycle.failed_reason == (
        "gateway accept owner raised: accept loop exploded"
    )
    assert transport.closed == [transport.listener_fd]
    assert scheduler.stopped == 1
    assert server.app_started is False


def test_accept_owner_normal_return_is_allowed_after_drain_begins() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    scheduler.outcome_value = virtual_thread.OUTCOME_RETURNED
    process_control = StopOnceProcessControl()
    server = GatewayServer(
        App(),
        config=GatewayConfig(drain_timeout_ms=10),
        transport=transport,
        scheduler=scheduler,
        process_control=process_control,
    )

    assert server.run() == 0
    assert server.lifecycle.state == STATE_STOPPED
    assert server.lifecycle.failed_reason == ""
    assert transport.closed == [transport.listener_fd]
    assert scheduler.stopped == 1
    assert process_control.installed == 1
    assert process_control.polled == 1
    assert process_control.uninstalled == 1


def test_shutdown_retires_accept_owner_before_stopping_carriers() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    scheduler.cancel_completes = False
    scheduler.outcome_values = [
        virtual_thread.OUTCOME_PENDING,
        virtual_thread.OUTCOME_PENDING,
        virtual_thread.OUTCOME_CANCELLED,
    ]
    server = GatewayServer(
        App(),
        config=GatewayConfig(
            install_signal_handlers=False,
            drain_timeout_ms=10,
            control_poll_ms=2,
        ),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )

    server.start()
    accept_thread = server.accept_thread
    assert server._shutdown_all() is None

    assert scheduler.cancelled == [accept_thread]
    assert server.accept_thread is None
    assert server.lifecycle.state == STATE_STOPPED
    assert transport.idle_waits == [2, 2]
    assert scheduler.events.index(("outcome", accept_thread)) < (
        scheduler.events.index(("stop",))
    )
    assert scheduler.events.index(("cancel", accept_thread)) < (
        scheduler.events.index(("stop",))
    )
    assert transport.closed == [transport.listener_fd]


def test_shutdown_observes_normal_accept_return_before_stopping_carriers() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    scheduler.outcome_value = virtual_thread.OUTCOME_RETURNED
    scheduler.result_value = "accept-finished"
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )

    server.start()
    accept_thread = server.accept_thread
    assert server._shutdown_all() is None

    assert scheduler.cancelled == [accept_thread]
    assert scheduler.result_calls == [accept_thread]
    assert scheduler.join_calls == []
    assert server.accept_thread is None
    assert server.lifecycle.state == STATE_STOPPED
    assert server.lifecycle.failed_reason == ""


def test_listener_fd_closes_only_after_accept_owner_is_terminal() -> None:
    events = []

    class OrderedTransport(FakeTransport):
        def close(self, fd: int) -> int:
            events.append(("close", fd))
            return super().close(fd)

    class OrderedScheduler(FakeScheduler):
        def outcome(self, thread):
            events.append(("outcome", thread))
            return super().outcome(thread)

        def stop(self) -> None:
            events.append(("stop",))
            super().stop()

    transport = OrderedTransport()
    scheduler = OrderedScheduler()
    scheduler.outcome_value = virtual_thread.OUTCOME_CANCELLED
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )
    server.start()
    listener_fd = server.listener_fd

    server.request_stop()
    assert listener_fd not in transport.closed
    assert server._shutdown_all() is None

    assert events.index(("outcome", server.scheduler.cancelled[0])) < events.index(
        ("close", listener_fd)
    )
    assert events.index(("close", listener_fd)) > events.index(("stop",))


def test_shutdown_records_accept_owner_exception_and_still_stops_carriers() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    failure = RuntimeError("accept teardown exploded")
    scheduler.outcome_value = virtual_thread.OUTCOME_RAISED
    scheduler.exception_value = failure
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )

    server.start()
    accept_thread = server.accept_thread
    cleanup_error = server._shutdown_all()

    assert cleanup_error is failure
    assert server.lifecycle.state == STATE_FAILED
    assert server.lifecycle.failed_reason == (
        "gateway accept owner raised during shutdown: accept teardown exploded"
    )
    assert server.accept_thread is None
    assert scheduler.stopped == 1
    assert scheduler.events.index(("outcome", accept_thread)) < (
        scheduler.events.index(("stop",))
    )
    assert scheduler.events.index(("cancel", accept_thread)) < (
        scheduler.events.index(("stop",))
    )


def test_shutdown_bounds_pending_accept_owner_before_stopping_carriers() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    scheduler.cancel_completes = False
    server = GatewayServer(
        App(),
        config=GatewayConfig(
            install_signal_handlers=False,
            drain_timeout_ms=5,
            control_poll_ms=2,
        ),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )

    server.start()
    accept_thread = server.accept_thread
    cleanup_error = server._shutdown_all()

    assert isinstance(cleanup_error, GatewayError)
    assert str(cleanup_error) == (
        "gateway accept owner did not terminate before shutdown deadline"
    )
    assert server.lifecycle.state == STATE_FAILED
    assert server.lifecycle.failed_reason == str(cleanup_error)
    assert server.accept_thread == accept_thread
    assert transport.idle_waits == [2, 2, 1]
    assert scheduler.stopped == 0
    assert server.pool_started is True
    assert server.app_started is True
    assert transport.closed == []
    assert ("cancel", accept_thread) in scheduler.events
    assert ("outcome", accept_thread) in scheduler.events
    assert ("stop",) not in scheduler.events

    # The first timeout retained the exact execution root. Once the accept
    # continuation becomes terminal, an embedding owner can retry teardown;
    # no parked root or listener number is carried into a restarted pool.
    scheduler.outcome_value = virtual_thread.OUTCOME_CANCELLED
    server.shutdown()
    assert server.accept_thread is None
    assert scheduler.stopped == 1
    assert server.pool_started is False
    assert server.app_started is False
    assert transport.closed == [transport.listener_fd]


def test_start_rollback_retires_partially_published_accept_owner() -> None:
    transport = FakeTransport()

    class PartiallyPublishingScheduler(FakeScheduler):
        def spawn_accept(self, server):
            thread = super().spawn_accept(server)
            server.accept_thread = thread
            raise RuntimeError("accept publication failed")

    scheduler = PartiallyPublishingScheduler()
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )

    with pytest.raises(RuntimeError, match="^accept publication failed$"):
        server.start()

    accept_thread = scheduler.accept_thread
    assert scheduler.cancelled == [accept_thread]
    assert server.accept_thread is None
    assert scheduler.stopped == 1
    assert server.pool_started is False
    assert server.app_started is False
    assert scheduler.events.index(("outcome", accept_thread)) < (
        scheduler.events.index(("stop",))
    )
    assert scheduler.events.index(("cancel", accept_thread)) < (
        scheduler.events.index(("stop",))
    )


def test_start_rollback_surfaces_and_preserves_nonterminal_accept_owner() -> None:
    class PartiallyPublishingScheduler(FakeScheduler):
        def spawn_accept(self, server):
            thread = super().spawn_accept(server)
            server.accept_thread = thread
            raise RuntimeError("accept publication failed")

    transport = FakeTransport()
    scheduler = PartiallyPublishingScheduler()
    scheduler.cancel_completes = False
    server = GatewayServer(
        App(),
        config=GatewayConfig(
            install_signal_handlers=False,
            drain_timeout_ms=5,
            control_poll_ms=2,
        ),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )

    with pytest.raises(
        GatewayError,
        match="gateway accept owner did not terminate before shutdown deadline",
    ):
        server.start()

    assert server.accept_thread == scheduler.accept_thread
    assert server.pool_started is True
    assert server.app_started is True
    assert scheduler.stopped == 0
    assert transport.listener_fd not in transport.closed

    scheduler.outcome_value = virtual_thread.OUTCOME_CANCELLED
    server.shutdown()
    assert server.accept_thread is None
    assert server.pool_started is False
    assert server.app_started is False
    assert scheduler.stopped == 1
    assert transport.listener_fd in transport.closed


def test_connection_finish_releases_admission_when_generation_cleanup_fails() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=transport,
        scheduler=scheduler,
    )
    server.start()
    transport.accepts.append((PCC_SOCKET_PROGRESS, 44, "127.0.0.1"))
    assert server.accept_once() == 1
    connection = scheduler.spawned[-1][2]

    class FailingGeneration:
        def release(self):
            raise RuntimeError("generation cleanup failed")

    connection.generation = FailingGeneration()
    with pytest.raises(RuntimeError, match="generation cleanup failed"):
        server._connection_finished(connection)
    assert server.connections == []
    assert server.lifecycle.metrics.get("connections_active") == 0


def test_shutdown_preserves_runtime_owners_when_carrier_stop_fails() -> None:
    events = []
    scheduler = FakeScheduler()
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=FakeTransport(),
        scheduler=scheduler,
    )
    server.start()

    class FailingPool:
        def close_idle(self):
            events.append("pool")
            raise RuntimeError("pool cleanup failed")

    class FailingControl:
        def uninstall(self):
            events.append("control")
            raise RuntimeError("control cleanup failed")

    def stop():
        events.append("scheduler")
        raise RuntimeError("scheduler cleanup failed")

    def shutdown():
        events.append("app")
        raise RuntimeError("app cleanup failed")

    server.proxy_pools = {"broken": FailingPool()}
    server.scheduler.stop = stop
    server.app.shutdown = shutdown
    server.process_control = FailingControl()
    server.process_control_installed = True
    cleanup_error = server._shutdown_all()

    assert str(cleanup_error) == "pool cleanup failed"
    assert events == ["pool", "scheduler"]
    assert server.pool_started is True
    assert server.app_started is True
    assert server.listener_fd == server.transport.listener_fd
    # Carrier-stop failure means application/TLS/control owners may still be
    # in use. They remain published for a later teardown attempt.
    assert server.process_control_installed is True


def test_shutdown_timeout_keeps_connection_ledger_until_owner_finishes() -> None:
    transport = FakeTransport()
    scheduler = FakeScheduler()
    server = GatewayServer(
        App(),
        config=GatewayConfig(
            install_signal_handlers=False,
            drain_timeout_ms=5,
            control_poll_ms=2,
        ),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )
    server.start()
    accept_thread = server.accept_thread
    transport.accepts.append((PCC_SOCKET_PROGRESS, 44, "127.0.0.1"))
    assert server.accept_once() == 1
    connection = server.connections[0]
    connection_thread = connection.vthread

    cleanup_error = server._shutdown_all()

    assert isinstance(cleanup_error, GatewayError)
    assert str(cleanup_error) == (
        "gateway connection owners did not terminate before shutdown deadline"
    )
    assert scheduler.cancelled == [accept_thread]
    assert connection_thread not in scheduler.cancelled
    assert transport.shutdowns == [44]
    assert server.connections == [connection]
    assert server.lifecycle.metrics.get("connections_active") == 1
    assert server.pool_started is True
    assert scheduler.stopped == 0
    assert transport.listener_fd not in transport.closed

    # Model the connection continuation's own finally block. Only that owner
    # closes request/socket state and publishes _connection_finished.
    connection.close("test-owner-terminal")
    server._connection_finished(connection)
    server.shutdown()

    assert server.connections == []
    assert server.lifecycle.metrics.get("connections_active") == 0
    assert server.pool_started is False
    assert scheduler.stopped == 1
    assert transport.listener_fd in transport.closed


def test_shutdown_waits_past_resource_release_for_task_terminal_outcome() -> None:
    class PerOwnerScheduler(FakeScheduler):
        def __init__(self) -> None:
            super().__init__()
            self.outcomes = {}

        def spawn_accept(self, server):
            thread = super().spawn_accept(server)
            self.outcomes[thread] = virtual_thread.OUTCOME_PENDING
            return thread

        def spawn_connection(self, server, connection):
            thread = super().spawn_connection(server, connection)
            self.outcomes[thread] = virtual_thread.OUTCOME_PENDING
            return thread

        def cancel(self, thread) -> bool:
            self.cancelled.append(thread)
            self.events.append(("cancel", thread))
            if thread == self.accept_thread:
                self.outcomes[thread] = virtual_thread.OUTCOME_CANCELLED
            return True

        def outcome(self, thread) -> int:
            self.events.append(("outcome", thread))
            return self.outcomes[thread]

    transport = FakeTransport()
    scheduler = PerOwnerScheduler()
    server = GatewayServer(
        App(),
        config=GatewayConfig(
            install_signal_handlers=False,
            drain_timeout_ms=5,
            control_poll_ms=2,
        ),
        transport=transport,
        scheduler=scheduler,
        process_control=ForbiddenProcessControl(),
    )
    server.start()
    transport.accepts.append((PCC_SOCKET_PROGRESS, 45, "127.0.0.1"))
    assert server.accept_once() == 1
    connection = server.connections[0]
    thread = connection.vthread

    # This is the narrow window inside _gateway_connection_entry: its finally
    # has released every connection resource, but the runtime has not yet
    # published the surrounding task's RETURNED outcome.
    connection.close("entry-finally")
    server._connection_finished(connection)
    assert server.connections == []
    assert server.connection_owners == [connection]

    cleanup_error = server._shutdown_all()
    assert isinstance(cleanup_error, GatewayError)
    assert scheduler.stopped == 0
    assert server.pool_started is True
    assert server.connection_owners == [connection]
    assert transport.listener_fd not in transport.closed

    scheduler.outcomes[thread] = virtual_thread.OUTCOME_RETURNED
    server.shutdown()
    assert server.connection_owners == []
    assert connection.vthread is None
    assert scheduler.stopped == 1
    assert transport.listener_fd in transport.closed


def test_run_surfaces_incomplete_shutdown_while_execution_root_is_live() -> None:
    server = GatewayServer(
        App(),
        config=GatewayConfig(install_signal_handlers=False),
        transport=FakeTransport(),
        scheduler=FakeScheduler(),
        process_control=ForbiddenProcessControl(),
    )
    cleanup_error = GatewayError("structured shutdown incomplete")
    server.start = lambda: None
    server.lifecycle.fail("request loop failed")
    server.pool_started = True
    server._shutdown_all = lambda: cleanup_error

    with pytest.raises(GatewayError, match="^structured shutdown incomplete$"):
        server.run()


def test_unowned_tls_provider_configuration_fails_closed() -> None:
    with pytest.raises(TlsProviderError, match="explicit registry"):
        GatewayServer(
            App(),
            config=GatewayConfig(
                listeners=(
                    ListenerConfig(
                        tls_provider="provider",
                        tls_config=TlsConfig("cert", "key"),
                    ),
                )
            ),
        )


def test_app_run_has_top_level_pcc1_gateway_closure_edge() -> None:
    source = (REPO / "pcc" / "web" / "app.py").read_text(encoding="utf-8")
    assert "from pcc.gateway.server import GatewayServer" in source
    assert "        from pcc.gateway.server import GatewayServer" not in source


def test_native_spawn_entries_have_compiler_visible_may_park_chain() -> None:
    from pcc.py_frontend.codegen.vthread_effect_analysis import (
        compute_vthread_may_park_functions,
    )
    from pcc.py_frontend.parser import parse

    path = REPO / "pcc" / "gateway" / "server.py"
    module = parse(path.read_text(encoding="utf-8"), str(path))
    _function_ids, names = compute_vthread_may_park_functions(module)
    assert {
        "_park_native_fd",
        "_gateway_accept_entry",
        "_wait_connection_fd",
        "_wait_dns_fd",
        "_resolve_upstream_address",
        "_proxy_exchange_attempt",
        "_run_gateway_proxy",
        "_run_tls_handshake",
        "_run_tls_close_notify",
        "_run_gateway_connection",
        "_gateway_connection_entry",
    } <= names


@pytest.mark.integration
@pytest.mark.pcc_gate(probe="pcc1")
def test_current_pcc1_self_no_libpython_product_shaped_gateway_core(
    tmp_path: Path,
    threaded_pcc_py_runtime_archive: Path,
) -> None:
    """Compile with current pcc1; this does not claim a live listener gate."""
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the gateway product gate")

    executable = tmp_path / "current_pcc1_gateway_app"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_ARCHIVE"] = str(threaded_pcc_py_runtime_archive)
    env["PCC_WITH_THREADS"] = "1"
    command = [
        str(pcc1),
        "--backend",
        "self",
        "--python-libpython=off",
        "--ir-scaffold=on",
        str(PCC1_PRODUCT_SOURCE),
        "-o",
        str(executable),
    ]
    built = subprocess.run(
        command,
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(executable)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout.strip() == "PCC1_GATEWAY_HTTP1_LOCAL_OK"
