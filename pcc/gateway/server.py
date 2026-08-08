"""Virtual-thread HTTP/1 connection and listener kernel for ``pcc.gateway``.

The protocol core is deliberately transport-independent: tests and current-
``pcc1`` product gates can feed fragmented bytes without borrowing CPython's
socket or event-loop implementations.  ``NativeSocketTransport`` is the
production bridge to pcc's freestanding, nonblocking observation ABI.  Each
accepted connection is scheduled as one pcc virtual thread and blocking-looking
waits lower to ``block_current_on_fd``; the underlying carrier is not meant to
block.

This slice owns local HTTP/1 dispatch, TLS listener driving and pcc-owned
numeric/hosts/DNS HTTP/1 upstreams.  TLS cryptography stays behind the reviewed
native provider ABI; no Python ``ssl`` or host interpreter participates.
"""

from pcc.extern import c_int64, c_ptr, extern
from pcc.py_runtime.py.py_abi_constants import PYBYTESOBJECT_DATA_OFFSET
from pcc.unsafe import load_i64, null, ptr_add, stack_alloc
import pcc.virtual_thread as virtual_thread
import threading

from .buffer import (
    BACKPRESSURE_HIGH,
    BACKPRESSURE_LOW,
    BufferLimitError,
    ChannelBuffer,
)
from .config import GatewayConfig, ListenerConfig
from .control import GatewayProcessControl
from .dns import DNS_A, DNS_AAAA, DNS_INTEREST_WRITE, normalize_numeric_address
from .dns_native import LazySystemResolver, NativeDnsTransport
from .http1 import (
    BodyChunk,
    Http1Error,
    Http1ResponseEncoder,
    Http1ServerCodec,
    RequestEnd,
    RequestHead,
)
from .lifecycle import (
    GatewayLifecycle,
    STATE_DRAINING,
    STATE_FAILED,
    STATE_RUNNING,
    STATE_STOPPED,
)
from .proxy import ProxyTransportPlan, proxy_failure_status
from .proxy_http1 import (
    ProxyDeadline,
    ProxyExchange,
    ProxyProtocolError,
    UpstreamConnectionPool,
)
from .tls import (
    PCC_NATIVE_TLS_ABI_NAME,
    PCC_NATIVE_TLS_PROVIDER_NAME,
    TLS_CLOSED,
    TLS_INTEREST_READ,
    TLS_INTEREST_WRITE,
    TLS_OK,
    TLS_SELECT_SNI,
    TLS_WANT_READ,
    TLS_WANT_WRITE,
    TlsConfig,
    TlsGenerationManager,
    TlsProviderError,
    production_tls_registry,
)


PCC_SOCKET_PROGRESS = 0
PCC_SOCKET_WOULD_BLOCK = 1
PCC_SOCKET_EOF = 2
PCC_SOCKET_CONNECTED = 3

PCC_IO_READ = 1
PCC_IO_WRITE = 4

_FLUSH_TIMEOUT = -1
_PROXY_CLIENT_OBSERVE_SLICE_MS = 25
_PROXY_DOWNSTREAM_RESPONSE_COMMITTED = "downstream-protocol-committed"


_platform_tcp_listen_with_backlog = extern(
    "pcc_platform_tcp_listen_with_backlog",
    (c_ptr, c_ptr, c_int64, c_int64),
    c_int64,
)
_platform_tcp_accept_observe = extern(
    "pcc_platform_tcp_accept_observe", (c_int64, c_ptr), c_int64
)
_platform_socket_peer_text = extern(
    "pcc_platform_socket_peer_text", (c_int64, c_ptr, c_int64), c_int64
)
_platform_tcp_connect_start = extern(
    "pcc_platform_tcp_connect_start", (c_ptr, c_ptr, c_ptr), c_int64
)
_platform_socket_connect_observe = extern(
    "pcc_platform_socket_connect_observe", (c_int64, c_int64), c_int64
)
_platform_socket_read_observe = extern(
    "pcc_platform_socket_read_observe",
    (c_int64, c_ptr, c_int64, c_int64, c_ptr),
    c_int64,
)
_platform_socket_write_observe = extern(
    "pcc_platform_socket_write_observe",
    (c_int64, c_ptr, c_int64, c_int64, c_ptr),
    c_int64,
)
_platform_socket_shutdown = extern(
    "pcc_platform_socket_shutdown", (c_int64, c_int64), c_int64
)
_platform_close = extern("pcc_platform_close", (c_int64,), c_int64)
_platform_monotonic_us = extern("pcc_platform_monotonic_us", (), c_int64)
_platform_sleep_ns = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)
_py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
_py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
_py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)


class GatewayError(RuntimeError):
    pass


class GatewayTransportError(GatewayError):
    pass


class UnsupportedGatewayFeature(GatewayError):
    pass


class GatewayHooks:
    """Fixed-cardinality observability hooks; methods are intentionally no-op."""

    def connection_opened(self, connection) -> None:
        pass

    def connection_closed(self, connection, reason: str) -> None:
        pass

    def request_started(self, connection, request) -> None:
        pass

    def request_finished(self, connection, request, response) -> None:
        pass

    def backpressure_changed(
        self, connection, enabled: bool, queued_bytes: int
    ) -> None:
        pass

    def deadline_exceeded(self, connection, phase: str) -> None:
        pass

    def dns_started(self, connection, host: str) -> None:
        pass

    def dns_finished(
        self, connection, host: str, address_count: int, error: str
    ) -> None:
        pass

    def tls_handshake_started(self, connection, channel) -> None:
        pass

    def tls_handshake_completed(self, connection, channel) -> None:
        pass

    def tls_handshake_failed(self, connection, error_name: str) -> None:
        pass

    def tls_closed(self, connection, graceful: bool) -> None:
        pass


class _SyntheticRequest:
    """Minimal request shape used only for transport-generated responses."""

    def __init__(self, method: str = "GET") -> None:
        self.method = method


class NativeSocketTransport:
    """Bridge managed gateway objects to the freestanding socket ABI."""

    native_virtual_threads = True
    # Capability, not a concrete transport type check: another pcc-native
    # transport may opt into the same connection-owner/handler-child contract.
    local_body_streaming = True

    def now_ms(self) -> int:
        return _platform_monotonic_us() // 1000

    def listen(
        self,
        host: str,
        port: int,
        reuse_port: bool,
        backlog: int = 128,
    ) -> int:
        host_pointer = null()
        if host:
            host_pointer = _py_str_utf8(host)
        port_text = str(port)
        return _platform_tcp_listen_with_backlog(
            host_pointer,
            _py_str_utf8(port_text),
            1 if reuse_port else 0,
            backlog,
        )

    def accept(self, listener_fd: int):
        output_fd = stack_alloc(8)
        outcome = _platform_tcp_accept_observe(listener_fd, output_fd)
        accepted_fd = -1
        client_ip = ""
        if outcome == PCC_SOCKET_PROGRESS:
            accepted_fd = load_i64(output_fd, 0)
            peer_text = stack_alloc(64)
            peer_length = _platform_socket_peer_text(accepted_fd, peer_text, 64)
            if peer_length <= 0:
                _platform_close(accepted_fd)
                return peer_length if peer_length < 0 else -1, -1, ""
            converted = _py_str_new(peer_text, peer_length)
            if not isinstance(converted, str):
                _platform_close(accepted_fd)
                return -1, -1, ""
            client_ip = converted
        return outcome, accepted_fd, client_ip

    def open_upstream(self, endpoint, address: str = "") -> int:
        """Own one nonblocking connection to an already resolved address."""
        if not address:
            address = endpoint.host
        if normalize_numeric_address(address) is None:
            return -1
        output_fd = stack_alloc(8)
        outcome = _platform_tcp_connect_start(
            _py_str_utf8(address),
            _py_str_utf8(str(endpoint.port)),
            output_fd,
        )
        fd = load_i64(output_fd, 0)
        if outcome == PCC_SOCKET_CONNECTED or outcome == PCC_SOCKET_WOULD_BLOCK:
            return fd
        return -1

    def connect_observe(self, fd: int) -> int:
        # Parking is compiler-visible in the top-level proxy driver.  The ABI
        # call itself is therefore a zero-time pure observation.
        return _platform_socket_connect_observe(fd, 0)

    def read(self, fd: int, limit: int):
        if limit > 65536:
            limit = 65536
        storage = stack_alloc(65536)
        output_count = stack_alloc(8)
        outcome = _platform_socket_read_observe(
            fd, storage, limit, 0, output_count
        )
        count = load_i64(output_count, 0)
        if outcome == PCC_SOCKET_PROGRESS and count > 0:
            return outcome, _py_bytes_new(storage, count)
        return outcome, b""

    def write(self, fd: int, data: bytes):
        output_count = stack_alloc(8)
        outcome = _platform_socket_write_observe(
            fd,
            ptr_add(data, PYBYTESOBJECT_DATA_OFFSET),
            len(data),
            0,
            output_count,
        )
        return outcome, load_i64(output_count, 0)

    def shutdown(self, fd: int) -> int:
        return _platform_socket_shutdown(fd, 2)

    def close(self, fd: int) -> int:
        return _platform_close(fd)

    def idle_wait(self, delay_ms: int) -> None:
        _platform_sleep_ns(delay_ms * 1000000)


def _park_native_fd(fd: int, events: int, timeout_ms: int) -> None:
    """Compiler-visible park leaf for transitive ``may_park`` analysis."""
    virtual_thread.block_current_on_fd(fd, events, timeout_ms)


def _gateway_accept_entry(server) -> None:
    # One immutable numeric descriptor belongs to this continuation until it
    # returns.  Server shutdown keeps that descriptor open until cancellation
    # has unregistered the wait owner, so no iteration can observe a reused fd.
    listener_fd = server.listener_fd
    while server.lifecycle.state == STATE_RUNNING and listener_fd >= 0:
        if server.listener_fd != listener_fd:
            raise GatewayTransportError(
                "gateway listener descriptor changed under accept owner"
            )
        accepted = virtual_thread.call(server.accept_once, listener_fd)
        if accepted == 0:
            if server.transport.native_virtual_threads:
                _park_native_fd(
                    listener_fd,
                    PCC_IO_READ,
                    server.config.accept_poll_ms,
                )
            else:
                server.transport.wait(
                    listener_fd,
                    PCC_IO_READ,
                    server.config.accept_poll_ms,
                )


def _gateway_connection_entry(server, connection) -> None:
    try:
        _run_gateway_connection(connection)
    finally:
        virtual_thread.call(server._connection_finished, connection)


def _gateway_local_handler_entry(connection, request):
    """Closed spawn target for one open-world pcc.web dispatch callback."""
    return virtual_thread.call(connection.app.dispatch, request)


class NativeVirtualThreadScheduler:
    """Concrete spawn sites keep pcc1 continuation analysis closed-world."""

    def start(self, carrier_count: int) -> int:
        return virtual_thread.carrier_pool_start(carrier_count)

    def stop(self) -> int:
        return virtual_thread.carrier_pool_stop()

    def spawn_accept(self, server):
        return virtual_thread.spawn(_gateway_accept_entry, server)

    def spawn_connection(self, server, connection):
        return virtual_thread.spawn(_gateway_connection_entry, server, connection)

    def cancel(self, thread) -> bool:
        return virtual_thread.cancel(thread)

    def join(self, thread):
        """Join only when the caller is itself a running virtual thread."""

        return virtual_thread.join(thread)

    def result(self, thread):
        return virtual_thread.result(thread)

    def outcome(self, thread) -> int:
        return virtual_thread.outcome(thread)

    def exception(self, thread):
        return virtual_thread.exception(thread)


def _minimum_deadline(first: int, second: int) -> int:
    if first < 0:
        return second
    if second < 0:
        return first
    if first < second:
        return first
    return second


class GatewayConnection:
    """One incremental HTTP/1 session, normally owned by one virtual thread."""

    def __init__(
        self,
        app,
        fd: int,
        transport,
        lifecycle: GatewayLifecycle,
        generation,
        config: GatewayConfig,
        hooks=None,
        client_ip: str = "",
        proxy_pools=None,
        tls_channel=None,
        resolver=None,
        dns_transport=None,
    ) -> None:
        if hooks is None:
            hooks = GatewayHooks()
        self.app = app
        self.fd = fd
        self.transport = transport
        self.lifecycle = lifecycle
        self.generation = generation
        self.config = config
        self.hooks = hooks
        self.client_ip = client_ip
        self.proxy_pools = {} if proxy_pools is None else proxy_pools
        self.resolver = resolver
        self.dns_transport = dns_transport
        self.tls_channel = tls_channel
        self.io_wait_interest = PCC_IO_READ
        self.tls_handshake_deadline_ms = -1
        self.codec = Http1ServerCodec(
            config.http1.request_line_bytes,
            config.http1.header_bytes,
            config.http1.header_count,
            config.http1.body_bytes,
            config.http1.chunk_bytes,
        )
        self.encoder = Http1ResponseEncoder()
        self.output = ChannelBuffer(
            segment_size=config.buffers.segment_bytes,
            low_watermark=config.buffers.low_watermark,
            high_watermark=config.buffers.high_watermark,
            max_bytes=config.buffers.connection_bytes,
        )
        self.current_request = None
        self.current_keep_alive = True
        self.current_admitted = False
        self.pending_proxy = None
        self.pending_proxy_request = None
        self.pending_local_request = None
        self.pending_local_thread = None
        self.pending_stream_request = None
        self.pending_stream_response = None
        self.pending_stream_iterator = None
        self.pending_stream_chunk = b""
        self.pending_stream_offset = 0
        self.pending_stream_admitted = False
        self.pending_stream_empty_chunks = 0
        self.deferred_events = []
        self.requests_completed = 0
        self.close_after_flush = False
        self.closed = False
        self.vthread = None
        self.close_reason = ""
        self.read_deadline_ms = -1
        self.header_deadline_ms = -1
        self.write_deadline_ms = -1

    def now_ms(self) -> int:
        if self.transport is None:
            return 0
        return self.transport.now_ms()

    def _queue_bytes(self, data: bytes) -> None:
        if not data:
            return
        if not self.lifecycle.reserve_buffered(len(data)):
            raise BufferLimitError("gateway global buffered-byte limit exceeded")
        try:
            transition = self.output.append(data)
        except Exception:
            self.lifecycle.release_buffered(len(data))
            raise
        if self.write_deadline_ms < 0:
            self.write_deadline_ms = self.now_ms() + self.config.write_timeout_ms
        if transition == BACKPRESSURE_HIGH:
            self.lifecycle.metrics.add("backpressure_parks")
            self.hooks.backpressure_changed(self, True, len(self.output))

    def _consume_output(self, count: int) -> None:
        if count <= 0:
            return
        transition = self.output.consume(count)
        self.lifecycle.release_buffered(count)
        if transition == BACKPRESSURE_LOW:
            self.hooks.backpressure_changed(self, False, len(self.output))
        if len(self.output) == 0:
            self.write_deadline_ms = -1
        else:
            self.write_deadline_ms = self.now_ms() + self.config.write_timeout_ms

    def peek_output(self, limit: int = -1) -> bytes:
        views = self.output.peek_views(limit)
        result = bytearray(b"")
        for view in views:
            result.extend(view.to_bytes())
            view.release()
        return bytes(result)

    def take_output(self, limit: int = -1) -> bytes:
        result = self.peek_output(limit)
        self._consume_output(len(result))
        return result

    def _abort_current_request(self, reason: str) -> None:
        request = self.current_request
        if request is None:
            return
        admitted = self.current_admitted
        proxy_owned = self.pending_proxy_request is request
        local_owned = self.pending_local_request is request
        self.current_request = None
        self.current_admitted = False
        first_error = None
        try:
            request.cancellation.cancel(reason)
        except Exception as error:
            first_error = error
        # The pending proxy owns both body finalization and the one request
        # admission after _begin_request transfers ownership.  Leave those
        # owners intact for its finally/_cancel_pending_proxy path; close()
        # invokes that path immediately after this one.
        if local_owned:
            # Wake the handler's Event waiter before cancellation can mark the
            # child ready.  The waiter FIFO then retires its scheduler root
            # through Event.set instead of retaining a cancelled task.
            try:
                request.body.cancel()
            except Exception as error:
                if first_error is None:
                    first_error = error
        elif not proxy_owned:
            try:
                request.body.cancel()
            except Exception as error:
                if first_error is None:
                    first_error = error
            try:
                request.body.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
            if admitted:
                try:
                    self.lifecycle.release_request()
                except Exception as error:
                    if first_error is None:
                        first_error = error
        if first_error is not None:
            raise first_error

    def _signal_pending_local(self, reason: str) -> None:
        """Wake-before-cancel without taking the pending child's owners."""
        request = self.pending_local_request
        thread = self.pending_local_thread
        if request is None or thread is None:
            return
        first_error = None
        try:
            request.cancellation.cancel(reason)
        except Exception as error:
            first_error = error
        try:
            request.body.cancel()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            virtual_thread.cancel(thread)
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def _signal_owner_shutdown(self, reason: str) -> None:
        """Request cooperative teardown without taking continuation owners.

        The connection virtual thread remains the only code allowed to join a
        local handler or release request/body/admission ownership.  A control
        thread may only publish cancellation, wake body waiters and shut down
        the descriptor so a parked connection gets another scheduling edge.
        """

        first_error = None
        requests = []
        for request in (
            self.current_request,
            self.pending_local_request,
            self.pending_proxy_request,
            self.pending_stream_request,
        ):
            if request is None:
                continue
            duplicate = False
            for existing in requests:
                if existing is request:
                    duplicate = True
                    break
            if duplicate:
                continue
            requests.append(request)
            try:
                request.cancellation.cancel(reason)
            except Exception as error:
                if first_error is None:
                    first_error = error
            try:
                request.body.cancel()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self.pending_local_thread is not None:
            try:
                virtual_thread.cancel(self.pending_local_thread)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self.transport is not None and self.fd >= 0:
            try:
                self.transport.shutdown(self.fd)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _take_pending_local(self):
        request = self.pending_local_request
        thread = self.pending_local_thread
        self.pending_local_request = None
        self.pending_local_thread = None
        return request, thread

    def _cancel_pending_proxy(self, reason: str) -> None:
        request = self.pending_proxy_request
        if request is None:
            return
        self.pending_proxy = None
        self.pending_proxy_request = None
        first_error = None
        try:
            request.cancellation.cancel(reason)
        except Exception as error:
            first_error = error
        try:
            request.body.cancel()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            request.body.close()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            self.lifecycle.release_request()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def _cancel_pending_stream(self, reason: str) -> None:
        request = self.pending_stream_request
        if request is None:
            return
        admitted = self.pending_stream_admitted
        self.pending_stream_request = None
        self.pending_stream_response = None
        self.pending_stream_iterator = None
        self.pending_stream_chunk = b""
        self.pending_stream_offset = 0
        self.pending_stream_admitted = False
        first_error = None
        try:
            request.cancellation.cancel(reason)
        except Exception as error:
            first_error = error
        try:
            request.body.close()
        except Exception as error:
            if first_error is None:
                first_error = error
        if admitted:
            try:
                self.lifecycle.release_request()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _begin_streaming_response(self, request, response, admitted: bool) -> None:
        # User iterator construction may execute arbitrary __iter__ code and
        # raise.  Do it before queueing a committed head or publishing any
        # pending owner; the caller still owns admission/body rollback here.
        iterator = iter(response.body)
        headers = []
        for name, value in response.headers:
            if name.lower() not in (
                "content-length",
                "transfer-encoding",
                "connection",
            ):
                headers.append((name, value))
        keep_alive = self.current_keep_alive and not self.close_after_flush
        self._queue_bytes(
            self.encoder.head(
                response.status,
                headers,
                content_length=-1,
                chunked=True,
                keep_alive=keep_alive,
            )
        )
        response.committed = True
        self.pending_stream_request = request
        self.pending_stream_response = response
        self.pending_stream_iterator = iterator
        self.pending_stream_chunk = b""
        self.pending_stream_offset = 0
        self.pending_stream_admitted = admitted
        self.pending_stream_empty_chunks = 0

    def _complete_streaming_response(self) -> None:
        request = self.pending_stream_request
        response = self.pending_stream_response
        if request is None or response is None:
            return
        admitted = self.pending_stream_admitted
        # Take every published owner before invoking a re-entrant hook.  A
        # hook-triggered close then observes no pending stream and cannot
        # release its admission/body a second time.
        self.pending_stream_request = None
        self.pending_stream_response = None
        self.pending_stream_iterator = None
        self.pending_stream_chunk = b""
        self.pending_stream_offset = 0
        self.pending_stream_admitted = False
        self.requests_completed += 1
        primary_error = None
        try:
            self.hooks.request_finished(self, request, response)
        except Exception as error:
            primary_error = error
        try:
            request.body.close()
        except Exception as error:
            if primary_error is None:
                primary_error = error
        if admitted:
            try:
                self.lifecycle.release_request()
            except Exception as error:
                if primary_error is None:
                    primary_error = error
        if (
            not self.current_keep_alive
            or self.requests_completed >= self.config.max_requests_per_connection
            or self.lifecycle.state != STATE_RUNNING
        ):
            self.close_after_flush = True
        if primary_error is not None:
            raise primary_error

    def _drive_streaming_response(self) -> None:
        """Queue at most one bounded chunk; transport flushing stays outside."""
        request = self.pending_stream_request
        if request is None:
            return
        if request.cancellation.is_cancelled():
            # The connection owner, not the control thread, retires the user
            # iterator/body/admission tuple. Never call user ``next`` again
            # after shutdown has published cancellation.
            self._cancel_pending_stream("streaming response cancelled")
            self.close_after_flush = True
            return
        available = self.config.buffers.connection_bytes - len(self.output)
        if available <= 32:
            return
        if self.pending_stream_offset >= len(self.pending_stream_chunk):
            try:
                chunk = next(self.pending_stream_iterator)
            except StopIteration:
                self._queue_bytes(self.encoder.end_chunks())
                self._complete_streaming_response()
                return
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            if not isinstance(chunk, bytes):
                self._cancel_pending_stream("invalid response stream chunk")
                self.close_after_flush = True
                return
            if len(chunk) == 0:
                self.pending_stream_empty_chunks += 1
                if self.pending_stream_empty_chunks > 1024:
                    self._cancel_pending_stream("too many empty response chunks")
                    self.close_after_flush = True
                return
            self.pending_stream_empty_chunks = 0
            self.pending_stream_chunk = chunk
            self.pending_stream_offset = 0
        take = len(self.pending_stream_chunk) - self.pending_stream_offset
        if take > self.config.buffers.segment_bytes:
            take = self.config.buffers.segment_bytes
        if take > available - 32:
            take = available - 32
        if take <= 0:
            return
        start = self.pending_stream_offset
        data = self.pending_stream_chunk[start:start + take]
        self.pending_stream_offset += take
        self._queue_bytes(self.encoder.chunk(data))

    def _begin_request(self, head: RequestHead) -> None:
        from pcc.gateway.models import BodyStream, Cancellation, Request

        if self.current_request is not None:
            raise Http1Error(400, "overlapping-request", "request body overlaps")
        deadline = self.now_ms() + self.config.http1.body_timeout_ms
        local_body_streaming = (
            self.transport is not None
            and self.transport.local_body_streaming
            and (head.content_length > 0 or head.chunked)
        )
        body = BodyStream(
            self.config.http1.body_bytes,
            self.config.buffers.low_watermark,
            self.config.buffers.high_watermark,
            local_body_streaming,
        )
        cancellation = Cancellation(deadline)
        request = Request(
            head.method,
            head.target,
            head.version,
            head.headers,
            body,
            self.client_ip,
            "https" if self.tls_channel is not None else "http",
            cancellation,
            head.content_length,
            head.chunked,
            head.expect_continue,
        )
        self.header_deadline_ms = -1
        self.current_request = request
        self.current_keep_alive = head.keep_alive
        self.current_admitted = self.lifecycle.admit_request()
        if self.current_admitted:
            self.hooks.request_started(self, request)
            if head.expect_continue:
                self._queue_bytes(b"HTTP/1.1 100 Continue\r\n\r\n")
            early_proxy = self.app.dispatch_proxy_head(request)
            if early_proxy is not None and self.transport is not None:
                self.pending_proxy = early_proxy
                self.pending_proxy_request = request
                # Transfer the one request-admission owner immediately.  The
                # parser still aliases ``current_request`` until RequestEnd,
                # but abort/close must not release the proxy-owned admission a
                # second time.
                self.current_admitted = False
            elif local_body_streaming:
                child = virtual_thread.spawn(
                    _gateway_local_handler_entry,
                    self,
                    request,
                )
                self.pending_local_request = request
                self.pending_local_thread = child
                # The handler child owns the one admission until its terminal
                # outcome is joined by the connection owner.  The parser keeps
                # only a non-owning current_request alias until RequestEnd.
                self.current_admitted = False

    def _response_payload(self, request, response) -> bytes:
        from pcc.gateway.models import Response

        if not isinstance(response, Response):
            response = Response.text("internal server error", 500)

        headers = []
        for name, value in response.headers:
            lower = name.lower()
            # Framing and connection persistence belong to this codec, not a
            # handler.  Removing them avoids ambiguous server responses.
            if lower in ("content-length", "transfer-encoding", "connection"):
                continue
            headers.append((name, value))

        keep_alive = self.current_keep_alive and not self.close_after_flush
        content_length_forbidden = (
            100 <= response.status < 200 or response.status == 204
        )
        status_without_body = content_length_forbidden or response.status == 304
        is_head = request.method == "HEAD"
        output = bytearray(b"")
        if response.streaming:
            stream_has_wire_body = not status_without_body and not is_head
            output.extend(
                self.encoder.head(
                    response.status,
                    headers,
                    content_length=-1,
                    chunked=stream_has_wire_body,
                    keep_alive=keep_alive,
                )
            )
            return bytes(output)

        body = response.body
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not isinstance(body, bytes):
            body = bytes(body)
        content_length = len(body)
        if content_length_forbidden:
            content_length = -1
        output.extend(
            self.encoder.head(
                response.status,
                headers,
                content_length=content_length,
                chunked=False,
                keep_alive=keep_alive,
            )
        )
        if not status_without_body and not is_head:
            output.extend(body)
        return bytes(output)

    def _complete_local_handler_response(self, request, response) -> None:
        """Publish one joined local child result and retire its admission."""
        from pcc.gateway.models import Response

        if not isinstance(response, Response):
            response = Response.text("internal server error", 500)
        status_without_body = (
            100 <= response.status < 200
            or response.status == 204
            or response.status == 304
        )
        streaming = (
            response.streaming
            and not status_without_body
            and request.method != "HEAD"
        )
        transferred = False
        primary_error = None
        try:
            if streaming:
                self._begin_streaming_response(request, response, True)
                transferred = True
                return
            payload = self._response_payload(request, response)
            self._queue_bytes(payload)
            response.committed = True
            self.hooks.request_finished(self, request, response)
        except BufferLimitError:
            self.close_after_flush = True
            if len(self.output) == 0:
                fallback = Response.text("response buffer limit exceeded", 503)
                self._queue_bytes(self._response_payload(request, fallback))
        except Exception as error:
            primary_error = error
        finally:
            if not transferred:
                cleanup_error = None
                try:
                    self.lifecycle.release_request()
                except Exception as error:
                    cleanup_error = error
                try:
                    request.body.close()
                except Exception as error:
                    if cleanup_error is None:
                        cleanup_error = error
                if primary_error is None and cleanup_error is not None:
                    primary_error = cleanup_error

        if primary_error is not None:
            raise primary_error

        self.requests_completed += 1
        if (
            not self.current_keep_alive
            or self.requests_completed >= self.config.max_requests_per_connection
            or self.lifecycle.state != STATE_RUNNING
        ):
            self.close_after_flush = True

    def _finish_request(self) -> None:
        from pcc.gateway.models import Response

        request = self.current_request
        admitted = self.current_admitted
        self.current_request = None
        self.current_admitted = False
        if request is None:
            raise Http1Error(400, "orphan-request-end", "request end without head")
        request.body.finish()
        if self.pending_proxy_request is request:
            # The streaming proxy continuation already owns request admission
            # and will finish/release it after the upstream response.
            return
        if self.pending_local_request is request:
            # RequestEnd closes the producer side and wakes read/read_chunk.
            # The connection loop will join and publish the child response;
            # dispatching here would invoke the handler a second time.
            return

        response = None
        try:
            if not admitted:
                response = Response.text("gateway overloaded", 503)
                self.close_after_flush = True
            else:
                now = self.now_ms()
                if now >= request.cancellation.deadline_ms:
                    request.cancellation.cancel("request deadline exceeded")
                    self.hooks.deadline_exceeded(self, "request")
                    response = Response.text("request timeout", 408)
                    self.close_after_flush = True
                else:
                    response = virtual_thread.call(self.app.dispatch, request)
                    if isinstance(response, ProxyTransportPlan):
                        # The live proxy owns independent DNS/upstream parks.
                        # Hand its request ownership to the top-level
                        # connection loop and defer admission release.
                        if self.transport is None:
                            response = Response.text(
                                "proxy transport unavailable", 503
                            )
                        else:
                            self.pending_proxy = response
                            self.pending_proxy_request = request
                            return
                    if self.now_ms() >= request.cancellation.deadline_ms:
                        request.cancellation.cancel(
                            "request deadline exceeded"
                        )
                        self.hooks.deadline_exceeded(self, "request")
                        response = Response.text("request timeout", 408)
                        self.close_after_flush = True
            status_without_body = (
                100 <= response.status < 200
                or response.status == 204
                or response.status == 304
            )
            if (
                response.streaming
                and not status_without_body
                and request.method != "HEAD"
            ):
                self._begin_streaming_response(request, response, admitted)
                return
            payload = self._response_payload(request, response)
            self._queue_bytes(payload)
            response.committed = True
            self.hooks.request_finished(self, request, response)
        except BufferLimitError:
            self.close_after_flush = True
            if len(self.output) == 0:
                fallback = Response.text("response buffer limit exceeded", 503)
                self._queue_bytes(self._response_payload(request, fallback))
        finally:
            if (
                admitted
                and self.pending_proxy is None
                and self.pending_stream_request is None
            ):
                self.lifecycle.release_request()
            if self.pending_proxy is None and self.pending_stream_request is None:
                request.body.close()

        if self.pending_proxy is not None or self.pending_stream_request is not None:
            return
        self.requests_completed += 1
        if (
            not self.current_keep_alive
            or self.requests_completed >= self.config.max_requests_per_connection
            or self.lifecycle.state != STATE_RUNNING
        ):
            self.close_after_flush = True

    def _handle_event(self, event) -> None:
        if isinstance(event, RequestHead):
            try:
                self._begin_request(event)
            except ValueError as error:
                # Request construction owns routing normalization. Keep every
                # untrusted target failure inside the named HTTP protocol
                # boundary rather than terminating the connection vthread.
                raise Http1Error(400, "bad-target", str(error)) from error
        elif isinstance(event, BodyChunk):
            if self.current_request is None:
                event.release()
                raise Http1Error(400, "orphan-body", "body without request head")
            try:
                self.current_request.body.feed(event.view)
            finally:
                event.release()
        elif isinstance(event, RequestEnd):
            if self.current_request is not None:
                self.current_request.trailers = list(event.trailers)
            self._finish_request()

    def feed_data(self, data: bytes, max_messages: int = 0) -> int:
        """Feed one transport fragment and return its decoded event count."""
        if self.closed or self.close_after_flush:
            return 0
        events = []
        event_index = 0
        try:
            events = self.codec.feed(data, max_messages)
            handled = 0
            while event_index < len(events):
                event = events[event_index]
                # Move the ledger cursor before any owner-transferring handler.
                # BodyChunk._handle_event already releases in its finally.
                event_index += 1
                if self.close_after_flush:
                    release_index = event_index - 1
                    self._release_event_suffix(events, release_index, False)
                    break
                if (
                    self.pending_proxy is not None
                    or self.pending_local_request is not None
                    or self.pending_stream_request is not None
                ):
                    if (
                        isinstance(event, RequestHead)
                        and not self.lifecycle.queue_request()
                    ):
                        self.close_after_flush = True
                        # This head was never admitted to the deferred ledger.
                        # Release only the later codec-call-owned body views.
                        self._release_event_suffix(events, event_index, False)
                        break
                    self.deferred_events.append(event)
                else:
                    self._handle_event(event)
                    handled += 1
            if (
                self.current_request is None
                and self.codec.state == "HEAD"
                and len(self.codec.buffer) > 0
                and self.header_deadline_ms < 0
            ):
                self.header_deadline_ms = (
                    self.now_ms() + self.config.http1.header_timeout_ms
                )
            return handled
        except Http1Error as error:
            # Events before event_index have transferred to the connection or
            # deferred queue.  Everything after it is still codec-call owned;
            # release retained body views and queued-request admissions before
            # the protocol error closes the connection.
            try:
                self._release_event_suffix(events, event_index, False)
            except Exception:
                pass
            self._fail_http(error)
            return -1
        except Exception:
            try:
                self._release_event_suffix(events, event_index, False)
            except Exception:
                pass
            try:
                self._discard_deferred_events()
            except Exception:
                pass
            raise

    def _release_event_suffix(
        self, events, start: int, queued_heads: bool
    ) -> None:
        """Best-effort retirement for one untransferred event ledger suffix."""
        first_error = None
        index = start
        while index < len(events):
            event = events[index]
            try:
                if isinstance(event, BodyChunk):
                    event.release()
                elif queued_heads and isinstance(event, RequestHead):
                    self.lifecycle.release_queued_request()
            except Exception as error:
                if first_error is None:
                    first_error = error
            index += 1
        if first_error is not None:
            raise first_error

    def _discard_deferred_events(self) -> None:
        """Release every event/admission which can no longer be resumed."""
        pending = self.deferred_events
        self.deferred_events = []
        self._release_event_suffix(pending, 0, True)

    def _resume_deferred_events(self) -> None:
        pending = self.deferred_events
        self.deferred_events = []
        pending_index = 0
        try:
            while pending_index < len(pending):
                event = pending[pending_index]
                if self.close_after_flush:
                    self._release_event_suffix(pending, pending_index, True)
                    pending_index = len(pending)
                    break
                if (
                    self.pending_proxy is not None
                    or self.pending_local_request is not None
                    or self.pending_stream_request is not None
                ):
                    self.deferred_events.append(event)
                    pending_index += 1
                else:
                    if isinstance(event, RequestHead):
                        self.lifecycle.release_queued_request()
                    pending_index += 1
                    self._handle_event(event)
        except Exception:
            try:
                self._release_event_suffix(pending, pending_index, True)
            except Exception:
                pass
            raise

    def _consume_pending_proxy_body_events(self, request) -> None:
        """Move only this request's deferred body events into BodyStream."""
        pending = self.deferred_events
        self.deferred_events = []
        completed = self.current_request is not request
        pending_index = 0
        try:
            while pending_index < len(pending):
                event = pending[pending_index]
                if not completed and (
                    isinstance(event, BodyChunk) or isinstance(event, RequestEnd)
                ):
                    pending_index += 1
                    self._handle_event(event)
                    completed = self.current_request is not request
                else:
                    self.deferred_events.append(event)
                    pending_index += 1
        except Exception:
            while pending_index < len(pending):
                self.deferred_events.append(pending[pending_index])
                pending_index += 1
            raise

    def _consume_pending_local_body_events(self, request) -> None:
        """Feed only the active child's body; keep later pipeline events."""
        pending = self.deferred_events
        self.deferred_events = []
        completed = self.current_request is not request
        pending_index = 0
        try:
            while pending_index < len(pending):
                event = pending[pending_index]
                if not completed and (
                    isinstance(event, BodyChunk) or isinstance(event, RequestEnd)
                ):
                    pending_index += 1
                    self._handle_event(event)
                    completed = self.current_request is not request
                else:
                    self.deferred_events.append(event)
                    pending_index += 1
        except Exception:
            while pending_index < len(pending):
                self.deferred_events.append(pending[pending_index])
                pending_index += 1
            raise

    def _drive_pending_local(self) -> bool:
        """Poll once; terminal children are joined before publishing output."""
        from pcc.gateway.models import Response

        request = self.pending_local_request
        thread = self.pending_local_thread
        if request is None or thread is None:
            return False
        self._consume_pending_local_body_events(request)
        outcome = virtual_thread.outcome(thread)
        if outcome == virtual_thread.OUTCOME_PENDING:
            # RequestEnd has closed the producer and woken the handler.  There
            # is no client input left to observe for this request, so join the
            # child now; merely polling then waiting on the client fd would
            # miss the child-terminal wake and hang until another byte/timeout.
            if not request.body.producer_finished():
                return False
        protocol_cancelled = request.cancellation.is_cancelled()
        if protocol_cancelled and outcome == virtual_thread.OUTCOME_PENDING:
            # Body cancellation woke any Event waiter first.  Now request
            # cooperative child cancellation so a handler which ignores the
            # body/cancellation token cannot keep the structured join pending.
            virtual_thread.cancel(thread)

        # The connection virtual thread is the structured parent.  join() is
        # immediate for a terminal child but still consumes the runtime's
        # result/exception channel through the one supported ownership path.
        response = None
        if outcome == virtual_thread.OUTCOME_RETURNED or (
            outcome == virtual_thread.OUTCOME_PENDING
            and not protocol_cancelled
        ):
            try:
                response = virtual_thread.join(thread)
            except Exception:
                response = Response.text("internal server error", 500)
                self.close_after_flush = True
        else:
            try:
                virtual_thread.join(thread)
            except Exception:
                pass
            response = Response.text("internal server error", 500)
            self.close_after_flush = True
        self._take_pending_local()

        if self.current_request is request:
            self.current_request = None
            self.current_admitted = False
            if not request.body.is_ended():
                # A handler which deliberately does not drain its body cannot
                # leave unread wire bytes to be mistaken for the next request.
                request.cancellation.cancel(
                    "handler completed before body end"
                )
                request.body.cancel()
                self.close_after_flush = True

        if protocol_cancelled:
            # A deadline/parser response is already authoritative.  Retire the
            # child owner without appending a second protocol response.
            first_error = None
            try:
                self.lifecycle.release_request()
            except Exception as error:
                first_error = error
            try:
                request.body.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
            if first_error is not None:
                raise first_error
            return True
        self._complete_local_handler_response(request, response)
        return True

    def _cancel_and_join_pending_local(self, reason: str) -> None:
        """Connection-owner terminal cleanup for a parked handler child."""
        request = self.pending_local_request
        thread = self.pending_local_thread
        if request is None or thread is None:
            return
        first_error = None
        try:
            self._signal_pending_local(reason)
        except Exception as error:
            first_error = error
        try:
            # Cancellation was requested after BodyStream.cancel woke its
            # Event waiter.  Join now parks the connection parent until the
            # child runs cleanup and publishes a terminal outcome.
            virtual_thread.join(thread)
        except Exception as error:
            # Cancelled join normally raises; it is cleanup evidence, not the
            # primary connection failure.  Preserve only an earlier signal
            # failure, then continue exact-once owner retirement.
            if first_error is None and virtual_thread.outcome(thread) not in (
                virtual_thread.OUTCOME_CANCELLED,
                virtual_thread.OUTCOME_RAISED,
            ):
                first_error = error
        self._take_pending_local()
        if self.current_request is request:
            self.current_request = None
            self.current_admitted = False
        try:
            self.lifecycle.release_request()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            request.body.close()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def _fail_http(self, error: Http1Error) -> None:
        from pcc.gateway.models import Response

        self.lifecycle.metrics.add("parser_errors")
        cleanup_error = None
        try:
            self._abort_current_request(error.code)
        except Exception as cleanup:
            cleanup_error = cleanup
        try:
            self._discard_deferred_events()
        except Exception as cleanup:
            if cleanup_error is None:
                cleanup_error = cleanup
        self.close_after_flush = True
        if len(self.output) == 0:
            request = _SyntheticRequest()
            response = Response.text(str(error), error.status)
            self._queue_bytes(self._response_payload(request, response))
        if cleanup_error is not None:
            raise cleanup_error

    def input_eof(self) -> None:
        if self.closed:
            return
        try:
            self.codec.eof()
        except Http1Error as error:
            self._fail_http(error)
        self.close_after_flush = True

    def _deadline_timeout(self, phase: str) -> None:
        self.hooks.deadline_exceeded(self, phase)
        if (
            phase == "request"
            or phase == "header"
            or self.current_request is not None
        ):
            from pcc.gateway.models import Response

            self._abort_current_request("request deadline exceeded")
            if len(self.output) == 0:
                request = _SyntheticRequest()
                response = Response.text("request timeout", 408)
                self._queue_bytes(self._response_payload(request, response))
            self.close_after_flush = True
        else:
            self.close_after_flush = True

    def _flush_transport(self) -> int:
        if len(self.output) == 0:
            return PCC_SOCKET_PROGRESS
        data = self.peek_output(self.config.buffers.segment_bytes)
        if self.tls_channel is None:
            outcome, count = self.transport.write(self.fd, data)
            self.io_wait_interest = PCC_IO_WRITE
        else:
            now = self.now_ms()
            if self.write_deadline_ms < 0:
                self.write_deadline_ms = now + self.config.write_timeout_ms
            result = self.tls_channel.write(
                data, len(data), now, self.write_deadline_ms
            )
            if result.status == TLS_OK:
                outcome = PCC_SOCKET_PROGRESS
                count = result.count
            elif result.status in (TLS_WANT_READ, TLS_WANT_WRITE):
                outcome = PCC_SOCKET_WOULD_BLOCK
                count = 0
                self.io_wait_interest = (
                    PCC_IO_READ
                    if result.wait_interest == TLS_INTEREST_READ
                    else PCC_IO_WRITE
                )
            elif result.status == TLS_CLOSED:
                raise GatewayTransportError("TLS peer closed during write")
            else:
                raise GatewayTransportError(
                    "TLS write failed: " + result.error_name
                )
        if outcome == PCC_SOCKET_PROGRESS:
            if count <= 0 or count > len(data):
                raise GatewayTransportError("invalid socket write progress")
            self._consume_output(count)
            return PCC_SOCKET_PROGRESS
        if outcome == PCC_SOCKET_WOULD_BLOCK:
            now = self.now_ms()
            if self.write_deadline_ms < 0:
                self.write_deadline_ms = now + self.config.write_timeout_ms
            if now >= self.write_deadline_ms:
                self._deadline_timeout("write")
                return _FLUSH_TIMEOUT
            return PCC_SOCKET_WOULD_BLOCK
        raise GatewayTransportError("socket write observation failed")

    def _read_transport(self, limit: int):
        if self.tls_channel is None:
            self.io_wait_interest = PCC_IO_READ
            return self.transport.read(self.fd, limit)
        output = bytearray(limit)
        now = self.now_ms()
        deadline = self.read_deadline_ms
        if self.current_request is not None:
            deadline = _minimum_deadline(
                deadline, self.current_request.cancellation.deadline_ms
            )
        elif self.header_deadline_ms >= 0:
            deadline = _minimum_deadline(deadline, self.header_deadline_ms)
        result = self.tls_channel.read(output, limit, now, deadline)
        if result.status == TLS_OK:
            self.io_wait_interest = PCC_IO_READ
            return PCC_SOCKET_PROGRESS, bytes(output[: result.count])
        if result.status in (TLS_WANT_READ, TLS_WANT_WRITE):
            self.io_wait_interest = (
                PCC_IO_READ
                if result.wait_interest == TLS_INTEREST_READ
                else PCC_IO_WRITE
            )
            return PCC_SOCKET_WOULD_BLOCK, b""
        if result.status == TLS_CLOSED:
            return PCC_SOCKET_EOF, b""
        raise GatewayTransportError("TLS read failed: " + result.error_name)

    def run(self) -> None:
        """Drive this connection until EOF, timeout, drain or hard failure."""
        _run_gateway_connection(self)

    def begin_drain(self) -> None:
        if self.current_request is None:
            self.close_after_flush = True

    def close(self, reason: str = "closed") -> None:
        if self.closed:
            return
        self.closed = True
        self.close_reason = reason

        # Take every raw/native owner before invoking cancellation, provider,
        # transport or hook code.  Reentrant close observes the terminal flag;
        # callback mutation cannot replace the handles this invocation owns.
        deferred = self.deferred_events
        self.deferred_events = []
        tls_channel = self.tls_channel
        self.tls_channel = None
        fd = self.fd
        self.fd = -1
        transport = self.transport
        output = self.output
        buffered = len(output)
        lifecycle = self.lifecycle
        hooks = self.hooks

        first_error = None
        for cleanup in (
            self._abort_current_request,
            # close() may be invoked by control code which is not the current
            # connection virtual thread.  Such a caller can wake/cancel the
            # child, but only the structured connection owner may join it and
            # retire request/body admission.  The normal run-finally does so
            # before entering close(); a direct caller gets an honest invariant
            # error and leaves the pending owner available for settlement.
            self._signal_pending_local,
            self._cancel_pending_proxy,
            self._cancel_pending_stream,
        ):
            try:
                cleanup(reason)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self.pending_local_request is not None and first_error is None:
            first_error = GatewayError(
                "pending local handler settlement requires connection owner"
            )
        for event in deferred:
            try:
                if isinstance(event, BodyChunk):
                    event.release()
                elif isinstance(event, RequestHead):
                    lifecycle.release_queued_request()
            except Exception as error:
                if first_error is None:
                    first_error = error
        try:
            output.close()
        except Exception as error:
            if first_error is None:
                first_error = error
        if buffered:
            try:
                lifecycle.release_buffered(buffered)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if tls_channel is not None:
            if reason in ("forced-drain", "carrier-pool-stopped"):
                try:
                    tls_channel.cancel()
                except Exception as error:
                    if first_error is None:
                        first_error = error
            try:
                tls_channel.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if transport is not None and fd >= 0:
            try:
                transport.shutdown(fd)
            except Exception as error:
                if first_error is None:
                    first_error = error
            try:
                transport.close(fd)
            except Exception as error:
                if first_error is None:
                    first_error = error
        try:
            hooks.connection_closed(self, reason)
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error


def _wait_connection_fd(connection, events: int, timeout_ms: int) -> None:
    """Top-level effect edge required by current pcc1 may-park analysis."""
    if connection.transport.native_virtual_threads:
        _park_native_fd(connection.fd, events, timeout_ms)
    else:
        connection.transport.wait(connection.fd, events, timeout_ms)


def _wait_proxy_fd(connection, fd: int, events: int, timeout_ms: int) -> None:
    """Top-level effect edge for an upstream descriptor."""
    if connection.transport.native_virtual_threads:
        _park_native_fd(fd, events, timeout_ms)
    else:
        connection.transport.wait(fd, events, timeout_ms)


def _wait_dns_fd(connection, fd: int, events: int, timeout_ms: int) -> None:
    """Top-level DNS readiness edge with the request's remaining budget."""
    if connection.transport.native_virtual_threads:
        _park_native_fd(fd, events, timeout_ms)
    else:
        connection.transport.wait(fd, events, timeout_ms)


def _proxy_observe_downstream(connection, request) -> str:
    """Consume one nonblocking client observation while an upstream owns it.

    A proxy continuation is also the sole downstream connection owner.  Bytes
    observed here are fed through the normal parser and remain deferred behind
    the pending proxy; EOF publishes cancellation before the upstream timeout.
    """
    outcome, data = connection._read_transport(
        connection.config.buffers.segment_bytes
    )
    if outcome == PCC_SOCKET_PROGRESS:
        if not data:
            request.cancellation.cancel("downstream closed")
            return "cancelled"
        connection.read_deadline_ms = (
            connection.now_ms() + connection.config.http1.idle_timeout_ms
        )
        handled = virtual_thread.call(connection.feed_data, data, 1)
        if handled < 0 or connection.close_after_flush:
            # feed_data/_fail_http has already committed the parser's 4xx and
            # cancelled the current parser alias.  Report that downstream
            # response explicitly so the proxy driver never appends a 502.
            return _PROXY_DOWNSTREAM_RESPONSE_COMMITTED
        if request.cancellation.is_cancelled():
            return "cancelled"
        virtual_thread.call(
            connection._consume_pending_proxy_body_events, request
        )
        return ""
    if outcome == PCC_SOCKET_EOF:
        request.cancellation.cancel("downstream closed")
        return "cancelled"
    if outcome == PCC_SOCKET_WOULD_BLOCK:
        return ""
    request.cancellation.cancel("downstream read failed")
    return "cancelled"


def _proxy_wait_upstream(
    connection,
    fd: int,
    events: int,
    timeout_ms: int,
    request,
) -> str:
    """Park in bounded slices so client EOF can cancel an upstream wait."""
    if timeout_ms <= 0:
        return ""
    wait_ms = timeout_ms
    if wait_ms > _PROXY_CLIENT_OBSERVE_SLICE_MS:
        wait_ms = _PROXY_CLIENT_OBSERVE_SLICE_MS
    _wait_proxy_fd(connection, fd, events, wait_ms)
    return _proxy_observe_downstream(connection, request)


def _proxy_read_downstream_body(connection, request, timeout_ms: int) -> str:
    """Wait for and parse another bounded request-body fragment."""
    virtual_thread.call(connection._consume_pending_proxy_body_events, request)
    if request.body.is_ended():
        return ""
    observation = _proxy_observe_downstream(connection, request)
    if (
        observation
        or request.body.is_ended()
        or request.body.queued_size() > 0
    ):
        return observation
    wait_ms = timeout_ms
    if wait_ms > _PROXY_CLIENT_OBSERVE_SLICE_MS:
        wait_ms = _PROXY_CLIENT_OBSERVE_SLICE_MS
    if wait_ms > 0:
        _wait_connection_fd(connection, connection.io_wait_interest, wait_ms)
    return ""


def _resolve_upstream_address(connection, pool, endpoint, limit: int, request):
    """Resolve one endpoint without borrowing a host resolver or carrier."""
    numeric = normalize_numeric_address(endpoint.host)
    if numeric is not None:
        return (numeric,), ""
    if connection.resolver is None or connection.dns_transport is None:
        return (), "dns"
    now = connection.now_ms()
    if request.cancellation.deadline_ms >= 0:
        limit = _minimum_deadline(limit, request.cancellation.deadline_ms)
    if limit <= now:
        return (), "dns-timeout"
    driver = None
    try:
        connection.hooks.dns_started(connection, endpoint.host)
        connection.lifecycle.metrics.add("dns_queries")
        query_type = DNS_A
        driver = connection.resolver.begin_driver(
            endpoint.host,
            query_type,
            now,
            limit,
            connection.dns_transport,
        )
        while True:
            if request.cancellation.is_cancelled():
                driver.cancel()
                connection.lifecycle.metrics.add("dns_failures")
                connection.hooks.dns_finished(
                    connection, endpoint.host, 0, "cancelled"
                )
                return (), "cancelled"
            now = connection.now_ms()
            result = driver.step(now)
            if result.kind == "complete":
                if not result.values:
                    connection.lifecycle.metrics.add("dns_failures")
                    connection.hooks.dns_finished(
                        connection, endpoint.host, 0, "no-address"
                    )
                    return (), "dns"
                addresses = pool.accept_and_order_addresses(
                    endpoint, result.values, query_type
                )
                if result.source == "cache" or result.source == "hosts":
                    connection.lifecycle.metrics.add("dns_cache_hits")
                connection.hooks.dns_finished(
                    connection, endpoint.host, len(addresses), ""
                )
                return addresses, ""
            if result.kind == "error":
                if query_type == DNS_A and result.error in (
                    "no-address",
                    "cached-negative",
                ):
                    # IPv6-only upstreams are valid.  Reuse the same absolute
                    # connect deadline; the AAAA query may itself complete
                    # through numeric/hosts/cache without transport I/O.
                    query_type = DNS_AAAA
                    driver = connection.resolver.begin_driver(
                        endpoint.host,
                        query_type,
                        now,
                        limit,
                        connection.dns_transport,
                    )
                    continue
                if (
                    result.error == "timeout"
                    or result.error.endswith(":attempt-timeout")
                    or result.error.startswith(
                        "retry-exhausted:attempt-timeout"
                    )
                    or now >= limit
                ):
                    connection.lifecycle.metrics.add("dns_failures")
                    connection.hooks.dns_finished(
                        connection, endpoint.host, 0, "timeout"
                    )
                    return (), "dns-timeout"
                connection.hooks.dns_finished(
                    connection, endpoint.host, 0, result.error
                )
                connection.lifecycle.metrics.add("dns_failures")
                return (), "dns"
            if result.kind == "wait-read" or result.kind == "wait-write":
                wait_limit = _minimum_deadline(result.deadline_ms, limit)
                remaining = wait_limit - now
                if remaining <= 0:
                    driver.cancel()
                    connection.lifecycle.metrics.add("dns_failures")
                    connection.hooks.dns_finished(
                        connection, endpoint.host, 0, "timeout"
                    )
                    return (), "dns-timeout"
                interest = PCC_IO_READ
                if result.interest == DNS_INTEREST_WRITE:
                    interest = PCC_IO_WRITE
                wait_ms = remaining
                if wait_ms > _PROXY_CLIENT_OBSERVE_SLICE_MS:
                    wait_ms = _PROXY_CLIENT_OBSERVE_SLICE_MS
                _wait_dns_fd(connection, result.handle, interest, wait_ms)
                failure = _proxy_observe_downstream(connection, request)
                if failure:
                    driver.cancel()
                    connection.lifecycle.metrics.add("dns_failures")
                    connection.hooks.dns_finished(
                        connection, endpoint.host, 0, "cancelled"
                    )
                    return (), failure
            elif result.kind == "ignored":
                # One invalid datagram was consumed.  Wait for the next
                # readiness edge; immediately observing recv again would let
                # a spoofed packet burst spin a carrier despite the driver's
                # bounded invalid-reply count.
                remaining = driver.attempt_deadline_ms - now
                if remaining <= 0:
                    driver.cancel()
                    connection.lifecycle.metrics.add("dns_failures")
                    connection.hooks.dns_finished(
                        connection, endpoint.host, 0, "timeout"
                    )
                    return (), "dns-timeout"
                wait_ms = remaining
                if wait_ms > _PROXY_CLIENT_OBSERVE_SLICE_MS:
                    wait_ms = _PROXY_CLIENT_OBSERVE_SLICE_MS
                _wait_dns_fd(connection, driver.handle, PCC_IO_READ, wait_ms)
                failure = _proxy_observe_downstream(connection, request)
                if failure:
                    driver.cancel()
                    connection.lifecycle.metrics.add("dns_failures")
                    connection.hooks.dns_finished(
                        connection, endpoint.host, 0, "cancelled"
                    )
                    return (), failure
            # Progress/retry are immediate observations and continue without
            # parking; DnsResolveDriver bounds them by attempt count and the
            # immutable absolute deadline.
    except Exception:
        # The driver is the sole owner of its live UDP/TCP descriptor.  Any
        # hook/policy/adapter exception must still close it before translating
        # the failure into the stable proxy boundary.
        try:
            if driver is not None:
                driver.cancel()
        except Exception:
            pass
        try:
            connection.lifecycle.metrics.add("dns_failures")
        except Exception:
            pass
        try:
            connection.hooks.dns_finished(
                connection, endpoint.host, 0, "resolver-error"
            )
        except Exception:
            pass
        return (), "dns"


def _proxy_target(plan) -> str:
    request = plan.request
    target = request.raw_path
    prefix = plan.spec.strip_prefix
    if prefix:
        if not target.startswith(prefix):
            raise ProxyProtocolError(
                "strip-prefix-mismatch", "proxy strip prefix does not match"
            )
        target = target[len(prefix):]
        if not target:
            target = "/"
        elif not target.startswith("/"):
            target = "/" + target
    if request.query_string:
        target = target + "?" + request.query_string
    return target


def _proxy_authority(endpoint) -> str:
    if ":" in endpoint.host and not endpoint.host.startswith("["):
        return "[" + endpoint.host + "]:" + str(endpoint.port)
    return endpoint.host + ":" + str(endpoint.port)


def _proxy_remaining_ms(connection, deadline, request) -> int:
    now = connection.now_ms()
    limit = deadline.deadline_ms
    request_limit = request.cancellation.deadline_ms
    if request_limit >= 0:
        limit = _minimum_deadline(limit, request_limit)
    if limit < 0:
        return 0
    return limit - now


def _proxy_deadline_failure(connection, deadline, request) -> str:
    if virtual_thread.call(request.cancellation.is_cancelled):
        return "cancelled"
    if _proxy_remaining_ms(connection, deadline, request) <= 0:
        virtual_thread.call(
            request.cancellation.cancel, "proxy deadline exceeded"
        )
        connection.hooks.deadline_exceeded(connection, deadline.stage)
        return deadline.failure()
    return ""


def _proxy_write_queued(connection, fd: int, exchange, deadline, request) -> str:
    """Drain bounded exchange output with partial-write preservation."""
    while len(exchange.to_upstream) > 0:
        data, _resumed = exchange.take_upstream(
            connection.config.buffers.segment_bytes
        )
        offset = 0
        while offset < len(data):
            failure = _proxy_deadline_failure(
                connection, deadline, request
            )
            if failure:
                return failure
            outcome, count = connection.transport.write(fd, data[offset:])
            if outcome == PCC_SOCKET_PROGRESS:
                if count <= 0 or offset + count > len(data):
                    return "reset-before-head"
                offset += count
                continue
            if outcome == PCC_SOCKET_WOULD_BLOCK:
                failure = _proxy_wait_upstream(
                    connection,
                    fd,
                    PCC_IO_WRITE,
                    _proxy_remaining_ms(connection, deadline, request),
                    request,
                )
                if failure:
                    return failure
                continue
            return "reset-before-head"
    return ""


def _proxy_flush_downstream(connection, exchange) -> str:
    """Apply client-side watermarks while the upstream response is live."""
    while len(exchange.to_downstream) > 0:
        data, _resumed = exchange.take_downstream(
            connection.config.buffers.segment_bytes
        )
        virtual_thread.call(connection._queue_bytes, data)
        while len(connection.output) > 0:
            outcome = virtual_thread.call(connection._flush_transport)
            if outcome == _FLUSH_TIMEOUT:
                return "downstream-timeout"
            if outcome == PCC_SOCKET_WOULD_BLOCK:
                _wait_connection_fd(
                    connection,
                    connection.io_wait_interest,
                    connection.write_deadline_ms - connection.now_ms(),
                )
    return ""


def _proxy_exchange_attempt(connection, plan, pool, attempt: int):
    """Run one HTTP/1 upstream attempt; every park is a top-level call edge."""
    request = plan.request
    spec = plan.spec
    deadline = ProxyDeadline(spec.timeouts, connection.now_ms())
    resolution_deadline_ms = deadline.deadline_ms
    if not virtual_thread.call(connection.lifecycle.admit_upstream):
        return 0, "overloaded", False
    upstream_admitted = True
    pooled = None
    exchange = None
    reusable = False
    failure = ""
    primary_error = None
    try:
        saturated = virtual_thread.call(pool.saturated)
        pooled = virtual_thread.call(pool.reserve, connection.now_ms())
        if pooled is None:
            return 0, "overloaded" if saturated else "connect", False
        if pooled.needs_open:
            endpoint = pooled.lease.endpoint
            if connection.resolver is None:
                numeric = normalize_numeric_address(endpoint.host)
                if numeric is None:
                    addresses = (endpoint.host,)
                else:
                    addresses = (numeric,)
            else:
                addresses, failure = _resolve_upstream_address(
                    connection, pool, endpoint, resolution_deadline_ms, request
                )
            if failure:
                return 0, failure, False
            handle = -1
            for address in addresses:
                failure = _proxy_deadline_failure(
                    connection, deadline, request
                )
                if failure:
                    break
                handle = connection.transport.open_upstream(endpoint, address)
                if handle < 0:
                    continue
                # Complete each candidate inside the one immutable connect
                # budget.  A refused first address must not discard the rest
                # of the policy-approved RRset.
                outcome = connection.transport.connect_observe(handle)
                while outcome == PCC_SOCKET_WOULD_BLOCK:
                    failure = _proxy_deadline_failure(
                        connection, deadline, request
                    )
                    if failure:
                        break
                    failure = _proxy_wait_upstream(
                        connection,
                        handle,
                        PCC_IO_WRITE,
                        _proxy_remaining_ms(connection, deadline, request),
                        request,
                    )
                    if failure:
                        break
                    outcome = connection.transport.connect_observe(handle)
                if not failure and outcome == PCC_SOCKET_CONNECTED:
                    break
                connection.transport.close(handle)
                handle = -1
                if failure:
                    break
            if failure:
                return 0, failure, False
            pooled = virtual_thread.call(pool.opened, pooled, handle)
            if pooled is None:
                # pool.opened already releases the failed reservation.
                return 0, "connect", False
            pooled.connected = True
        outcome = PCC_SOCKET_CONNECTED
        if not pooled.connected:
            # DNS time is part of the connect budget.  Do not reset the
            # absolute connect deadline after resolution.
            deadline.deadline_ms = resolution_deadline_ms
            outcome = connection.transport.connect_observe(pooled.handle)
            while outcome == PCC_SOCKET_WOULD_BLOCK:
                failure = _proxy_deadline_failure(
                    connection, deadline, request
                )
                if failure:
                    break
                failure = _proxy_wait_upstream(
                    connection,
                    pooled.handle,
                    PCC_IO_WRITE,
                    _proxy_remaining_ms(connection, deadline, request),
                    request,
                )
                if failure:
                    break
                outcome = connection.transport.connect_observe(pooled.handle)
            if not failure and outcome != PCC_SOCKET_CONNECTED:
                failure = "connect"
        if failure:
            return 0, failure, False

        pooled.connected = True
        # Connecting does not start the upstream response-header clock.  A slow
        # client upload is governed by the request's immutable body deadline;
        # the header budget begins only once every request byte is upstream.
        deadline.request_body(request.cancellation.deadline_ms)
        endpoint = pooled.lease.endpoint
        exchange = ProxyExchange(
            request.method,
            _proxy_target(plan),
            request.headers,
            _proxy_authority(endpoint),
            request.client_ip,
            request.scheme,
            request.header("host", ""),
            content_length=request.content_length,
            chunked_request=request.chunked_body,
            expect_continue_handled=request.expect_continue_handled,
            trust_forwarded=spec.trust_forwarded,
            segment_bytes=connection.config.buffers.segment_bytes,
            low_watermark=connection.config.buffers.low_watermark,
            high_watermark=connection.config.buffers.high_watermark,
            max_buffered_bytes=connection.config.buffers.connection_bytes,
            accounting=connection.lifecycle,
        )
        failure = _proxy_write_queued(
            connection, pooled.handle, exchange, deadline, request
        )
        if failure:
            return 0, failure, exchange.response_committed

        # Interleave downstream parsing and upstream writes.  ``BodyStream``
        # holds only parser fragments not yet consumed; read_chunk releases
        # each retained BufferView as soon as ownership moves to the exchange.
        while not virtual_thread.call(request.body.is_ended):
            virtual_thread.call(
                connection._consume_pending_proxy_body_events, request
            )
            chunk = virtual_thread.call(request.body.read_chunk)
            while chunk is not None and chunk != b"":
                exchange.feed_request_body(chunk)
                failure = _proxy_write_queued(
                    connection, pooled.handle, exchange, deadline, request
                )
                if failure:
                    return 0, failure, exchange.response_committed
                chunk = virtual_thread.call(request.body.read_chunk)
            if virtual_thread.call(request.body.is_ended):
                break
            failure = _proxy_deadline_failure(connection, deadline, request)
            if failure:
                return 0, failure, exchange.response_committed
            failure = _proxy_read_downstream_body(
                connection,
                request,
                _proxy_remaining_ms(connection, deadline, request),
            )
            if failure:
                return 0, failure, exchange.response_committed
        # Drain any final fragment that arrived in the same parse batch as
        # RequestEnd before emitting the upstream end marker.
        chunk = virtual_thread.call(request.body.read_chunk)
        while chunk is not None and chunk != b"":
            exchange.feed_request_body(chunk)
            failure = _proxy_write_queued(
                connection, pooled.handle, exchange, deadline, request
            )
            if failure:
                return 0, failure, exchange.response_committed
            chunk = virtual_thread.call(request.body.read_chunk)
        exchange.finish_request(request.trailers)
        failure = _proxy_write_queued(
            connection, pooled.handle, exchange, deadline, request
        )
        if failure:
            return 0, failure, exchange.response_committed

        deadline.connected(connection.now_ms())

        while not exchange.response_finished:
            failure = _proxy_deadline_failure(
                connection, deadline, request
            )
            if failure:
                return exchange.response_status, failure, exchange.response_committed
            outcome, data = connection.transport.read(
                pooled.handle, connection.config.buffers.segment_bytes
            )
            if outcome == PCC_SOCKET_PROGRESS:
                if not data:
                    return exchange.response_status, "reset-before-head", exchange.response_committed
                was_committed = exchange.response_committed
                exchange.feed_upstream(data)
                now = connection.now_ms()
                if not was_committed and exchange.response_committed:
                    deadline.response_head(now, not exchange.response_finished)
                elif exchange.response_committed and not exchange.response_finished:
                    deadline.body_progress(now)
                failure = _proxy_flush_downstream(connection, exchange)
                if failure:
                    return exchange.response_status, failure, True
                continue
            if outcome == PCC_SOCKET_EOF:
                exchange.upstream_eof()
                failure = _proxy_flush_downstream(connection, exchange)
                if failure:
                    return exchange.response_status, failure, True
                break
            if outcome == PCC_SOCKET_WOULD_BLOCK:
                failure = _proxy_wait_upstream(
                    connection,
                    pooled.handle,
                    PCC_IO_READ,
                    _proxy_remaining_ms(connection, deadline, request),
                    request,
                )
                if failure:
                    return exchange.response_status, failure, exchange.response_committed
                continue
            return exchange.response_status, "reset-before-head", exchange.response_committed

        deadline.finish()
        reusable = exchange.upstream_keep_alive and exchange.response_finished
        return exchange.response_status, "", True
    except ProxyProtocolError as error:
        committed = exchange is not None and exchange.response_committed
        status = 0 if exchange is None else exchange.response_status
        if error.code == "buffer-overload":
            return status, "overloaded", committed
        return status, "protocol", committed
    except Exception as error:
        primary_error = error
        raise
    finally:
        cleanup_error = None
        if exchange is not None:
            try:
                exchange.cancel("attempt complete")
            except Exception as error:
                cleanup_error = error
        try:
            if pooled is not None and not pooled.released:
                virtual_thread.call(
                    pool.release, pooled, connection.now_ms(), reusable
                )
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        if upstream_admitted:
            try:
                virtual_thread.call(connection.lifecycle.release_upstream)
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None and primary_error is None:
            raise cleanup_error


def _run_gateway_proxy(connection) -> None:
    """Drive a pending proxy plan and translate pre-commit failures."""
    from pcc.gateway.models import Response

    plan = connection.pending_proxy
    request = connection.pending_proxy_request
    if plan is None or request is None:
        return
    status = 0
    failure = "no-endpoint"
    committed = False
    response = None
    primary_error = None
    try:
        pool = connection.proxy_pools.get(plan.upstream.name)
        if pool is not None:
            attempt = 1
            while attempt <= plan.spec.retry.attempts:
                status, failure, committed = _proxy_exchange_attempt(
                    connection, plan, pool, attempt
                )
                if failure == _PROXY_DOWNSTREAM_RESPONSE_COMMITTED:
                    committed = True
                if not failure:
                    break
                retry_failure = failure
                if failure == "header-timeout":
                    retry_failure = "timeout-before-head"
                if not plan.spec.retry.allows(
                    request.method,
                    attempt,
                    committed,
                    retry_failure,
                    virtual_thread.call(request.body.consumed_size) == 0,
                ):
                    break
                virtual_thread.call(
                    connection.lifecycle.metrics.add, "upstream_retries"
                )
                attempt += 1

        if failure == _PROXY_DOWNSTREAM_RESPONSE_COMMITTED:
            # The normal HTTP parser already queued its precise 4xx.  This
            # continuation owns cleanup only; it must not synthesize a proxy
            # response or report a fictitious 502 through request hooks.
            connection.close_after_flush = True
        elif failure:
            if failure == "cancelled":
                virtual_thread.call(
                    connection.lifecycle.metrics.add, "upstream_cancelled"
                )
            if not committed:
                status = proxy_failure_status(failure)
                response = Response.text("upstream " + failure, status)
                virtual_thread.call(
                    connection._queue_bytes,
                    virtual_thread.call(
                        connection._response_payload, request, response
                    ),
                )
            else:
                connection.close_after_flush = True
                response = Response(status if status else 502, b"")
        else:
            response = Response(status, b"")

        if response is not None:
            response.committed = True
            connection.hooks.request_finished(
                connection, request, response
            )
    except Exception as error:
        primary_error = error
        raise
    finally:
        # Take the pending request exactly once before invoking body/hooks/
        # lifecycle callbacks.  An early proxy still aliases current_request
        # until RequestEnd; clear that alias and its admission marker without
        # performing a second release.
        owns_pending = connection.pending_proxy_request is request
        if owns_pending:
            connection.pending_proxy = None
            connection.pending_proxy_request = None
            current_alias = connection.current_request is request
            if current_alias:
                connection.current_request = None
                connection.current_admitted = False

            cleanup_error = None
            try:
                body_ended = virtual_thread.call(request.body.is_ended)
            except Exception as error:
                cleanup_error = error
                body_ended = False
            if not body_ended:
                connection.close_after_flush = True
                try:
                    already_cancelled = virtual_thread.call(
                        request.cancellation.is_cancelled
                    )
                except Exception as error:
                    if cleanup_error is None:
                        cleanup_error = error
                    already_cancelled = False
                if not already_cancelled:
                    try:
                        virtual_thread.call(
                            request.cancellation.cancel,
                            "proxy completed before request body end",
                        )
                    except Exception as error:
                        if cleanup_error is None:
                            cleanup_error = error
                try:
                    virtual_thread.call(request.body.cancel)
                except Exception as error:
                    if cleanup_error is None:
                        cleanup_error = error
            try:
                virtual_thread.call(request.body.close)
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            try:
                virtual_thread.call(connection.lifecycle.release_request)
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            try:
                connection.requests_completed += 1
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            try:
                if (
                    not connection.current_keep_alive
                    or connection.requests_completed
                    >= connection.config.max_requests_per_connection
                    or connection.lifecycle.state != STATE_RUNNING
                ):
                    connection.close_after_flush = True
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            if cleanup_error is not None and primary_error is None:
                raise cleanup_error


def _run_tls_handshake(connection) -> bool:
    """Drive one server handshake; only this top-level edge may park."""

    channel = connection.tls_channel
    if channel is None:
        return True
    deadline = connection.now_ms() + connection.config.tls_handshake_timeout_ms
    connection.tls_handshake_deadline_ms = deadline
    virtual_thread.call(
        connection.lifecycle.metrics.add, "tls_handshakes_started"
    )
    connection.hooks.tls_handshake_started(connection, channel)
    while True:
        if connection.closed or connection.lifecycle.state != STATE_RUNNING:
            result = virtual_thread.call(channel.cancel)
        else:
            now = connection.now_ms()
            result = virtual_thread.call(channel.handshake, now, deadline)
        if result.status == TLS_OK:
            if channel.alpn not in ("", "http/1.1"):
                virtual_thread.call(
                    connection.lifecycle.metrics.add,
                    "tls_handshakes_failed",
                )
                connection.hooks.tls_handshake_failed(connection, "alpn")
                raise GatewayTransportError("TLS selected unsupported ALPN")
            virtual_thread.call(
                connection.lifecycle.metrics.add,
                "tls_handshakes_completed",
            )
            connection.hooks.tls_handshake_completed(connection, channel)
            return True
        if result.status == TLS_SELECT_SNI:
            # Context installation is synchronous; immediately re-enter the
            # provider before yielding the carrier.
            continue
        if result.status in (TLS_WANT_READ, TLS_WANT_WRITE):
            now = connection.now_ms()
            remaining = deadline - now
            if remaining <= 0:
                result = virtual_thread.call(
                    channel.handshake, now, deadline
                )
                virtual_thread.call(
                    connection.lifecycle.metrics.add,
                    "tls_handshakes_failed",
                )
                connection.hooks.tls_handshake_failed(
                    connection, result.error_name
                )
                return False
            interest = (
                PCC_IO_READ
                if result.wait_interest == TLS_INTEREST_READ
                else PCC_IO_WRITE
            )
            _wait_connection_fd(connection, interest, remaining)
            continue
        if result.status == TLS_CLOSED:
            virtual_thread.call(
                connection.lifecycle.metrics.add, "tls_handshakes_failed"
            )
            connection.hooks.tls_handshake_failed(connection, "peer-closed")
            return False
        virtual_thread.call(
            connection.lifecycle.metrics.add, "tls_handshakes_failed"
        )
        connection.hooks.tls_handshake_failed(connection, result.error_name)
        return False


def _run_tls_close_notify(connection) -> None:
    """Best-effort bounded TLS shutdown before the socket is released."""

    channel = connection.tls_channel
    if (
        channel is None
        or not channel.handshake_complete
        or channel.failed
        or channel.released
    ):
        return
    deadline = connection.now_ms() + connection.config.tls_close_timeout_ms
    while True:
        now = connection.now_ms()
        result = virtual_thread.call(channel.close_notify, now, deadline)
        if result.status == TLS_CLOSED:
            virtual_thread.call(
                connection.lifecycle.metrics.add,
                "tls_close_notify_completed",
            )
            connection.hooks.tls_closed(connection, True)
            return
        if result.status in (TLS_WANT_READ, TLS_WANT_WRITE):
            remaining = deadline - now
            if remaining <= 0:
                virtual_thread.call(
                    connection.lifecycle.metrics.add,
                    "tls_close_notify_failed",
                )
                connection.hooks.tls_closed(connection, False)
                return
            interest = (
                PCC_IO_READ
                if result.wait_interest == TLS_INTEREST_READ
                else PCC_IO_WRITE
            )
            _wait_connection_fd(connection, interest, remaining)
            continue
        virtual_thread.call(
            connection.lifecycle.metrics.add, "tls_close_notify_failed"
        )
        connection.hooks.tls_closed(connection, False)
        return


def _run_gateway_connection(connection) -> None:
    """Resumable top-level connection loop used by the native spawn site."""
    reason = "closed"
    primary_error = None
    try:
        if connection.transport is None or connection.fd < 0:
            raise GatewayTransportError("connection has no live transport")
        connection.hooks.connection_opened(connection)
        connection.read_deadline_ms = (
            connection.now_ms() + connection.config.http1.idle_timeout_ms
        )
        if not _run_tls_handshake(connection):
            reason = "tls-handshake-failed"
            return
        while not connection.closed:
            if len(connection.output) > 0:
                flush = virtual_thread.call(connection._flush_transport)
                if flush == _FLUSH_TIMEOUT:
                    reason = "write-timeout"
                    break
                if flush == PCC_SOCKET_WOULD_BLOCK:
                    now = connection.now_ms()
                    _wait_connection_fd(
                        connection,
                        connection.io_wait_interest,
                        connection.write_deadline_ms - now,
                    )
                continue
            if connection.pending_proxy is not None:
                _run_gateway_proxy(connection)
                continue
            if connection.pending_local_request is not None:
                if virtual_thread.call(connection._drive_pending_local):
                    continue
                request = connection.pending_local_request
                if request is not None:
                    # The handler is the sole body reader.  Once its queue is
                    # high, wait_writable parks this connection owner until
                    # consumption crosses low; otherwise it returns at once.
                    # The method observes state under BodyStream's lock, so no
                    # carrier reads the flag concurrently without ownership.
                    now = connection.now_ms()
                    remaining = request.cancellation.deadline_ms - now
                    if remaining <= 0:
                        virtual_thread.call(
                            connection._deadline_timeout, "request"
                        )
                        continue
                    writable = virtual_thread.call(
                        request.body.wait_writable, remaining
                    )
                    if not writable:
                        if connection.now_ms() >= (
                            request.cancellation.deadline_ms
                        ):
                            virtual_thread.call(
                                connection._deadline_timeout, "request"
                            )
                            continue
                        # finish/cancel woke the waiter.  Give the handler a
                        # scheduling edge; the next drive either observes its
                        # terminal outcome or joins it because the producer is
                        # closed.
                        virtual_thread.yield_now()
                        continue
            if (
                connection.pending_local_request is None
                and connection.pending_stream_request is not None
            ):
                virtual_thread.call(connection._drive_streaming_response)
                continue
            if (
                connection.pending_local_request is None
                and connection.deferred_events
            ):
                virtual_thread.call(connection._resume_deferred_events)
                continue
            if connection.close_after_flush:
                reason = "response-complete"
                break
            if (
                connection.lifecycle.state != STATE_RUNNING
                and connection.current_request is None
            ):
                reason = "drain"
                break

            # The live transport decodes one complete request at a time. This
            # preserves response order and keeps a malformed later pipeline
            # member from erasing events decoded for its predecessor. A partial
            # buffered request returns zero and falls through to the next read.
            if len(connection.codec.buffer) > 0:
                buffered_before = len(connection.codec.buffer)
                buffered = virtual_thread.call(connection.feed_data, b"", 1)
                if connection.pending_proxy_request is not None:
                    virtual_thread.call(
                        connection._consume_pending_proxy_body_events,
                        connection.pending_proxy_request,
                    )
                if connection.pending_local_request is not None:
                    virtual_thread.call(
                        connection._consume_pending_local_body_events,
                        connection.pending_local_request,
                    )
                if (
                    buffered != 0
                    or connection.deferred_events
                    or len(connection.codec.buffer) < buffered_before
                ):
                    continue

            outcome, data = virtual_thread.call(
                connection._read_transport,
                connection.config.buffers.segment_bytes,
            )
            if outcome == PCC_SOCKET_PROGRESS:
                if not data:
                    raise GatewayTransportError("empty socket read progress")
                connection.read_deadline_ms = (
                    connection.now_ms()
                    + connection.config.http1.idle_timeout_ms
                )
                virtual_thread.call(connection.feed_data, data, 1)
                if connection.pending_local_request is not None:
                    virtual_thread.call(
                        connection._consume_pending_local_body_events,
                        connection.pending_local_request,
                    )
                continue
            if outcome == PCC_SOCKET_EOF:
                virtual_thread.call(connection.input_eof)
                reason = "eof"
                if len(connection.output) == 0:
                    break
                continue
            if outcome == PCC_SOCKET_WOULD_BLOCK:
                now = connection.now_ms()
                deadline = connection.read_deadline_ms
                phase = "read"
                if connection.current_request is not None:
                    deadline = _minimum_deadline(
                        deadline,
                        connection.current_request.cancellation.deadline_ms,
                    )
                    phase = "request"
                elif connection.header_deadline_ms >= 0:
                    deadline = _minimum_deadline(
                        deadline, connection.header_deadline_ms
                    )
                    phase = "header"
                if now >= deadline:
                    virtual_thread.call(connection._deadline_timeout, phase)
                    reason = phase + "-timeout"
                    continue
                _wait_connection_fd(
                    connection, connection.io_wait_interest, deadline - now
                )
                continue
            raise GatewayTransportError("socket read observation failed")
    except Exception as error:
        reason = "transport-error"
        primary_error = error
    finally:
        local_cleanup_error = None
        close_notify_error = None
        close_error = None
        if connection.pending_local_request is not None:
            try:
                virtual_thread.call(
                    connection._cancel_and_join_pending_local, reason
                )
            except Exception as error:
                local_cleanup_error = error
        if not connection.closed:
            try:
                _run_tls_close_notify(connection)
            except Exception as error:
                close_notify_error = error
            try:
                virtual_thread.call(connection.close, reason)
            except Exception as error:
                close_error = error
        if primary_error is None:
            if local_cleanup_error is not None:
                raise local_cleanup_error
            if close_notify_error is not None:
                raise close_notify_error
            if close_error is not None:
                raise close_error
    if primary_error is not None:
        raise primary_error


class GatewayServer:
    """Listener owner used by :meth:`pcc.web.App.run`."""

    def __init__(
        self,
        app,
        host: str = "0.0.0.0",
        port: int = 8080,
        carrier_count: int = 0,
        config=None,
        transport=None,
        scheduler=None,
        hooks=None,
        tls_registry=None,
        resolver=None,
        dns_transport=None,
        process_control=None,
        reload_factory=None,
    ) -> None:
        if port <= 0 or port > 65535:
            raise ValueError("gateway port is out of range")
        if carrier_count < 0 or carrier_count > 64:
            raise ValueError("carrier count must be between 0 and 64")
        if config is None:
            config = GatewayConfig(
                listeners=(ListenerConfig(host, port),),
                carrier_count=carrier_count,
            )
        if not isinstance(config, GatewayConfig):
            raise TypeError("gateway config must be GatewayConfig")
        if len(config.listeners) != 1:
            raise UnsupportedGatewayFeature(
                "GatewayServer currently owns exactly one listener"
            )
        listener = config.listeners[0]
        # The virtual-thread runtime owns waitset selection process-wide.  An
        # explicit gateway backend is enforced by the runtime's frozen
        # ``PCC_VTHREAD_IO_BACKEND`` setting; it is never emulated inside the
        # HTTP layer.  ``auto`` selects the compiled platform backend.
        if config.waitset_backend not in ("auto", "poll", "kqueue", "epoll"):
            raise UnsupportedGatewayFeature("unknown gateway waitset backend")
        if config.buffers.segment_bytes > 65536:
            raise ValueError("native gateway socket chunks are bounded at 64 KiB")
        if transport is None:
            transport = NativeSocketTransport()
        if scheduler is None:
            scheduler = NativeVirtualThreadScheduler()
        if hooks is None:
            hooks = GatewayHooks()
        self.app = app
        self.listener = listener
        self.host = listener.host
        self.port = listener.port
        configured_carriers = config.carrier_count
        if carrier_count > 0:
            configured_carriers = carrier_count
        self.carrier_count = configured_carriers if configured_carriers > 0 else 1
        self.config = config
        self.transport = transport
        if dns_transport is None and type(transport) is NativeSocketTransport:
            dns_transport = NativeDnsTransport()
        if resolver is None and dns_transport is not None:
            resolver = LazySystemResolver(dns_transport)
        self.resolver = resolver
        self.dns_transport = dns_transport
        self.scheduler = scheduler
        self.hooks = hooks
        if process_control is None:
            process_control = GatewayProcessControl()
        self.process_control = process_control
        self.process_control_installed = False
        self.reload_factory = reload_factory
        self.lifecycle = GatewayLifecycle(config, config.admission)
        self.tls_registry = tls_registry
        self.tls_manager = None
        if listener.tls_provider:
            if not isinstance(listener.tls_config, TlsConfig):
                raise TlsProviderError("TLS listener configuration is invalid")
            if listener.tls_config.alpn != ("http/1.1",):
                raise TlsProviderError(
                    "HTTP/1 gateway TLS listener must offer only http/1.1 ALPN"
                )
            if self.tls_registry is None:
                if listener.tls_provider != PCC_NATIVE_TLS_PROVIDER_NAME:
                    raise TlsProviderError(
                        "non-native TLS provider requires an explicit registry"
                    )
                self.tls_registry = production_tls_registry(
                    listener.tls_provider_library,
                    listener.tls_provider_library_sha256,
                    listener.tls_provider_max_bytes,
                )
            self.tls_manager = TlsGenerationManager(
                self.tls_registry,
                listener.tls_provider,
                listener.tls_config,
                require_production=True,
            )
            provider_info = self.tls_manager.provider_info
            declared_artifact_mismatch = False
            if listener.tls_provider_library:
                declared_artifact_mismatch = (
                    provider_info.library_path
                    != listener.tls_provider_library
                    or provider_info.expected_library_sha256
                    != listener.tls_provider_library_sha256
                    or provider_info.verified_library_sha256
                    != listener.tls_provider_library_sha256
                    or provider_info.library_max_bytes
                    != listener.tls_provider_max_bytes
                )
            if (
                provider_info.native_abi != PCC_NATIVE_TLS_ABI_NAME
                or "no-libpython" not in provider_info.link_boundary
                or not provider_info.implementation_id
                or declared_artifact_mismatch
            ):
                self.tls_manager.close()
                self.tls_manager = None
                raise TlsProviderError(
                    "TLS listener provider lacks reviewed native/no-libpython provenance"
                )
            self.lifecycle.current.attach_resource(self.tls_manager.active)
        self.proxy_pools = {}
        for name, group in app.upstreams.items():
            self.proxy_pools[name] = UpstreamConnectionPool(
                group,
                close_connection=self.transport.close,
            )
        self.listener_fd = -1
        self.accept_thread = None
        self.connections = []
        # A connection leaves ``connections`` from inside its finally block,
        # before the scheduler publishes the surrounding vthread as terminal.
        # Keep the task handle independently until outcome proves that no
        # queued continuation can survive a carrier-pool stop/restart.
        self.connection_owners = []
        self.connections_lock = threading.Lock()
        self.generation_lock = threading.Lock()
        self.pool_started = False
        self.app_started = False
        self.drain_started_ms = -1

    def start(self) -> None:
        if self.lifecycle.state != 0:
            raise GatewayError("gateway server can start only once")
        self.lifecycle.start()
        try:
            if self.config.install_signal_handlers:
                self.process_control.install()
                self.process_control_installed = True
            self.app.startup()
            self.app_started = True
            if isinstance(self.resolver, LazySystemResolver):
                # Read immutable resolver/hosts configuration before accepting
                # work.  Each connection later gets independent cache,
                # rebinding and query-id state from this snapshot.
                self.resolver.prepare()
            self.listener_fd = self.transport.listen(
                self.host,
                self.port,
                self.listener.reuse_port,
                self.listener.backlog,
            )
            if self.listener_fd < 0:
                raise GatewayTransportError("gateway listen failed")
            self.lifecycle.started()
            started = self.scheduler.start(self.carrier_count)
            if started <= 0:
                raise GatewayError("virtual-thread carrier pool did not start")
            self.pool_started = True
            expected_backend = {
                "poll": 0,
                "kqueue": 1,
                "epoll": 2,
            }.get(self.config.waitset_backend, -1)
            if (
                expected_backend >= 0
                and type(self.scheduler) is NativeVirtualThreadScheduler
                and virtual_thread.io_backend() != expected_backend
            ):
                raise UnsupportedGatewayFeature(
                    "requested gateway waitset backend is unavailable"
                )
            self.accept_thread = self.scheduler.spawn_accept(self)
        except Exception as error:
            self.lifecycle.fail(str(error))
            cleanup_error = self._rollback_start()
            if cleanup_error is not None:
                raise cleanup_error from error
            raise

    def accept_once(self, listener_fd: int = -1) -> int:
        if listener_fd < 0:
            listener_fd = self.listener_fd
        if (
            listener_fd < 0
            or listener_fd != self.listener_fd
            or self.lifecycle.state != STATE_RUNNING
        ):
            return 0
        outcome, fd, client_ip = self.transport.accept(listener_fd)
        if outcome == PCC_SOCKET_WOULD_BLOCK:
            return 0
        if outcome != PCC_SOCKET_PROGRESS or fd < 0:
            raise GatewayTransportError("socket accept observation failed")
        if not self.lifecycle.admit_connection():
            self.transport.close(fd)
            return -1
        self.generation_lock.acquire()
        generation = None
        tls_channel = None
        try:
            generation = self.lifecycle.acquire_generation()
            connection_resolver = self.resolver
            if isinstance(connection_resolver, LazySystemResolver):
                connection_resolver = connection_resolver.fork()
            connection_dns_transport = self.dns_transport
            if isinstance(connection_dns_transport, NativeDnsTransport):
                # Descriptor provenance is connection-owned.  Sharing the
                # adapter's handle table across carrier threads would make an
                # unrelated connection part of the DNS trust boundary.
                connection_dns_transport = connection_dns_transport.fork()
            if self.tls_manager is not None:
                tls_generation = generation.resources[0]
                tls_channel = self.tls_manager.new_channel(fd, tls_generation)
            connection = GatewayConnection(
                self.app,
                fd,
                self.transport,
                self.lifecycle,
                generation,
                generation.config,
                self.hooks,
                client_ip,
                self.proxy_pools,
                tls_channel,
                connection_resolver,
                connection_dns_transport,
            )
        except Exception:
            # Construction has not published the connection, so this block
            # owns every partially acquired resource.  Provider callbacks are
            # independent failure boundaries: one broken close must never
            # strand the generation, admission counter, or accepted fd.
            if tls_channel is not None:
                try:
                    tls_channel.close()
                except Exception:
                    pass
            if generation is not None:
                try:
                    generation.release()
                except Exception:
                    pass
            try:
                self.lifecycle.release_connection()
            except Exception:
                pass
            try:
                self.transport.close(fd)
            except Exception:
                pass
            raise
        finally:
            self.generation_lock.release()
        self.connections_lock.acquire()
        try:
            self.connections.append(connection)
        finally:
            self.connections_lock.release()
        spawn_error = None
        try:
            # Never invoke the scheduler while holding a gateway state lock.
            # The accept vthread cannot become terminal until this method
            # returns, so shutdown's accept-owner barrier protects the small
            # interval before the separate task ledger is published.
            connection.vthread = self.scheduler.spawn_connection(
                self, connection
            )
        except Exception as error:
            spawn_error = error
        if spawn_error is None:
            self.connections_lock.acquire()
            try:
                self.connection_owners.append(connection)
            finally:
                self.connections_lock.release()
        else:
            self.connections_lock.acquire()
            try:
                if connection in self.connections:
                    self.connections.remove(connection)
            finally:
                self.connections_lock.release()
        if spawn_error is not None:
            # Preserve the spawn error while releasing every independently
            # owned resource.  One failing provider callback must not strand
            # the admission counter or socket owner.
            try:
                connection.close("spawn-failed")
            except Exception:
                pass
            try:
                generation.release()
            except Exception:
                pass
            try:
                self.lifecycle.release_connection()
            except Exception:
                pass
            raise spawn_error
        return 1

    def _accept_loop(self) -> None:
        _gateway_accept_entry(self)

    def _connection_finished(self, connection) -> None:
        removed = False
        self.connections_lock.acquire()
        try:
            if connection in self.connections:
                self.connections.remove(connection)
                removed = True
        finally:
            self.connections_lock.release()
        if not removed:
            return
        first_error = None
        try:
            connection.generation.release()
        except Exception as error:
            first_error = error
        try:
            self.lifecycle.release_connection()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            self.lifecycle.collect_retired()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def _serve_connection(self, connection) -> None:
        try:
            virtual_thread.call(connection.run)
        finally:
            self._connection_finished(connection)

    def request_stop(self) -> None:
        if self.lifecycle.state == STATE_RUNNING:
            self.lifecycle.begin_drain()
            self.drain_started_ms = self.transport.now_ms()
            # Keep the numeric descriptor owned until the accept continuation
            # has been cancelled and unregistered from the waitset.  Closing it
            # here permits immediate fd reuse while the parked owner still
            # names the old integer.

    def _connection_snapshot(self):
        self.connections_lock.acquire()
        try:
            return list(self.connections)
        finally:
            self.connections_lock.release()

    def _connection_owner_snapshot(self):
        self.connections_lock.acquire()
        try:
            return list(self.connection_owners)
        finally:
            self.connections_lock.release()

    def _reap_connection_owners(self):
        """Retire only scheduler-proven terminal connection task handles."""

        terminal = []
        first_error = None
        for connection in self._connection_owner_snapshot():
            thread = connection.vthread
            if thread is None:
                if first_error is None:
                    first_error = GatewayError(
                        "gateway connection owner lost its virtual-thread handle"
                    )
                continue
            try:
                outcome = self.scheduler.outcome(thread)
            except Exception as error:
                if first_error is None:
                    first_error = error
                continue
            if outcome == virtual_thread.OUTCOME_PENDING:
                continue
            if outcome not in (
                virtual_thread.OUTCOME_RETURNED,
                virtual_thread.OUTCOME_RAISED,
                virtual_thread.OUTCOME_CANCELLED,
            ):
                if first_error is None:
                    first_error = GatewayError(
                        "gateway connection owner reported unknown outcome "
                        + str(outcome)
                    )
                continue
            terminal.append(connection)

        if terminal:
            self.connections_lock.acquire()
            try:
                for connection in terminal:
                    if connection in self.connection_owners:
                        self.connection_owners.remove(connection)
                        connection.vthread = None
            finally:
                self.connections_lock.release()
        return len(self._connection_owner_snapshot()) == 0, first_error

    def reload(self, config: GatewayConfig):
        """Publish a same-listener generation, including TLS certificates."""

        if self.lifecycle.state != STATE_RUNNING:
            raise GatewayError("gateway reload requires running state")
        if not isinstance(config, GatewayConfig) or len(config.listeners) != 1:
            raise GatewayError("gateway reload requires one listener")
        listener = config.listeners[0]
        if (
            listener.host != self.listener.host
            or listener.port != self.listener.port
            or listener.backlog != self.listener.backlog
            or listener.reuse_port != self.listener.reuse_port
            or listener.tls_provider != self.listener.tls_provider
            or listener.tls_provider_library
            != self.listener.tls_provider_library
            or listener.tls_provider_library_sha256
            != self.listener.tls_provider_library_sha256
            or listener.tls_provider_max_bytes
            != self.listener.tls_provider_max_bytes
        ):
            raise UnsupportedGatewayFeature(
                "gateway reload cannot replace listener identity or TLS provider"
            )
        if (
            config.carrier_count != self.config.carrier_count
            or config.waitset_backend != self.config.waitset_backend
            or config.install_signal_handlers
            != self.config.install_signal_handlers
        ):
            raise UnsupportedGatewayFeature(
                "gateway reload cannot replace carrier, waitset or signal ownership"
            )
        old_admission = self.config.admission
        new_admission = config.admission
        if (
            new_admission.max_connections != old_admission.max_connections
            or new_admission.max_requests != old_admission.max_requests
            or new_admission.max_queued_requests
            != old_admission.max_queued_requests
            or new_admission.max_upstream_active
            != old_admission.max_upstream_active
            or new_admission.max_buffered_bytes
            != old_admission.max_buffered_bytes
        ):
            raise UnsupportedGatewayFeature(
                "gateway reload cannot replace live admission limits"
            )
        self.generation_lock.acquire()
        try:
            if self.tls_manager is not None:
                if not isinstance(listener.tls_config, TlsConfig):
                    raise TlsProviderError("TLS reload configuration is invalid")
                if listener.tls_config.alpn != ("http/1.1",):
                    raise TlsProviderError(
                        "HTTP/1 gateway TLS listener must offer only http/1.1 ALPN"
                    )
                tls_generation = self.tls_manager.reload(listener.tls_config)
                self.lifecycle.metrics.add("tls_generation_reloads")
                generation = self.lifecycle.publish(config, (tls_generation,))
            else:
                generation = self.lifecycle.publish(config)
            self.config = config
            self.listener = listener
            return generation
        finally:
            self.generation_lock.release()

    def _close_listener(self) -> None:
        if self.listener_fd >= 0:
            listener_fd = self.listener_fd
            self.listener_fd = -1
            self.transport.close(listener_fd)

    def _rollback_start(self):
        """Attempt every reverse-order startup cleanup, preserving primary error."""
        first_error = None
        if self.pool_started:
            # The carrier pool owns execution but the server owns the accept
            # continuation.  Retire that logical owner before stopping its
            # carriers, including partially published startup failures.
            accept_error = self._stop_accept_owner()
            if accept_error is not None:
                first_error = accept_error
            if self.accept_thread is not None:
                # A parked accept continuation still owns a waitset root and
                # the numeric listener descriptor. Preserve the complete
                # startup state so an embedding owner can retry shutdown (or
                # terminate the worker process) without fd reuse corruption.
                return first_error or GatewayError(
                    "gateway startup rollback retained accept owner"
                )
            try:
                stopped = self.scheduler.stop()
            except Exception as error:
                return first_error or error
            if isinstance(stopped, int) and stopped < 0:
                return first_error or GatewayError(
                    "virtual-thread carrier pool did not stop during startup rollback"
                )
            self.pool_started = False
        try:
            self._close_listener()
        except Exception as error:
            if first_error is None:
                first_error = error
        if self.app_started:
            try:
                self.app.shutdown()
            except Exception as error:
                if first_error is None:
                    first_error = error
            self.app_started = False
        current = self.lifecycle.current
        if not current.retired:
            try:
                current.retire()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self.tls_manager is not None:
            try:
                self.tls_manager.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self.process_control_installed:
            try:
                self.process_control.uninstall()
            except Exception as error:
                # The control wrapper deliberately keeps its owner capability
                # on failed restoration, so retain the installed marker too.
                if first_error is None:
                    first_error = error
            else:
                self.process_control_installed = False
        return first_error

    def _force_close_connections(self) -> None:
        """Wake connection owners; never cancel/close them concurrently."""
        forced = 0
        for connection in self._connection_snapshot():
            if not connection.closed:
                try:
                    connection._signal_owner_shutdown("gateway drain deadline")
                except Exception:
                    pass
                forced += 1
        if forced:
            self.lifecycle.metrics.add("drain_forced", forced)

    def _settle_closed_connections(self) -> None:
        """Assert that structured owners retired before carriers stopped."""

        if self._connection_snapshot() or self._connection_owner_snapshot():
            raise GatewayError(
                "gateway stopped carriers with nonterminal connection owners"
            )

    def _record_accept_shutdown_failure(self, reason: str, error=None):
        """Preserve an earlier failure while returning this cleanup failure."""

        if self.lifecycle.state != STATE_FAILED:
            self.lifecycle.fail(reason)
        if isinstance(error, Exception):
            return error
        return GatewayError(reason)

    def _stop_accept_owner(self):
        """Cancel and boundedly observe the accept continuation before pool stop."""

        thread = self.accept_thread
        if thread is None:
            return None
        first_error = None
        first_reason = ""
        try:
            self.scheduler.cancel(thread)
        except Exception as error:
            first_error = error
            first_reason = (
                "gateway accept owner cancellation failed: " + str(error)
            )

        try:
            deadline = self.transport.now_ms() + self.config.drain_timeout_ms
        except Exception as error:
            if first_error is None:
                first_error = error
                first_reason = (
                    "gateway accept owner shutdown clock failed: " + str(error)
                )
            return self._record_accept_shutdown_failure(
                first_reason,
                first_error,
            )

        outcome = virtual_thread.OUTCOME_PENDING
        while True:
            try:
                outcome = self.scheduler.outcome(thread)
            except Exception as error:
                if first_error is None:
                    first_error = error
                    first_reason = (
                        "gateway accept owner shutdown inspection failed: "
                        + str(error)
                    )
                break
            if outcome != virtual_thread.OUTCOME_PENDING:
                break
            try:
                now = self.transport.now_ms()
            except Exception as error:
                if first_error is None:
                    first_error = error
                    first_reason = (
                        "gateway accept owner shutdown clock failed: " + str(error)
                    )
                break
            if now >= deadline:
                if first_error is None:
                    first_reason = (
                        "gateway accept owner did not terminate before "
                        "shutdown deadline"
                    )
                    first_error = GatewayError(first_reason)
                break
            remaining = deadline - now
            delay = self.config.control_poll_ms
            if delay > remaining:
                delay = remaining
            try:
                self.transport.idle_wait(delay)
            except Exception as error:
                if first_error is None:
                    first_error = error
                    first_reason = (
                        "gateway accept owner shutdown wait failed: " + str(error)
                    )
                break

        if outcome == virtual_thread.OUTCOME_RETURNED:
            self.accept_thread = None
            try:
                self.scheduler.result(thread)
            except Exception as error:
                if first_error is None:
                    first_error = error
                    first_reason = (
                        "gateway accept owner result inspection failed: " + str(error)
                    )
        elif outcome == virtual_thread.OUTCOME_RAISED:
            self.accept_thread = None
            try:
                error = self.scheduler.exception(thread)
            except Exception as inspection_error:
                error = inspection_error
                reason = (
                    "gateway accept owner exception inspection failed: "
                    + str(inspection_error)
                )
            else:
                reason = "gateway accept owner raised during shutdown"
                if error is not None and str(error):
                    reason += ": " + str(error)
            if first_error is None:
                if isinstance(error, Exception):
                    first_error = error
                else:
                    first_error = GatewayError(reason)
                first_reason = reason
        elif outcome == virtual_thread.OUTCOME_CANCELLED:
            self.accept_thread = None
        elif outcome != virtual_thread.OUTCOME_PENDING:
            self.accept_thread = None
            if first_error is None:
                first_reason = (
                    "gateway accept owner reported unknown shutdown outcome "
                    + str(outcome)
                )
                first_error = GatewayError(first_reason)

        if first_error is not None:
            return self._record_accept_shutdown_failure(
                first_reason,
                first_error,
            )
        return None

    def _shutdown_all(self):
        """Best-effort teardown; return the first cleanup failure."""
        first_error = None
        try:
            if self.lifecycle.state == STATE_RUNNING:
                self.lifecycle.begin_drain()
        except Exception as error:
            if first_error is None:
                first_error = error
        accept_error = self._stop_accept_owner()
        if first_error is None and accept_error is not None:
            first_error = accept_error
        owners_terminal = False
        try:
            if self._connection_snapshot():
                self._force_close_connections()
            owners_terminal, owner_error = self._await_connection_owners()
            if first_error is None and owner_error is not None:
                first_error = owner_error
        except Exception as error:
            if first_error is None:
                first_error = error
        if not owners_terminal:
            reason = (
                "gateway connection owners did not terminate before "
                "shutdown deadline"
            )
            if self.lifecycle.state != STATE_FAILED:
                self.lifecycle.fail(reason)
            if first_error is None:
                first_error = GatewayError(reason)

        # A nonterminal accept or connection continuation may still own a
        # waitset registration, request admission, TLS session, BodyStream and
        # GC scheduler root. Stopping carriers or releasing application state
        # here would leak those owners and allow queued work to resume in a
        # later pool. Preserve the execution root and make the incomplete
        # shutdown explicit; this method is retryable after cooperative owners
        # reach terminal state.
        if self.accept_thread is not None or not owners_terminal:
            return first_error or GatewayError(
                "gateway shutdown retained nonterminal virtual-thread owners"
            )
        for pool in self.proxy_pools.values():
            try:
                pool.close_idle()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self.pool_started:
            try:
                stopped = self.scheduler.stop()
            except Exception as error:
                if first_error is None:
                    first_error = error
                return first_error
            if isinstance(stopped, int) and stopped < 0:
                error = GatewayError("virtual-thread carrier pool did not stop")
                if first_error is None:
                    first_error = error
                return first_error
            self.pool_started = False
        # At this point the accept and connection owners are terminal and every
        # carrier that could hold their registrations has joined. Only now may
        # the listener number be closed and reused by the process.
        try:
            self._close_listener()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            self._settle_closed_connections()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            if self.lifecycle.state == STATE_DRAINING:
                if not self.lifecycle.finish_drain():
                    self.lifecycle.fail("gateway drain retained active owners")
        except Exception as error:
            if first_error is None:
                first_error = error
        if self.lifecycle.state != STATE_STOPPED:
            try:
                current = self.lifecycle.current
                if not current.retired:
                    current.retire()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self.app_started:
            try:
                self.app.shutdown()
            except Exception as error:
                if first_error is None:
                    first_error = error
            self.app_started = False
        if self.tls_manager is not None:
            try:
                self.tls_manager.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if self.process_control_installed:
            try:
                self.process_control.uninstall()
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self.process_control_installed = False
        return first_error

    def shutdown(self) -> None:
        """Run retryable structured teardown from an embedding control owner."""

        error = self._shutdown_all()
        if error is not None:
            raise error

    def _await_connection_owners(self):
        """Wait for both resource-ledger removal and scheduler terminal state."""

        deadline = self.transport.now_ms() + self.config.drain_timeout_ms
        first_error = None
        while True:
            terminal, owner_error = self._reap_connection_owners()
            if first_error is None and owner_error is not None:
                first_error = owner_error
            if not self._connection_snapshot() and terminal:
                return True, first_error
            now = self.transport.now_ms()
            if now >= deadline:
                return False, first_error
            remaining = deadline - now
            delay = self.config.control_poll_ms
            if delay > remaining:
                delay = remaining
            self.transport.idle_wait(delay)

    def _drive_drain(self) -> bool:
        if self.lifecycle.state != STATE_DRAINING:
            return False
        active = self.lifecycle.metrics.get("connections_active")
        if active == 0:
            self.lifecycle.finish_drain()
            return True
        now = self.transport.now_ms()
        if now - self.drain_started_ms >= self.config.drain_timeout_ms:
            self._force_close_connections()
            # Connection vthreads own admission release.  Force completion is
            # recorded by lifecycle only after those finally blocks run. The
            # control loop still exits at the deadline so _shutdown_all can
            # boundedly prove terminal task handles or return a fail-closed,
            # retryable teardown error instead of spinning forever.
            if self.lifecycle.metrics.get("connections_active") == 0:
                self.lifecycle.finish_drain()
            return True
        return False

    def _fail_accept_owner(self, reason: str, error=None) -> None:
        """Record one accept-owner failure before entering common teardown."""

        self.lifecycle.fail(reason)
        if isinstance(error, Exception):
            raise error
        raise GatewayError(reason)

    def _check_accept_owner(self) -> None:
        """Fail closed when the listener's sole virtual-thread owner exits."""

        if self.accept_thread is None:
            if self.lifecycle.state == STATE_RUNNING:
                self._fail_accept_owner("gateway accept owner is missing")
            return
        outcome = -1
        try:
            outcome = self.scheduler.outcome(self.accept_thread)
        except Exception as error:
            self._fail_accept_owner(
                "gateway accept owner health check failed: " + str(error),
                error,
            )
        if outcome == virtual_thread.OUTCOME_PENDING:
            return
        if outcome == virtual_thread.OUTCOME_RETURNED:
            # A return after admission has entered drain is a successful owner
            # terminal outcome; returning while admission is open abandons the
            # port.  The listener itself remains open until owner retirement.
            if self.lifecycle.state != STATE_RUNNING:
                return
            self._fail_accept_owner(
                "gateway accept owner returned while server is running"
            )
        if outcome == virtual_thread.OUTCOME_RAISED:
            error = None
            try:
                error = self.scheduler.exception(self.accept_thread)
            except Exception as inspection_error:
                self._fail_accept_owner(
                    "gateway accept owner exception inspection failed: "
                    + str(inspection_error),
                    inspection_error,
                )
            detail = ""
            if error is not None:
                detail = str(error)
            reason = "gateway accept owner raised"
            if detail:
                reason += ": " + detail
            self._fail_accept_owner(reason, error)
        if outcome == virtual_thread.OUTCOME_CANCELLED:
            self._fail_accept_owner("gateway accept owner was cancelled")
        self._fail_accept_owner(
            "gateway accept owner reported unknown outcome " + str(outcome)
        )

    def run(self) -> int:
        self.start()
        run_error = None
        try:
            while self.lifecycle.state in (STATE_RUNNING, STATE_DRAINING):
                _owners_terminal, owner_error = self._reap_connection_owners()
                if owner_error is not None:
                    raise owner_error
                control = None
                if self.process_control_installed:
                    control = self.process_control.poll()
                control_reload_requested = False
                control_stop_requested = False
                if control is not None:
                    control_reload_requested = control.reload_requested
                    control_stop_requested = control.stop_requested
                if control_reload_requested:
                    # A signal is only an allocation-safe notification.  A
                    # validated replacement generation must be supplied by
                    # the application/control plane through reload().
                    self.lifecycle.metrics.add("reload_requested")
                    if self.reload_factory is not None:
                        replacement = self.reload_factory(self.config)
                        if replacement is not None:
                            self.reload(replacement)
                if control_stop_requested:
                    self.request_stop()
                self._check_accept_owner()
                if self.lifecycle.state == STATE_DRAINING and self._drive_drain():
                    break
                self.transport.idle_wait(self.config.control_poll_ms)
        except Exception as error:
            run_error = error
        cleanup_error = self._shutdown_all()
        if cleanup_error is not None and (
            self.pool_started
            or self.accept_thread is not None
            or bool(self._connection_snapshot())
            or bool(self._connection_owner_snapshot())
        ):
            # App.run constructs the server internally, so an incomplete
            # teardown must be the visible error: otherwise callers could see
            # only the earlier request failure while live carriers/listener
            # owners remain hidden inside the local server object.
            raise cleanup_error
        if run_error is not None:
            raise run_error
        if cleanup_error is not None:
            raise cleanup_error
        return 0 if self.lifecycle.state == STATE_STOPPED else -1


__all__ = [
    "PCC_SOCKET_PROGRESS",
    "PCC_SOCKET_WOULD_BLOCK",
    "PCC_SOCKET_EOF",
    "PCC_SOCKET_CONNECTED",
    "PCC_IO_READ",
    "PCC_IO_WRITE",
    "GatewayError",
    "GatewayTransportError",
    "UnsupportedGatewayFeature",
    "GatewayConfig",
    "GatewayHooks",
    "NativeSocketTransport",
    "NativeVirtualThreadScheduler",
    "GatewayConnection",
    "GatewayServer",
]
