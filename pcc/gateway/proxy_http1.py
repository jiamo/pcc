"""Bounded sans-I/O HTTP/1 reverse-proxy transport state.

The live socket owner drives this module with nonblocking reads and writes.
This module owns the protocol state that must remain identical for poll,
kqueue and future epoll transports: request framing, response parsing,
bidirectional watermarks, cancellation, deadlines and pre-commit retry.

It deliberately does not open a socket or resolve a hostname.  Those are
separate pcc-owned operations, never hidden blocking host calls.
"""

from threading import Lock

from .buffer import ChannelBuffer
from .http1 import (
    Http1Error,
    Http1ResponseEncoder,
    _contains_bad_value_byte,
    _find_crlf,
    _find_double_crlf,
    _is_token,
    _parse_chunk_size_line,
    _parse_headers,
    _split_commas,
)
from .proxy import forwarded_request_headers, sanitize_hop_by_hop


PROXY_STAGE_CONNECT = "connect"
PROXY_STAGE_REQUEST_BODY = "request-body"
PROXY_STAGE_HEADER = "header"
PROXY_STAGE_BODY = "body"
PROXY_STAGE_IDLE = "idle"
PROXY_STAGE_DONE = "done"


class ProxyProtocolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UpstreamResponseHead:
    def __init__(
        self,
        status: int,
        reason: str,
        version: str,
        headers,
        content_length: int,
        chunked: bool,
        keep_alive: bool,
        informational: bool,
        body_expected: bool,
    ) -> None:
        self.status = status
        self.reason = reason
        self.version = version
        self.headers = headers
        self.content_length = content_length
        self.chunked = chunked
        self.keep_alive = keep_alive
        self.informational = informational
        self.body_expected = body_expected


class UpstreamResponseBody:
    def __init__(self, data: bytes) -> None:
        self.data = data


class UpstreamResponseEnd:
    def __init__(self, trailers=None) -> None:
        if trailers is None:
            trailers = []
        self.trailers = trailers


class Http1UpstreamCodec:
    """Decode exactly one final upstream response, plus informational heads."""

    def __init__(
        self,
        request_method: str,
        max_header_bytes: int = 32768,
        max_header_count: int = 100,
        max_body_bytes: int = 16777216,
        max_chunk_bytes: int = 1048576,
    ) -> None:
        if max_header_bytes <= 0 or max_header_count <= 0:
            raise ValueError("upstream header limits must be positive")
        self.request_method = request_method.upper()
        self.max_header_bytes = max_header_bytes
        self.max_header_count = max_header_count
        self.max_body_bytes = max_body_bytes
        self.max_chunk_bytes = max_chunk_bytes
        self.buffer = bytearray(b"")
        self.state = "HEAD"
        self.remaining = 0
        self.body_received = 0
        self.complete = False
        self.closed = False

    def _finish(self, events, trailers=None) -> None:
        events.append(UpstreamResponseEnd(trailers))
        self.state = "COMPLETE"
        self.complete = True

    def _parse_head(self, block: bytes):
        lines = block.split(b"\r\n")
        if not lines or not lines[0]:
            raise ProxyProtocolError("empty-status-line", "empty upstream status line")
        parts = lines[0].split(b" ", 2)
        if len(parts) < 2:
            raise ProxyProtocolError("bad-status-line", "invalid upstream status line")
        version_bytes = parts[0]
        status_bytes = parts[1]
        reason_bytes = b"" if len(parts) == 2 else parts[2]
        if version_bytes not in (b"HTTP/1.0", b"HTTP/1.1"):
            raise ProxyProtocolError("bad-version", "unsupported upstream HTTP version")
        if len(status_bytes) != 3:
            raise ProxyProtocolError("bad-status", "upstream status must be three digits")
        for value in status_bytes:
            if value < 48 or value > 57:
                raise ProxyProtocolError("bad-status", "upstream status is not decimal")
        if _contains_bad_value_byte(reason_bytes):
            raise ProxyProtocolError("bad-reason", "unsafe upstream reason phrase")
        status = int(status_bytes.decode("ascii"))
        if status < 100 or status > 999:
            raise ProxyProtocolError("bad-status", "upstream status is out of range")
        try:
            headers, names = _parse_headers(lines[1:], self.max_header_count)
        except Http1Error as error:
            raise ProxyProtocolError("bad-header", str(error)) from error

        content_length = -1
        if "content-length" in names:
            lengths = names["content-length"]
            if len(lengths) != 1 or "," in lengths[0] or not lengths[0]:
                raise ProxyProtocolError("ambiguous-length", "ambiguous upstream length")
            for character in lengths[0]:
                if character < "0" or character > "9":
                    raise ProxyProtocolError("bad-length", "invalid upstream length")
            content_length = int(lengths[0])
            if content_length > self.max_body_bytes:
                raise ProxyProtocolError("body-too-large", "upstream body limit exceeded")

        chunked = False
        if "transfer-encoding" in names:
            if content_length >= 0:
                raise ProxyProtocolError(
                    "ambiguous-framing",
                    "upstream sent both Transfer-Encoding and Content-Length",
                )
            codings = []
            for value in names["transfer-encoding"]:
                codings.extend(_split_commas(value))
            if codings != ["chunked"]:
                raise ProxyProtocolError(
                    "unsupported-transfer-coding",
                    "only final chunked upstream coding is supported",
                )
            chunked = True

        connection_tokens = []
        if "connection" in names:
            for value in names["connection"]:
                connection_tokens.extend(_split_commas(value))
        version = version_bytes.decode("ascii")
        keep_alive = version == "HTTP/1.1"
        if "close" in connection_tokens:
            keep_alive = False
        elif version == "HTTP/1.0" and "keep-alive" in connection_tokens:
            keep_alive = True

        informational = 100 <= status < 200
        if status == 101:
            raise ProxyProtocolError("upgrade-rejected", "HTTP upgrade is not supported")
        body_expected = not (
            self.request_method == "HEAD"
            or informational
            or status == 204
            or status == 304
        )
        head = UpstreamResponseHead(
            status,
            reason_bytes.decode("latin1"),
            version,
            headers,
            content_length,
            chunked,
            keep_alive,
            informational,
            body_expected,
        )
        if informational:
            self.state = "HEAD"
        elif not body_expected or content_length == 0:
            self.state = "FINAL_EMPTY"
        elif chunked:
            self.state = "CHUNK_SIZE"
        elif content_length >= 0:
            self.remaining = content_length
            self.state = "FIXED_BODY"
        else:
            # EOF framing cannot be returned to an idle keep-alive pool.
            head.keep_alive = False
            self.state = "UNTIL_EOF"
        return head

    def feed(self, data: bytes):
        if self.closed or self.complete:
            if data:
                raise ProxyProtocolError("response-complete", "bytes after upstream response")
            return []
        self.buffer.extend(data)
        events = []
        while True:
            if self.state == "HEAD":
                marker = _find_double_crlf(self.buffer)
                if marker < 0:
                    if len(self.buffer) > self.max_header_bytes:
                        raise ProxyProtocolError("headers-too-large", "upstream headers exceed limit")
                    break
                if marker + 4 > self.max_header_bytes:
                    raise ProxyProtocolError("headers-too-large", "upstream headers exceed limit")
                block = bytes(self.buffer[:marker])
                del self.buffer[:marker + 4]
                head = self._parse_head(block)
                events.append(head)
                if head.informational:
                    continue
                if self.state == "FINAL_EMPTY":
                    self._finish(events)
                    if self.buffer:
                        raise ProxyProtocolError("pipelined-response", "unexpected response bytes")
                    break
            elif self.state == "FIXED_BODY":
                if not self.buffer:
                    break
                take = min(self.remaining, len(self.buffer))
                chunk = bytes(self.buffer[:take])
                del self.buffer[:take]
                self.remaining -= take
                self.body_received += take
                if chunk:
                    events.append(UpstreamResponseBody(chunk))
                if self.remaining == 0:
                    self._finish(events)
                    if self.buffer:
                        raise ProxyProtocolError("pipelined-response", "unexpected response bytes")
                    break
            elif self.state == "CHUNK_SIZE":
                marker = _find_crlf(self.buffer)
                if marker < 0:
                    if len(self.buffer) > 128:
                        raise ProxyProtocolError("chunk-line-too-long", "upstream chunk line exceeds limit")
                    break
                if marker > 128:
                    raise ProxyProtocolError("chunk-line-too-long", "upstream chunk line exceeds limit")
                line = bytes(self.buffer[:marker])
                del self.buffer[:marker + 2]
                try:
                    size = _parse_chunk_size_line(line)
                except Http1Error as error:
                    raise ProxyProtocolError(error.code, str(error)) from error
                if size > self.max_chunk_bytes:
                    raise ProxyProtocolError("chunk-too-large", "upstream chunk limit exceeded")
                if self.body_received + size > self.max_body_bytes:
                    raise ProxyProtocolError("body-too-large", "upstream body limit exceeded")
                if size == 0:
                    self.state = "TRAILERS"
                else:
                    self.remaining = size
                    self.state = "CHUNK_DATA"
            elif self.state == "CHUNK_DATA":
                if not self.buffer:
                    break
                take = min(self.remaining, len(self.buffer))
                chunk = bytes(self.buffer[:take])
                del self.buffer[:take]
                self.remaining -= take
                self.body_received += take
                if chunk:
                    events.append(UpstreamResponseBody(chunk))
                if self.remaining == 0:
                    self.state = "CHUNK_CRLF"
            elif self.state == "CHUNK_CRLF":
                if len(self.buffer) < 2:
                    break
                if self.buffer[:2] != b"\r\n":
                    raise ProxyProtocolError("bad-chunk-ending", "invalid upstream chunk ending")
                del self.buffer[:2]
                self.state = "CHUNK_SIZE"
            elif self.state == "TRAILERS":
                if len(self.buffer) >= 2 and self.buffer[:2] == b"\r\n":
                    del self.buffer[:2]
                    self._finish(events, [])
                    if self.buffer:
                        raise ProxyProtocolError("pipelined-response", "unexpected response bytes")
                    break
                marker = _find_double_crlf(self.buffer)
                if marker < 0:
                    if len(self.buffer) > self.max_header_bytes:
                        raise ProxyProtocolError("trailers-too-large", "upstream trailers exceed limit")
                    break
                if marker + 4 > self.max_header_bytes:
                    raise ProxyProtocolError(
                        "trailers-too-large",
                        "upstream trailers exceed limit",
                    )
                block = bytes(self.buffer[:marker])
                del self.buffer[:marker + 4]
                try:
                    trailers, names = _parse_headers(
                        block.split(b"\r\n"), self.max_header_count
                    )
                except Http1Error as error:
                    raise ProxyProtocolError("bad-trailer", str(error)) from error
                for forbidden in ("content-length", "transfer-encoding"):
                    if forbidden in names:
                        raise ProxyProtocolError("bad-trailer", "framing field in upstream trailer")
                self._finish(events, sanitize_hop_by_hop(trailers))
                if self.buffer:
                    raise ProxyProtocolError("pipelined-response", "unexpected response bytes")
                break
            elif self.state == "UNTIL_EOF":
                if not self.buffer:
                    break
                self.body_received += len(self.buffer)
                if self.body_received > self.max_body_bytes:
                    raise ProxyProtocolError("body-too-large", "upstream body limit exceeded")
                events.append(UpstreamResponseBody(bytes(self.buffer)))
                self.buffer = bytearray(b"")
                break
            elif self.state == "COMPLETE":
                break
            else:
                raise ProxyProtocolError("codec-state", "unknown upstream codec state")
        return events

    def eof(self):
        self.closed = True
        if self.state == "UNTIL_EOF":
            events = self.feed(b"") if not self.complete else []
            self._finish(events)
            return events
        if self.complete:
            return []
        raise ProxyProtocolError("incomplete-response", "upstream closed during response")


def encode_proxy_request_head(
    method: str,
    target: str,
    headers,
    upstream_authority: str,
    client_ip: str,
    scheme: str,
    original_host: str,
    content_length: int = 0,
    chunked: bool = False,
    trust_forwarded: bool = False,
    expect_continue_handled: bool = False,
) -> bytes:
    method_bytes = method.encode("ascii")
    target_bytes = target.encode("latin1")
    if not _is_token(method_bytes) or not target_bytes:
        raise ProxyProtocolError("bad-request", "invalid proxy request line")
    for value in target_bytes:
        if value <= 32 or value == 127:
            raise ProxyProtocolError("bad-target", "unsafe proxy target")
    if not upstream_authority:
        raise ProxyProtocolError("bad-upstream", "upstream authority is required")
    if content_length < 0 and not chunked:
        raise ProxyProtocolError("unknown-framing", "unknown request length must be chunked")
    if content_length >= 0 and chunked:
        raise ProxyProtocolError("ambiguous-framing", "proxy request framing is ambiguous")

    forwarded = forwarded_request_headers(
        headers, client_ip, scheme, original_host, trust_forwarded
    )
    output_headers = [("host", upstream_authority)]
    for name, value in forwarded:
        lower_name = name.lower()
        if lower_name in ("host", "content-length", "transfer-encoding"):
            continue
        if expect_continue_handled and lower_name == "expect":
            continue
        output_headers.append((name, value))
    output = bytearray(method_bytes + b" " + target_bytes + b" HTTP/1.1\r\n")
    for name, value in output_headers:
        name_bytes = name.encode("ascii")
        value_bytes = value.encode("latin1")
        if not _is_token(name_bytes) or _contains_bad_value_byte(value_bytes):
            raise ProxyProtocolError("bad-header", "unsafe proxy request header")
        output.extend(name_bytes)
        output.extend(b": ")
        output.extend(value_bytes)
        output.extend(b"\r\n")
    if chunked:
        output.extend(b"transfer-encoding: chunked\r\n")
    else:
        output.extend(("content-length: " + str(content_length) + "\r\n").encode("ascii"))
    output.extend(b"\r\n")
    return bytes(output)


class ProxyDeadline:
    """One absolute stage deadline; updates never extend an earlier cancel."""

    def __init__(self, timeouts, now_ms: int) -> None:
        self.timeouts = timeouts
        self.stage = PROXY_STAGE_CONNECT
        self.deadline_ms = now_ms + timeouts.connect_ms
        self.cancelled = False

    def connected(self, now_ms: int) -> None:
        self.stage = PROXY_STAGE_HEADER
        self.deadline_ms = now_ms + self.timeouts.header_ms

    def request_body(self, deadline_ms: int) -> None:
        """Use the downstream body's absolute budget until it is fully sent."""
        self.stage = PROXY_STAGE_REQUEST_BODY
        self.deadline_ms = deadline_ms

    def response_head(self, now_ms: int, body_expected: bool) -> None:
        if body_expected:
            self.stage = PROXY_STAGE_BODY
            self.deadline_ms = now_ms + self.timeouts.body_ms
        else:
            self.stage = PROXY_STAGE_DONE
            self.deadline_ms = -1

    def body_progress(self, now_ms: int) -> None:
        if self.stage == PROXY_STAGE_BODY:
            self.deadline_ms = now_ms + self.timeouts.idle_ms

    def finish(self) -> None:
        self.stage = PROXY_STAGE_DONE
        self.deadline_ms = -1

    def cancel(self) -> None:
        self.cancelled = True
        self.deadline_ms = -1

    def expired(self, now_ms: int) -> bool:
        return not self.cancelled and self.deadline_ms >= 0 and now_ms >= self.deadline_ms

    def failure(self) -> str:
        if self.stage == PROXY_STAGE_CONNECT:
            return "connect-timeout"
        if self.stage == PROXY_STAGE_REQUEST_BODY:
            return "request-body-timeout"
        if self.stage == PROXY_STAGE_HEADER:
            return "header-timeout"
        return "body-timeout"


class ProxyExchange:
    """One request/response flow with independent bounded directions."""

    def __init__(
        self,
        request_method: str,
        request_target: str,
        request_headers,
        upstream_authority: str,
        client_ip: str,
        scheme: str,
        original_host: str,
        content_length: int = 0,
        chunked_request: bool = False,
        trust_forwarded: bool = False,
        expect_continue_handled: bool = False,
        segment_bytes: int = 16384,
        low_watermark: int = -1,
        high_watermark: int = -1,
        max_buffered_bytes: int = 1048576,
        accounting=None,
    ) -> None:
        if max_buffered_bytes <= 0:
            raise ValueError("max buffered bytes must be positive")
        # Derive defaults from the caller's actual bound.  Fixed 32/64 KiB
        # defaults make every smaller, otherwise-valid bounded exchange fail
        # during ChannelBuffer construction.  Explicit non-sentinel values
        # still pass through ChannelBuffer's strict invariant checks.
        if high_watermark == -1:
            high_watermark = min(65536, max_buffered_bytes)
        if low_watermark == -1:
            low_watermark = min(32768, high_watermark // 2)
        self.method = request_method.upper()
        self.content_length = content_length
        self.chunked_request = chunked_request
        self.expect_continue_handled = expect_continue_handled
        self.request_sent = 0
        self.request_finished = False
        self.response_committed = False
        self.response_status = 0
        self.response_finished = False
        self.cancelled = False
        self.cancel_reason = ""
        self.upstream_keep_alive = False
        self.downstream_chunked = False
        self.accounting = accounting
        self.encoder = Http1ResponseEncoder()
        self.codec = Http1UpstreamCodec(self.method)
        self.to_upstream = ChannelBuffer(
            segment_bytes, low_watermark, high_watermark, max_buffered_bytes
        )
        self.to_downstream = ChannelBuffer(
            segment_bytes, low_watermark, high_watermark, max_buffered_bytes
        )
        head = encode_proxy_request_head(
            self.method,
            request_target,
            request_headers,
            upstream_authority,
            client_ip,
            scheme,
            original_host,
            content_length,
            chunked_request,
            trust_forwarded,
            expect_continue_handled,
        )
        self._append_upstream(head)

    def _reserve(self, count: int) -> None:
        if count <= 0 or self.accounting is None:
            return
        if not self.accounting.reserve_buffered(count):
            raise ProxyProtocolError(
                "buffer-overload",
                "gateway global buffered-byte limit exceeded",
            )

    def _release(self, count: int) -> None:
        if count > 0 and self.accounting is not None:
            self.accounting.release_buffered(count)

    def _append_upstream(self, data) -> int:
        count = len(data)
        self._reserve(count)
        try:
            if hasattr(data, "to_bytes"):
                return self.to_upstream.append_view(data)
            return self.to_upstream.append(data)
        except Exception:
            self._release(count)
            raise

    def feed_request_body(self, data) -> int:
        if self.cancelled or self.request_finished:
            raise RuntimeError("proxy request is not writable")
        if not self.chunked_request and self.request_sent + len(data) > self.content_length:
            raise ProxyProtocolError("request-overflow", "proxy request exceeds declared length")
        self.request_sent += len(data)
        framed = data
        if self.chunked_request and data:
            if hasattr(data, "to_bytes"):
                data = data.to_bytes()
            framed = format(len(data), "x").encode("ascii") + b"\r\n" + data + b"\r\n"
        if not self.chunked_request and hasattr(framed, "to_bytes"):
            return self._append_upstream(framed)
        return self._append_upstream(framed)

    def finish_request(self, trailers=None) -> int:
        if self.request_finished:
            return 0
        if not self.chunked_request and self.request_sent != self.content_length:
            raise ProxyProtocolError("request-underflow", "proxy request ended before declared length")
        self.request_finished = True
        if not self.chunked_request:
            return 0
        if trailers is None:
            trailers = []
        ending = bytearray(b"0\r\n")
        for name, value in sanitize_hop_by_hop(trailers):
            lower = name.lower()
            if lower in ("content-length", "transfer-encoding", "host"):
                raise ProxyProtocolError("bad-trailer", "forbidden proxy request trailer")
            ending.extend(lower.encode("ascii") + b": " + value.encode("latin1") + b"\r\n")
        ending.extend(b"\r\n")
        return self._append_upstream(bytes(ending))

    def take_upstream(self, limit: int = -1):
        was_paused = self.to_upstream.backpressured
        data = self.to_upstream.read(limit)
        self._release(len(data))
        resumed = was_paused and not self.to_upstream.backpressured
        return data, resumed

    def _append_downstream(self, data: bytes) -> int:
        if not data:
            return 0
        self._reserve(len(data))
        try:
            return self.to_downstream.append(data)
        except Exception:
            self._release(len(data))
            raise

    def feed_upstream(self, data: bytes) -> int:
        if self.cancelled or self.response_finished:
            raise RuntimeError("proxy response is not readable")
        transition = 0
        for event in self.codec.feed(data):
            if isinstance(event, UpstreamResponseHead):
                sanitized_headers = sanitize_hop_by_hop(event.headers)
                headers = []
                for name, value in sanitized_headers:
                    if name.lower() not in (
                        "content-length",
                        "transfer-encoding",
                    ):
                        headers.append((name, value))
                if event.informational:
                    # The gateway may already have emitted the one permitted
                    # 100 response before streaming the client body.  Do not
                    # duplicate it if an upstream sends a stray second one.
                    if event.status == 100 and self.expect_continue_handled:
                        continue
                    # Other informational responses are safe; upgrades were
                    # rejected by the response codec.
                    transition = self._append_downstream(
                        self.encoder.head(event.status, headers)
                    ) or transition
                    continue
                self.response_committed = True
                self.response_status = event.status
                self.upstream_keep_alive = event.keep_alive
                self.downstream_chunked = event.body_expected and event.content_length < 0
                length = event.content_length
                if not event.body_expected:
                    # HEAD and 304 may describe the selected representation's
                    # length even though no payload follows. Informational and
                    # 204 responses must not forward framing fields.
                    if self.method != "HEAD" and event.status != 304:
                        length = -1
                transition = self._append_downstream(
                    self.encoder.head(
                        event.status,
                        headers,
                        content_length=length if not self.downstream_chunked else -1,
                        chunked=self.downstream_chunked,
                    )
                ) or transition
            elif isinstance(event, UpstreamResponseBody):
                payload = event.data
                if self.downstream_chunked:
                    payload = self.encoder.chunk(payload)
                transition = self._append_downstream(payload) or transition
            elif isinstance(event, UpstreamResponseEnd):
                if self.downstream_chunked:
                    transition = self._append_downstream(
                        self.encoder.end_chunks(event.trailers)
                    ) or transition
                self.response_finished = True
        return transition

    def upstream_eof(self) -> int:
        transition = 0
        for event in self.codec.eof():
            if isinstance(event, UpstreamResponseBody):
                payload = self.encoder.chunk(event.data) if self.downstream_chunked else event.data
                transition = self._append_downstream(payload) or transition
            elif isinstance(event, UpstreamResponseEnd):
                if self.downstream_chunked:
                    transition = self._append_downstream(self.encoder.end_chunks()) or transition
                self.response_finished = True
        return transition

    def take_downstream(self, limit: int = -1):
        was_paused = self.to_downstream.backpressured
        data = self.to_downstream.read(limit)
        self._release(len(data))
        resumed = was_paused and not self.to_downstream.backpressured
        return data, resumed

    def cancel(self, reason: str) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        self.cancel_reason = reason
        remaining = len(self.to_upstream) + len(self.to_downstream)
        self.to_upstream.close()
        self.to_downstream.close()
        self._release(remaining)

    def can_retry(self, retry_policy, attempt: int, failure: str) -> bool:
        return retry_policy.allows(
            self.method,
            attempt,
            self.response_committed,
            failure,
            self.request_sent == 0,
        )


class PooledUpstreamConnection:
    def __init__(self, handle: int, lease, created_ms: int) -> None:
        self.handle = handle
        self.lease = lease
        self.created_ms = created_ms
        self.last_used_ms = created_ms
        self.connected = False
        self.released = False

    @property
    def needs_open(self) -> bool:
        return self.handle < 0


class UpstreamConnectionPool:
    """Bounded keep-alive ownership around an ``UpstreamGroup`` lease."""

    def __init__(
        self,
        group,
        idle_timeout_ms: int = 30000,
        close_connection=None,
    ) -> None:
        if idle_timeout_ms <= 0:
            raise ValueError("upstream idle timeout must be positive")
        self._lock = Lock()
        self.group = group
        self.idle_timeout_ms = idle_timeout_ms
        self.idle = []
        self.close_connection = close_connection

    def accept_and_order_addresses(self, endpoint, values, qtype: int):
        """Linearize endpoint policy through the shared group owner lock."""
        self._lock.acquire()
        try:
            return self.group.accept_and_order_addresses(endpoint, values, qtype)
        finally:
            self._lock.release()

    def saturated(self) -> bool:
        self._lock.acquire()
        try:
            return self.group.saturated()
        finally:
            self._lock.release()

    def _close_locked(self, connection, failed: bool) -> int:
        """Release pool/group ownership and return a handle to close outside."""

        if connection.released:
            return -1
        connection.released = True
        handle = connection.handle
        # UpstreamLease mutates endpoint.active and group.active.  Every lease
        # created by this pool is released under the same pool lock.
        connection.lease.release(failed=failed)
        return handle

    def _close_handle(self, handle: int) -> None:
        if self.close_connection is not None and handle >= 0:
            self.close_connection(handle)

    def _close(self, connection, failed: bool) -> None:
        handle = -1
        self._lock.acquire()
        try:
            handle = self._close_locked(connection, failed)
        finally:
            self._lock.release()
        # Socket/provider close is an arbitrary callback and may block.  Pool
        # and group ownership were already released under the lock.
        self._close_handle(handle)

    def reserve(self, now_ms: int):
        """Return an idle connection or reserve one endpoint lease.

        A fresh reservation has handle ``-1``.  The top-level proxy driver can
        then resolve its endpoint while parking without hiding a park inside a
        pool class method, open the numeric destination, and call
        :meth:`opened`.
        """
        while True:
            connection = None
            expired_handle = -1
            self._lock.acquire()
            try:
                if self.idle:
                    connection = self.idle.pop()
                    if (
                        not connection.released
                        and now_ms - connection.last_used_ms
                        < self.idle_timeout_ms
                    ):
                        return connection
                    expired_handle = self._close_locked(connection, False)
                else:
                    lease = self.group.acquire()
                    if lease is None:
                        return None
                    return PooledUpstreamConnection(-1, lease, now_ms)
            finally:
                self._lock.release()
            # Close one expired descriptor at a time outside the lock.  If the
            # callback fails, its lease is nevertheless already released and
            # no freshly reserved lease is leaked.
            self._close_handle(expired_handle)

    def opened(self, connection, handle: int):
        if handle < 0:
            self._close(connection, True)
            return None
        invalid = False
        self._lock.acquire()
        try:
            if connection.released or connection.handle >= 0:
                invalid = True
            else:
                connection.handle = handle
        finally:
            self._lock.release()
        if invalid:
            # The caller transferred this newly opened handle to the pool.
            # Close it even when a concurrent cancellation won the race.
            self._close_handle(handle)
            raise RuntimeError("upstream reservation is not openable")
        return connection

    def acquire(self, now_ms: int, open_connection):
        """Compatibility one-step acquire for already numeric endpoints."""
        connection = self.reserve(now_ms)
        if connection is None or not connection.needs_open:
            return connection
        try:
            handle = open_connection(connection.lease.endpoint)
        except Exception:
            self._close(connection, True)
            raise
        return self.opened(connection, handle)

    def release(self, connection, now_ms: int, reusable: bool) -> bool:
        close_handle = -1
        self._lock.acquire()
        try:
            if connection.released:
                raise RuntimeError("upstream connection released more than once")
            connection.last_used_ms = now_ms
            if (
                connection.handle >= 0
                and reusable
                and len(self.idle) < self.group.max_idle
            ):
                self.idle.append(connection)
                return True
            close_handle = self._close_locked(connection, not reusable)
        finally:
            self._lock.release()
        self._close_handle(close_handle)
        return False

    def close_idle(self) -> int:
        handles = []
        closed = 0
        self._lock.acquire()
        try:
            idle = self.idle
            self.idle = []
            for connection in idle:
                if not connection.released:
                    handles.append(self._close_locked(connection, False))
                    closed += 1
        finally:
            self._lock.release()

        # Attempt every descriptor close even if one callback fails.  All
        # group/lease state was already made consistent under the lock.
        first_error = None
        for handle in handles:
            try:
                self._close_handle(handle)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise RuntimeError("upstream idle close callback failed") from first_error
        return closed


__all__ = [
    "PROXY_STAGE_CONNECT",
    "PROXY_STAGE_REQUEST_BODY",
    "PROXY_STAGE_HEADER",
    "PROXY_STAGE_BODY",
    "PROXY_STAGE_IDLE",
    "PROXY_STAGE_DONE",
    "ProxyProtocolError",
    "UpstreamResponseHead",
    "UpstreamResponseBody",
    "UpstreamResponseEnd",
    "Http1UpstreamCodec",
    "encode_proxy_request_head",
    "ProxyDeadline",
    "ProxyExchange",
    "PooledUpstreamConnection",
    "UpstreamConnectionPool",
]
