import os
import subprocess
from pathlib import Path
from threading import Barrier, Event, Lock, Thread

import pytest

from pcc.gateway.dns import DNS_A, DnsError
from pcc.gateway.proxy import (
    ProxyTimeouts,
    RetryPolicy,
    UpstreamEndpoint,
    UpstreamGroup,
    forwarded_request_headers,
    proxy_failure_status,
    sanitize_hop_by_hop,
)
from pcc.gateway.proxy_http1 import (
    Http1UpstreamCodec,
    ProxyDeadline,
    ProxyExchange,
    ProxyProtocolError,
    UpstreamConnectionPool,
    UpstreamResponseBody,
    UpstreamResponseEnd,
    UpstreamResponseHead,
)
from pcc1_gate import find_current_pcc1


REPO = Path(__file__).resolve().parents[2]
PCC1_PROXY_SOURCE = (
    REPO / "tests" / "fixtures" / "gateway" / "current_pcc1_proxy_exchange.py"
)


def test_connection_named_and_standard_hop_headers_are_removed() -> None:
    headers = [
        ("host", "example"),
        ("connection", "keep-alive, X-Private"),
        ("keep-alive", "timeout=5"),
        ("x-private", "secret"),
        ("x-end-to-end", "kept"),
    ]
    assert sanitize_hop_by_hop(headers) == [
        ("host", "example"),
        ("x-end-to-end", "kept"),
    ]


def test_forwarded_headers_are_reconstructed_at_trust_boundary() -> None:
    output = forwarded_request_headers(
        [("x-forwarded-for", "untrusted"), ("host", "front")],
        "192.0.2.4",
        "https",
        "front",
        False,
    )
    assert ("x-forwarded-for", "192.0.2.4") in output
    assert ("x-forwarded-proto", "https") in output
    assert ("x-forwarded-host", "front") in output
    assert ("x-forwarded-for", "untrusted") not in output


def test_upstream_admission_and_lease_release_are_bounded() -> None:
    group = UpstreamGroup(
        "backend",
        (UpstreamEndpoint("127.0.0.1", 8001), UpstreamEndpoint("127.0.0.1", 8002)),
        max_active=2,
    )
    first = group.acquire()
    second = group.acquire()
    assert first is not None and second is not None
    assert group.acquire() is None
    first.release()
    assert group.acquire() is not None


def test_upstream_dns_addresses_rotate_without_changing_authority() -> None:
    endpoint = UpstreamEndpoint("api.internal", 8443)
    values = ("192.0.2.10", "192.0.2.11")
    assert endpoint.choose_address(values) == "192.0.2.10"
    assert endpoint.choose_address(values) == "192.0.2.11"
    assert endpoint.choose_address(values) == "192.0.2.10"
    assert endpoint.host == "api.internal"
    # A policy-approved replacement set starts a fresh deterministic cycle.
    assert endpoint.choose_address(("198.51.100.20",)) == "198.51.100.20"

    endpoint2 = UpstreamEndpoint("api.internal", 8443)
    assert endpoint2.ordered_addresses(values) == values
    assert endpoint2.ordered_addresses(values) == (
        "192.0.2.11",
        "192.0.2.10",
    )
    endpoint3 = UpstreamEndpoint("api.internal", 8443)
    assert endpoint3.ordered_addresses(values) == values
    # DNS servers may reorder an unchanged RRset.  That must not reset the
    # endpoint's rotation cursor and repeatedly select the first record.
    assert endpoint3.ordered_addresses(tuple(reversed(values))) == (
        "192.0.2.11",
        "192.0.2.10",
    )
    # The policy belongs to the endpoint, not one client connection's
    # resolver, so a later public-to-private rebind is rejected globally.
    endpoint2.accept_resolved(["192.0.2.12"], DNS_A)
    with pytest.raises(DnsError, match="class changed"):
        endpoint2.accept_resolved(["10.0.0.12"], DNS_A)


def test_pool_owns_atomic_dns_rebinding_and_address_rotation() -> None:
    endpoint = UpstreamEndpoint("api.internal", 8443)
    pool = UpstreamConnectionPool(UpstreamGroup("backend", (endpoint,)))

    assert pool.accept_and_order_addresses(
        endpoint, ("192.0.2.10", "192.0.2.11"), DNS_A
    ) == ("192.0.2.10", "192.0.2.11")
    assert pool.accept_and_order_addresses(
        endpoint, ("192.0.2.11", "192.0.2.10"), DNS_A
    ) == ("192.0.2.11", "192.0.2.10")
    with pytest.raises(DnsError, match="class changed"):
        pool.accept_and_order_addresses(endpoint, ("10.0.0.1",), DNS_A)
    with pytest.raises(RuntimeError, match="does not belong"):
        pool.accept_and_order_addresses(
            UpstreamEndpoint("other.internal", 8443),
            ("192.0.2.12",),
            DNS_A,
        )


def test_retry_is_safe_method_and_precommit_only() -> None:
    retry = RetryPolicy(attempts=2)
    assert retry.allows("GET", 1, False, "connect")
    assert not retry.allows("POST", 1, False, "connect")
    assert not retry.allows("GET", 1, True, "connect")
    assert not retry.allows("GET", 2, False, "connect")
    assert not retry.allows("GET", 1, False, "connect", False)
    assert proxy_failure_status("connect-timeout") == 504
    assert proxy_failure_status("request-body-timeout") == 408
    assert proxy_failure_status("overloaded") == 503
    assert proxy_failure_status("reset") == 502


def test_upstream_codec_streams_fragmented_chunked_response() -> None:
    codec = Http1UpstreamCodec("GET", max_body_bytes=64, max_chunk_bytes=32)
    events = []
    fragments = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chu",
        b"nked\r\nConnection: keep-alive\r\n\r\n4\r\nWi",
        b"ki\r\n5\r\npcc1!\r\n0\r\n\r\n",
    )
    for fragment in fragments:
        events.extend(codec.feed(fragment))

    heads = [event for event in events if isinstance(event, UpstreamResponseHead)]
    bodies = [event.data for event in events if isinstance(event, UpstreamResponseBody)]
    ends = [event for event in events if isinstance(event, UpstreamResponseEnd)]
    assert len(heads) == 1
    assert heads[0].status == 200
    assert heads[0].chunked
    assert heads[0].keep_alive
    assert b"".join(bodies) == b"Wikipcc1!"
    assert len(ends) == 1
    assert codec.complete


@pytest.mark.parametrize("fragmented", (False, True))
def test_upstream_codec_rejects_complete_oversized_trailers(
    fragmented: bool,
) -> None:
    codec = Http1UpstreamCodec("GET", max_header_bytes=128)
    prefix = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"0\r\nx-test: "
    )
    if fragmented:
        codec.feed(prefix + b"a" * 110)
    payload = b"a" * (20 if fragmented else 130) + b"\r\n\r\n"
    with pytest.raises(ProxyProtocolError) as caught:
        codec.feed(payload if fragmented else prefix + payload)
    assert caught.value.code == "trailers-too-large"


def test_upstream_codec_rejects_ambiguous_or_unbounded_framing() -> None:
    codec = Http1UpstreamCodec("GET")
    with pytest.raises(ProxyProtocolError, match="both Transfer-Encoding"):
        codec.feed(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: 4\r\nTransfer-Encoding: chunked\r\n\r\n"
        )

    oversized = Http1UpstreamCodec("GET", max_body_bytes=3)
    with pytest.raises(ProxyProtocolError, match="body limit"):
        oversized.feed(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n")


@pytest.mark.parametrize(
    "size_line",
    (b" 1", b"1 ", b"+1", b"1_0", b"g", b""),
)
def test_upstream_codec_rejects_non_hexdig_chunk_size(size_line: bytes) -> None:
    codec = Http1UpstreamCodec("GET")
    with pytest.raises(ProxyProtocolError) as caught:
        codec.feed(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + size_line
            + b"\r\n"
        )
    assert caught.value.code == "bad-chunk-size"


@pytest.mark.parametrize(
    "extension",
    (
        b";",
        b";=value",
        b";name=",
        b";bad name=value",
        b";name=bad value",
        b";name=\"unterminated",
        b";name=\"bad\\\"",
        b";name=\"value\"junk",
    ),
)
def test_upstream_codec_rejects_invalid_chunk_extension(extension: bytes) -> None:
    codec = Http1UpstreamCodec("GET")
    with pytest.raises(ProxyProtocolError) as caught:
        codec.feed(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"1"
            + extension
            + b"\r\nx\r\n0\r\n\r\n"
        )
    assert caught.value.code == "bad-chunk-extension"


def test_upstream_codec_accepts_token_and_quoted_chunk_extensions() -> None:
    codec = Http1UpstreamCodec("GET")
    events = codec.feed(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"1;flag;name=value;quoted=\"a b\\\"c\"\r\nx\r\n0\r\n\r\n"
    )
    assert [
        event.data for event in events if isinstance(event, UpstreamResponseBody)
    ] == [b"x"]
    assert isinstance(events[-1], UpstreamResponseEnd)


def test_upstream_codec_rejects_complete_oversized_chunk_line() -> None:
    codec = Http1UpstreamCodec("GET")
    with pytest.raises(ProxyProtocolError) as caught:
        codec.feed(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"1;name="
            + b"x" * 128
            + b"\r\n"
        )
    assert caught.value.code == "chunk-line-too-long"


def test_upstream_codec_handles_informational_head_and_head_method() -> None:
    codec = Http1UpstreamCodec("HEAD")
    events = codec.feed(
        b"HTTP/1.1 100 Continue\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 9000\r\n\r\n"
    )
    heads = [event for event in events if isinstance(event, UpstreamResponseHead)]
    assert [head.status for head in heads] == [100, 200]
    assert heads[0].informational
    assert not heads[1].body_expected
    assert not any(isinstance(event, UpstreamResponseBody) for event in events)
    assert isinstance(events[-1], UpstreamResponseEnd)


def test_proxy_rebuilds_response_framing_from_parsed_state() -> None:
    informational = ProxyExchange(
        "GET", "/", [], "origin:80", "127.0.0.1", "http", "front"
    )
    informational.take_upstream()
    informational.feed_upstream(
        b"HTTP/1.1 103 Early Hints\r\nContent-Length: 99\r\n\r\n"
        b"HTTP/1.1 204 No Content\r\nTransfer-Encoding: chunked\r\n\r\n"
    )
    output, _ = informational.take_downstream()
    assert b"HTTP/1.1 103 " in output
    assert b"HTTP/1.1 204 No Content\r\n" in output
    assert b"Content-Length" not in output
    assert b"Transfer-Encoding" not in output

    head = ProxyExchange(
        "HEAD", "/", [], "origin:80", "127.0.0.1", "http", "front"
    )
    head.take_upstream()
    head.feed_upstream(
        b"HTTP/1.1 200 OK\r\nContent-Length: 42\r\n\r\n"
    )
    head_output, _ = head.take_downstream()
    assert b"Content-Length: 42\r\n" in head_output


def test_eof_framed_upstream_body_becomes_bounded_downstream_chunks() -> None:
    exchange = ProxyExchange(
        "GET",
        "/legacy",
        [("host", "front")],
        "127.0.0.1:9000",
        "192.0.2.1",
        "http",
        "front",
        max_buffered_bytes=1024,
    )
    exchange.take_upstream()
    exchange.feed_upstream(b"HTTP/1.0 200 OK\r\n\r\nlegacy-")
    exchange.feed_upstream(b"body")
    assert not exchange.response_finished
    exchange.upstream_eof()
    output, _ = exchange.take_downstream()
    assert b"transfer-encoding: chunked\r\n" in output.lower()
    assert output.endswith(b"7\r\nlegacy-\r\n4\r\nbody\r\n0\r\n\r\n")
    assert exchange.response_finished
    assert not exchange.upstream_keep_alive


def test_proxy_exchange_has_bidirectional_watermarks_and_stream_framing() -> None:
    exchange = ProxyExchange(
        "POST",
        "/upload",
        [
            ("host", "front.example"),
            ("connection", "X-Private"),
            ("x-private", "drop"),
            ("x-end-to-end", "keep"),
        ],
        "127.0.0.1:9000",
        "192.0.2.9",
        "https",
        "front.example",
        content_length=-1,
        chunked_request=True,
        segment_bytes=8,
        low_watermark=8,
        high_watermark=16,
        max_buffered_bytes=1024,
    )
    assert exchange.to_upstream.backpressured
    request_head, resumed = exchange.take_upstream()
    assert resumed
    assert request_head.startswith(b"POST /upload HTTP/1.1\r\n")
    assert b"host: 127.0.0.1:9000\r\n" in request_head
    assert b"x-private" not in request_head
    assert b"x-end-to-end: keep\r\n" in request_head
    assert b"transfer-encoding: chunked\r\n" in request_head

    exchange.feed_request_body(b"pcc")
    exchange.feed_request_body(b"1")
    exchange.finish_request()
    request_body, _ = exchange.take_upstream()
    assert request_body == b"3\r\npcc\r\n1\r\n1\r\n0\r\n\r\n"

    transition = exchange.feed_upstream(
        b"HTTP/1.1 200 OK\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: X-Hop\r\nX-Hop: remove\r\nX-Keep: yes\r\n\r\n"
        b"4\r\ndone\r\n0\r\n\r\n"
    )
    assert transition != 0
    assert exchange.to_downstream.backpressured
    response, resumed = exchange.take_downstream()
    assert resumed
    assert response.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"X-Hop" not in response and b"x-hop" not in response
    assert b"x-keep: yes\r\n" in response
    assert response.endswith(b"4\r\ndone\r\n0\r\n\r\n")
    assert exchange.response_committed and exchange.response_finished
    assert exchange.upstream_keep_alive


def test_proxy_retry_stops_at_downstream_commit_and_cancel_releases_buffers() -> None:
    retry = RetryPolicy(attempts=3)
    exchange = ProxyExchange(
        "GET",
        "/",
        [("host", "front")],
        "127.0.0.1:9000",
        "192.0.2.1",
        "http",
        "front",
    )
    assert exchange.can_retry(retry, 1, "connect")
    body_exchange = ProxyExchange(
        "GET",
        "/search",
        [("host", "front")],
        "127.0.0.1:9000",
        "192.0.2.1",
        "http",
        "front",
        content_length=1,
    )
    body_exchange.feed_request_body(b"x")
    assert not body_exchange.can_retry(retry, 1, "reset-before-head")
    exchange.feed_upstream(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    assert not exchange.can_retry(retry, 1, "reset-before-head")
    exchange.cancel("downstream disconnected")
    assert exchange.cancelled
    assert len(exchange.to_upstream) == 0
    assert len(exchange.to_downstream) == 0


def test_gateway_owned_continue_is_not_forwarded_or_duplicated() -> None:
    exchange = ProxyExchange(
        "POST",
        "/upload",
        [("host", "front"), ("expect", "100-continue")],
        "127.0.0.1:9000",
        "192.0.2.1",
        "http",
        "front",
        content_length=3,
        expect_continue_handled=True,
    )
    request_head, _ = exchange.take_upstream()
    assert b"expect:" not in request_head.lower()
    exchange.feed_request_body(b"pcc")
    exchange.finish_request()
    exchange.take_upstream()
    exchange.feed_upstream(
        b"HTTP/1.1 100 Continue\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
    )
    downstream, _ = exchange.take_downstream()
    assert b"100 Continue" not in downstream
    assert downstream.startswith(b"HTTP/1.1 200")


def test_proxy_deadline_is_stage_specific_and_absolute() -> None:
    deadline = ProxyDeadline(
        ProxyTimeouts(connect_ms=5, header_ms=7, body_ms=11, idle_ms=13),
        100,
    )
    assert not deadline.expired(104)
    assert deadline.expired(105)
    assert deadline.failure() == "connect-timeout"
    deadline.request_body(180)
    assert deadline.deadline_ms == 180
    assert deadline.failure() == "request-body-timeout"
    deadline.connected(200)
    assert deadline.deadline_ms == 207
    assert deadline.failure() == "header-timeout"
    deadline.response_head(300, True)
    assert deadline.deadline_ms == 311
    deadline.body_progress(305)
    assert deadline.deadline_ms == 318
    assert deadline.failure() == "body-timeout"
    deadline.finish()
    assert not deadline.expired(1000)


def test_upstream_keep_alive_pool_obeys_active_and_idle_bounds() -> None:
    group = UpstreamGroup(
        "backend",
        (UpstreamEndpoint("127.0.0.1", 9000),),
        max_active=1,
        max_idle=1,
    )
    closed = []
    pool = UpstreamConnectionPool(
        group,
        idle_timeout_ms=10,
        close_connection=lambda handle: closed.append(handle),
    )
    opened = []

    def open_connection(endpoint):
        opened.append(endpoint.port)
        return 70 + len(opened)

    first = pool.acquire(100, open_connection)
    assert first is not None and first.handle == 71
    assert pool.acquire(101, open_connection) is None
    assert pool.release(first, 102, reusable=True)
    reused = pool.acquire(103, open_connection)
    assert reused is first
    assert opened == [9000]
    assert not pool.release(reused, 104, reusable=False)
    assert group.active == 0
    assert closed == [71]

    expiring = pool.acquire(200, open_connection)
    assert expiring is not None
    assert pool.release(expiring, 200, reusable=True)
    replacement = pool.acquire(211, open_connection)
    assert replacement is not None and replacement is not expiring
    assert len(opened) == 3
    assert closed == [71, 72]


def test_pool_reservation_exposes_endpoint_before_numeric_socket_open() -> None:
    group = UpstreamGroup(
        "backend", (UpstreamEndpoint("api.internal", 9000),), max_active=1
    )
    closed = []
    pool = UpstreamConnectionPool(
        group, close_connection=lambda handle: closed.append(handle)
    )
    reservation = pool.reserve(10)
    assert reservation is not None and reservation.needs_open
    assert reservation.lease.endpoint.host == "api.internal"
    opened = pool.opened(reservation, 81)
    assert opened is reservation and opened.handle == 81
    assert not pool.release(opened, 11, reusable=False)
    assert closed == [81]
    assert group.active == 0


def test_pool_serializes_group_active_and_idle_bounds_across_carriers() -> None:
    max_active = 8
    max_idle = 3
    group = UpstreamGroup(
        "backend",
        (UpstreamEndpoint("127.0.0.1", 9000),),
        max_active=max_active,
        max_idle=max_idle,
    )
    closed = []
    closed_lock = Lock()

    def close_connection(handle: int) -> None:
        with closed_lock:
            closed.append(handle)

    pool = UpstreamConnectionPool(group, close_connection=close_connection)
    worker_count = 32
    start = Barrier(worker_count)
    result_lock = Lock()
    reservations = []

    def reserve_one() -> None:
        start.wait()
        connection = pool.reserve(100)
        with result_lock:
            reservations.append(connection)

    workers = [Thread(target=reserve_one) for _ in range(worker_count)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    active = [connection for connection in reservations if connection is not None]
    assert len(active) == max_active
    assert group.active == max_active
    for index, connection in enumerate(active):
        pool.opened(connection, 1000 + index)

    release_start = Barrier(max_active)

    def release_one(connection) -> None:
        release_start.wait()
        pool.release(connection, 101, reusable=True)

    releasers = [Thread(target=release_one, args=(connection,)) for connection in active]
    for worker in releasers:
        worker.start()
    for worker in releasers:
        worker.join()

    assert len(pool.idle) == max_idle
    assert group.active == max_idle
    assert len(closed) == max_active - max_idle
    assert pool.close_idle() == max_idle
    assert group.active == 0
    assert len(closed) == max_active


def test_shared_endpoint_admission_is_atomic_across_groups() -> None:
    endpoint = UpstreamEndpoint("127.0.0.1", 9000, weight=64)
    first_group = UpstreamGroup("first", (endpoint,), max_active=16)
    second_group = UpstreamGroup("second", (endpoint,), max_active=16)
    all_acquired = Barrier(33)
    release = Event()
    result_lock = Lock()
    leases = []

    def acquire_one(group) -> None:
        lease = group.acquire()
        with result_lock:
            leases.append(lease)
        all_acquired.wait()
        release.wait()
        if lease is not None:
            lease.release()

    workers = []
    for index in range(32):
        group = first_group if index < 16 else second_group
        workers.append(Thread(target=acquire_one, args=(group,)))
    for worker in workers:
        worker.start()
    all_acquired.wait()

    assert all(lease is not None for lease in leases)
    assert first_group.active == 16
    assert second_group.active == 16
    assert endpoint.active == 32

    release.set()
    for worker in workers:
        worker.join()
    assert first_group.active == 0
    assert second_group.active == 0
    assert endpoint.active == 0


def test_pool_close_callback_runs_after_unlock_and_failure_keeps_lease_released() -> None:
    group = UpstreamGroup(
        "backend",
        (UpstreamEndpoint("127.0.0.1", 9000),),
        max_active=1,
    )
    callback_entered = Event()
    callback_continue = Event()

    def blocking_close(_handle: int) -> None:
        callback_entered.set()
        callback_continue.wait(2)

    pool = UpstreamConnectionPool(group, close_connection=blocking_close)
    first = pool.opened(pool.reserve(1), 41)
    releaser = Thread(target=pool.release, args=(first, 2, False))
    releaser.start()
    assert callback_entered.wait(1)

    result = []
    reserve_done = Event()

    def reserve_during_close() -> None:
        result.append(pool.reserve(3))
        reserve_done.set()

    reserver = Thread(target=reserve_during_close)
    reserver.start()
    lock_was_free = reserve_done.wait(1)
    callback_continue.set()
    releaser.join()
    reserver.join()
    assert lock_was_free
    assert result[0] is not None
    pool.release(result[0], 4, reusable=False)
    assert group.active == 0

    failing_group = UpstreamGroup(
        "failing",
        (UpstreamEndpoint("127.0.0.1", 9001),),
        max_active=1,
    )

    def fail_close(_handle: int) -> None:
        raise OSError("close failed")

    failing_pool = UpstreamConnectionPool(
        failing_group, close_connection=fail_close
    )
    connection = failing_pool.opened(failing_pool.reserve(5), 55)
    with pytest.raises(OSError, match="close failed"):
        failing_pool.release(connection, 6, reusable=False)
    assert connection.released
    assert failing_group.active == 0


def test_pool_open_callback_failure_releases_fresh_group_lease() -> None:
    group = UpstreamGroup(
        "backend",
        (UpstreamEndpoint("127.0.0.1", 9000),),
        max_active=1,
    )
    pool = UpstreamConnectionPool(group)

    def fail_open(_endpoint):
        raise OSError("open failed")

    with pytest.raises(OSError, match="open failed"):
        pool.acquire(1, fail_open)
    assert group.active == 0
    assert group.endpoints[0].active == 0


def test_pool_source_uses_short_native_lock_sections_around_group_state() -> None:
    source = (REPO / "pcc" / "gateway" / "proxy_http1.py").read_text(
        encoding="utf-8"
    )

    assert "from threading import Lock" in source
    assert "def _close_locked" in source
    assert "self.group.acquire()" in source
    assert "connection.lease.release(failed=failed)" in source
    assert "Socket/provider close is an arbitrary callback" in source
    close_method = source[source.index("    def _close(self,") : source.index("    def reserve(")]
    assert close_method.index("self._lock.release()") < close_method.index(
        "self._close_handle(handle)"
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(probe="pcc1")
def test_current_pcc1_self_no_libpython_streaming_reverse_proxy(
    tmp_path: Path,
    threaded_pcc_py_runtime_archive: Path,
) -> None:
    """Compile/run proxy core; live outbound socket ownership remains open."""
    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        pytest.fail("current pcc1 is required for the proxy product gate")
    executable = tmp_path / "current_pcc1_proxy_exchange"
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    environment["PCC_RUNTIME_ARCHIVE"] = str(threaded_pcc_py_runtime_archive)
    environment["PCC_WITH_THREADS"] = "1"
    built = subprocess.run(
        [
            str(pcc1),
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(PCC1_PROXY_SOURCE),
            "-o",
            str(executable),
        ],
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run(
        [str(executable)],
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout.strip() == "PCC1_GATEWAY_PROXY_CORE_OK"
