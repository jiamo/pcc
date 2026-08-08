"""Focused contracts for pcc-native local request-body streaming.

These tests deliberately script virtual-thread edges.  They distinguish the
host sans-I/O RequestEnd path from the live transport capability without
borrowing a host socket or making CPython own the scheduler.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pcc.gateway.server as gateway_server
import pcc.virtual_thread as virtual_thread
from pcc.gateway.buffer import BufferSegment
from pcc.gateway.config import BufferLimits, GatewayConfig, Http1Limits
from pcc.gateway.http1 import BodyChunk
from pcc.gateway.lifecycle import GatewayLifecycle
from pcc.gateway.proxy import RetryPolicy, UpstreamEndpoint, UpstreamGroup
from pcc.gateway.server import (
    PCC_SOCKET_PROGRESS,
    GatewayConnection,
    GatewayError,
    GatewayHooks,
)
from pcc.web import App, BodyStream, Request, Response, post, proxy


class StreamingTransport:
    native_virtual_threads = False
    local_body_streaming = True

    def __init__(self) -> None:
        self.clock_ms = 100
        self.reads = []
        self.read_calls = 0
        self.written = bytearray(b"")
        self.closed = []

    def now_ms(self) -> int:
        return self.clock_ms

    def read(self, fd: int, limit: int):
        self.read_calls += 1
        if self.reads:
            return self.reads.pop(0)
        raise AssertionError("connection owner attempted an extra client read")

    def write(self, fd: int, data: bytes):
        self.written.extend(data)
        return PCC_SOCKET_PROGRESS, len(data)

    def wait(self, fd: int, events: int, timeout_ms: int) -> None:
        self.clock_ms += timeout_ms

    def shutdown(self, fd: int) -> int:
        return 0

    def close(self, fd: int) -> int:
        self.closed.append(fd)
        return 0


class ScriptedChildRuntime:
    def __init__(self) -> None:
        self.thread = object()
        self.spawned = []
        self.outcomes = []
        self.join_result = Response.text("joined")
        self.joined = []
        self.cancelled = []
        self.yields = 0

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(virtual_thread, "spawn", self.spawn)
        monkeypatch.setattr(virtual_thread, "outcome", self.outcome)
        monkeypatch.setattr(virtual_thread, "join", self.join)
        monkeypatch.setattr(virtual_thread, "cancel", self.cancel)
        monkeypatch.setattr(virtual_thread, "yield_now", self.yield_now)
        monkeypatch.setattr(
            virtual_thread, "sleep_current", self.sleep_current
        )

    def spawn(self, fn, *args):
        self.spawned.append((fn, args))
        return self.thread

    def outcome(self, thread):
        assert thread is self.thread
        if self.outcomes:
            return self.outcomes.pop(0)
        return virtual_thread.OUTCOME_PENDING

    def join(self, thread):
        assert thread is self.thread
        self.joined.append(thread)
        return self.join_result

    def cancel(self, thread):
        assert thread is self.thread
        self.cancelled.append(thread)
        return True

    def yield_now(self) -> None:
        self.yields += 1

    def sleep_current(self, delay_ms: int) -> None:
        self.yields += 1


def _connection(app, transport=None, config=None):
    if config is None:
        config = GatewayConfig()
    lifecycle = GatewayLifecycle(config, config.admission)
    lifecycle.start()
    lifecycle.started()
    assert lifecycle.admit_connection()
    generation = lifecycle.acquire_generation()
    connection = GatewayConnection(
        app,
        41 if transport is not None else -1,
        transport,
        lifecycle,
        generation,
        config,
        client_ip="127.0.0.1",
    )
    return connection, lifecycle, generation


def _release_connection(connection, lifecycle, generation) -> None:
    connection.close("test-complete")
    generation.release()
    lifecycle.release_connection()


def test_live_local_handler_spawns_at_head_and_joins_after_request_end(
    monkeypatch,
) -> None:
    runtime = ScriptedChildRuntime()
    runtime.outcomes = [
        virtual_thread.OUTCOME_PENDING,
        virtual_thread.OUTCOME_PENDING,
    ]
    runtime.install(monkeypatch)
    app = App(routes=(post("/upload", lambda request: request.read_body()),))
    connection, lifecycle, generation = _connection(
        app, StreamingTransport()
    )

    assert connection.feed_data(
        b"POST /upload HTTP/1.1\r\nHost: local\r\n"
        b"Content-Length: 4\r\n\r\n",
        1,
    ) == 1
    request = connection.pending_local_request
    assert request is connection.current_request
    assert request is not None
    assert request.body.streaming
    assert len(runtime.spawned) == 1
    assert lifecycle.metrics.get("requests_active") == 1
    assert not connection.current_admitted

    assert connection.feed_data(b"pcc1", 1) == 0
    connection._consume_pending_local_body_events(request)
    assert request.body.read_chunk() == b"pcc1"
    assert request.body.consumed_size() == 4
    assert request.body.is_ended()

    assert connection._drive_pending_local()
    assert runtime.joined == [runtime.thread]
    assert connection.pending_local_request is None
    assert connection.pending_local_thread is None
    assert lifecycle.metrics.get("requests_active") == 0
    assert connection.take_output().endswith(b"joined")
    _release_connection(connection, lifecycle, generation)


def test_buffered_body_is_consumed_without_an_extra_client_read(
    monkeypatch,
) -> None:
    runtime = ScriptedChildRuntime()
    runtime.outcomes = [
        virtual_thread.OUTCOME_PENDING,
        virtual_thread.OUTCOME_PENDING,
    ]
    runtime.install(monkeypatch)
    transport = StreamingTransport()
    transport.reads = [(
        PCC_SOCKET_PROGRESS,
        b"POST / HTTP/1.1\r\nHost: local\r\nContent-Length: 4\r\n"
        b"Connection: close\r\n\r\npcc1",
    )]
    connection, lifecycle, generation = _connection(
        App(routes=(post("/", lambda request: request.read_body()),)),
        transport,
    )

    connection.run()

    assert transport.read_calls == 1
    assert bytes(transport.written).endswith(b"joined")
    assert lifecycle.metrics.get("requests_active") == 0
    generation.release()
    lifecycle.release_connection()


def test_body_stream_high_low_compaction_and_consumed_bytes_are_monotonic() -> None:
    body = BodyStream(256, low_watermark=32, high_watermark=64, streaming=True)
    segments = []
    index = 0
    while index < 96:
        segment = BufferSegment(1)
        segment.write(b"x")
        view = segment.view()
        segment.release()
        segments.append(segment)
        body.feed(view)
        view.release()
        index += 1

    assert body.is_backpressured()
    index = 0
    while index < 80:
        assert body.read_chunk() == b"x"
        index += 1
    assert body.consumed_size() == 80
    assert body.queued_size() == 16
    assert len(body.chunks) < 64
    assert not body.is_backpressured()
    body.finish()
    body.close()
    assert all(segment.released for segment in segments)


def test_proxy_retry_is_denied_after_compacted_body_history(monkeypatch) -> None:
    body = BodyStream(256, low_watermark=8, high_watermark=32)
    index = 0
    while index < 80:
        body.feed(b"x")
        assert body.read_chunk() == b"x"
        index += 1
    assert body.read_index < 64
    assert body.consumed_size() == 80

    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("127.0.0.1", 9000),)
    )
    app = App(
        routes=(proxy(
            "/api/{path*}",
            "backend",
            method="POST",
            retry=RetryPolicy(attempts=2, methods=("POST",)),
        ),),
        upstreams=(group,),
    )
    connection, lifecycle, generation = _connection(app)
    request = Request(
        "POST", "/api/value", headers=(("host", "local"),), body=body
    )
    connection.pending_proxy = app.dispatch_proxy_head(request)
    connection.pending_proxy_request = request
    assert lifecycle.admit_request()
    attempts = []

    def fail_attempt(connection, plan, pool, attempt):
        attempts.append(attempt)
        return 0, "connect", False

    monkeypatch.setattr(
        gateway_server, "_proxy_exchange_attempt", fail_attempt
    )
    connection.proxy_pools = {"backend": object()}
    gateway_server._run_gateway_proxy(connection)

    assert attempts == [1]
    assert lifecycle.metrics.get("upstream_retries") == 0
    assert lifecycle.metrics.get("requests_active") == 0
    _release_connection(connection, lifecycle, generation)


def test_local_response_framing_omits_forbidden_or_unknown_lengths() -> None:
    connection, lifecycle, generation = _connection(App())
    get_request = Request("GET", "/")
    head_request = Request("HEAD", "/")

    no_content = connection._response_payload(
        get_request, Response(204, b"ignored")
    )
    not_modified = connection._response_payload(
        get_request, Response(304, b"representation")
    )
    streamed_not_modified = connection._response_payload(
        get_request, Response.stream((b"ignored",), status=304)
    )
    streamed_head = connection._response_payload(
        head_request, Response.stream((b"unknown",))
    )
    ordinary_head = connection._response_payload(
        head_request, Response.bytes(b"known")
    )

    assert b"content-length" not in no_content.lower()
    assert b"Content-Length: 14" in not_modified
    assert b"content-length" not in streamed_not_modified.lower()
    assert b"transfer-encoding" not in streamed_not_modified.lower()
    assert b"content-length" not in streamed_head.lower()
    assert b"transfer-encoding" not in streamed_head.lower()
    assert b"Content-Length: 5" in ordinary_head
    assert not ordinary_head.endswith(b"known")
    with pytest.raises(ValueError, match="200..599"):
        Response(103, b"not terminal")
    with pytest.raises(ValueError, match="200..599"):
        Response(600, b"invalid")
    _release_connection(connection, lifecycle, generation)


def test_feed_data_failure_releases_current_and_suffix_body_owners(
    monkeypatch,
) -> None:
    connection, lifecycle, generation = _connection(App())
    chunks = [BodyChunk(b"a"), BodyChunk(b"b")]
    monkeypatch.setattr(connection.codec, "feed", lambda data, limit: chunks)

    def fail_current(event):
        try:
            raise RuntimeError("handler failed")
        finally:
            event.release()

    monkeypatch.setattr(connection, "_handle_event", fail_current)
    with pytest.raises(RuntimeError, match="handler failed"):
        connection.feed_data(b"ignored", 1)

    assert chunks[0].released
    assert chunks[1].released
    _release_connection(connection, lifecycle, generation)


def test_pending_child_cleanup_wakes_cancels_joins_and_releases_once(
    monkeypatch,
) -> None:
    runtime = ScriptedChildRuntime()
    runtime.install(monkeypatch)
    connection, lifecycle, generation = _connection(
        App(routes=(post("/", lambda request: request.read_body()),)),
        StreamingTransport(),
    )
    connection.feed_data(
        b"POST / HTTP/1.1\r\nHost: local\r\nContent-Length: 4\r\n\r\n",
        1,
    )
    request = connection.pending_local_request
    assert request is not None

    connection._cancel_and_join_pending_local("failure-injection")

    assert request.cancellation.is_cancelled()
    assert request.body.cancelled
    assert runtime.cancelled == [runtime.thread]
    assert runtime.joined == [runtime.thread]
    assert connection.pending_local_request is None
    assert lifecycle.metrics.get("requests_active") == 0
    connection._cancel_and_join_pending_local("second-call")
    assert runtime.cancelled == [runtime.thread]
    assert runtime.joined == [runtime.thread]
    _release_connection(connection, lifecycle, generation)


def test_stalled_high_water_body_wait_observes_request_deadline(
    monkeypatch,
) -> None:
    runtime = ScriptedChildRuntime()
    runtime.install(monkeypatch)
    config = GatewayConfig(
        http1=Http1Limits(body_bytes=64, chunk_bytes=64, body_timeout_ms=5),
        buffers=BufferLimits(
            segment_bytes=8,
            low_watermark=2,
            high_watermark=4,
            connection_bytes=128,
        ),
    )
    transport = StreamingTransport()
    connection, lifecycle, generation = _connection(
        App(routes=(post("/", lambda request: request.read_body()),)),
        transport,
        config,
    )
    connection.feed_data(
        b"POST / HTTP/1.1\r\nHost: local\r\nContent-Length: 8\r\n\r\n"
        b"abcd",
        1,
    )
    request = connection.pending_local_request
    connection._consume_pending_local_body_events(request)
    transport.clock_ms = request.cancellation.deadline_ms
    connection._deadline_timeout("request")
    runtime.outcomes = [virtual_thread.OUTCOME_PENDING]

    assert connection._drive_pending_local()
    assert runtime.cancelled == [runtime.thread]
    assert lifecycle.metrics.get("requests_active") == 0
    assert connection.take_output().startswith(b"HTTP/1.1 408")
    _release_connection(connection, lifecycle, generation)


def test_stream_completion_takes_owner_before_reentrant_close() -> None:
    class ClosingHook(GatewayHooks):
        def request_finished(self, connection, request, response) -> None:
            connection.close("hook-close")

    connection, lifecycle, generation = _connection(App())
    connection.hooks = ClosingHook()
    request = Request("GET", "/")
    response = Response.stream((b"x",))
    assert lifecycle.admit_request()
    connection.pending_stream_request = request
    connection.pending_stream_response = response
    connection.pending_stream_iterator = iter(response.body)
    connection.pending_stream_admitted = True

    connection._complete_streaming_response()

    assert connection.closed
    assert connection.pending_stream_request is None
    assert lifecycle.metrics.get("requests_active") == 0
    generation.release()
    lifecycle.release_connection()


def test_stream_iterator_failure_precedes_owner_publication_and_rolls_back() -> None:
    class ThrowingIterable:
        def __iter__(self):
            raise RuntimeError("iterator construction failed")

    connection, lifecycle, generation = _connection(App())
    body = BodyStream()
    request = Request("GET", "/", body=body)
    response = Response.stream(ThrowingIterable())
    assert lifecycle.admit_request()

    with pytest.raises(RuntimeError, match="iterator construction failed"):
        connection._complete_local_handler_response(request, response)

    assert connection.pending_stream_request is None
    assert connection.pending_stream_response is None
    assert connection.pending_stream_iterator is None
    assert lifecycle.metrics.get("requests_active") == 0
    assert body.closed
    assert connection.take_output() == b""
    _release_connection(connection, lifecycle, generation)


def test_cancelled_stream_retires_owner_without_reentering_iterator() -> None:
    class CountingIterator:
        def __init__(self) -> None:
            self.next_calls = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.next_calls += 1
            return b"must-not-run"

    connection, lifecycle, generation = _connection(App())
    body = BodyStream()
    request = Request("GET", "/", body=body)
    iterator = CountingIterator()
    response = Response.stream(iterator)
    assert lifecycle.admit_request()
    connection._begin_streaming_response(request, response, True)
    request.cancellation.cancel("gateway shutdown")

    connection._drive_streaming_response()

    assert iterator.next_calls == 0
    assert connection.pending_stream_request is None
    assert connection.close_after_flush
    assert lifecycle.metrics.get("requests_active") == 0
    assert body.closed
    connection.take_output()
    _release_connection(connection, lifecycle, generation)


def test_direct_close_defers_pending_child_settlement_to_connection_owner(
    monkeypatch,
) -> None:
    runtime = ScriptedChildRuntime()
    runtime.install(monkeypatch)
    connection, lifecycle, generation = _connection(
        App(routes=(post("/", lambda request: request.read_body()),)),
        StreamingTransport(),
    )
    connection.feed_data(
        b"POST / HTTP/1.1\r\nHost: local\r\nContent-Length: 4\r\n\r\n",
        1,
    )
    request = connection.pending_local_request

    with pytest.raises(GatewayError, match="requires connection owner"):
        connection.close("external-close")

    assert connection.pending_local_request is request
    assert lifecycle.metrics.get("requests_active") == 1
    connection._cancel_and_join_pending_local("owner-finally")
    assert connection.pending_local_request is None
    assert connection.current_request is None
    assert lifecycle.metrics.get("requests_active") == 0
    generation.release()
    lifecycle.release_connection()


def test_gateway_top_level_may_park_edges_use_explicit_call_boundary() -> None:
    source = Path(gateway_server.__file__).read_text(encoding="utf-8")
    normalized_source = "".join(source.split())
    required = (
        "virtual_thread.call(server.accept_once, listener_fd)",
        "virtual_thread.call(server._connection_finished, connection)",
        "virtual_thread.call(connection._drive_pending_local)",
        "virtual_thread.call(connection._drive_streaming_response)",
        "virtual_thread.call(connection.input_eof)",
        "virtual_thread.call(connection._cancel_and_join_pending_local, reason)",
        "virtual_thread.call(connection.close, reason)",
        "virtual_thread.call(connection.run)",
    )
    for boundary in required:
        assert "".join(boundary.split()) in normalized_source


def test_request_body_canonical_handler_methods_are_effect_visible() -> None:
    from pcc.py_frontend.codegen.vthread_effect_analysis import (
        compute_vthread_may_park_methods,
    )
    from pcc.py_frontend.parser import parse

    path = Path(gateway_server.__file__).parents[1] / "web" / "models.py"
    module = parse(path.read_text(encoding="utf-8"), str(path))
    _method_ids, method_keys = compute_vthread_may_park_methods(module, set())
    assert {
        "BodyStream.read_chunk",
        "BodyStream.read",
        "BodyStream.wait_writable",
        "Request.read_body",
        "Request.read_body_chunk",
    } <= method_keys


def test_threading_wait_effect_roots_exclude_plain_lock_acquire() -> None:
    from pcc.py_frontend.codegen.vthread_effect_analysis import (
        compute_vthread_may_park_functions,
    )
    from pcc.py_frontend.parser import parse

    module = parse(
        """import threading

def event_wait():
    event = threading.Event()
    event.wait()

def condition_wait():
    condition = threading.Condition()
    condition.wait()

def semaphore_wait():
    semaphore = threading.Semaphore(0)
    semaphore.acquire()

def short_lock_region():
    lock = threading.Lock()
    lock.acquire()
    lock.release()
""",
        "threading_effect_contract.py",
    )
    _function_ids, names = compute_vthread_may_park_functions(module)
    assert {"event_wait", "condition_wait", "semaphore_wait"} <= names
    assert "short_lock_region" not in names
