"""Transport-neutral request, response, stream and cancellation records.

The gateway owns these records because it constructs and consumes them at the
HTTP transport boundary. :mod:`pcc.web.models` re-exports the same class
objects for the public framework API; the lower gateway layer never imports
the framework above it.
"""

import json
import threading
import pcc.virtual_thread as virtual_thread

from pcc.gateway.buffer import BufferView
from pcc.gateway.routing import normalize_path, split_target


class Cancellation:
    def __init__(self, deadline_ms: int = -1) -> None:
        self.deadline_ms = deadline_ms
        self.cancelled = False
        self.reason = ""
        self._lock = threading.Lock()

    def cancel(self, reason: str = "cancelled") -> None:
        self._lock.acquire()
        try:
            self.cancelled = True
            self.reason = reason
        finally:
            self._lock.release()

    def is_cancelled(self) -> bool:
        self._lock.acquire()
        try:
            return self.cancelled
        finally:
            self._lock.release()

    def reason_snapshot(self) -> str:
        self._lock.acquire()
        try:
            return self.reason
        finally:
            self._lock.release()

    def check(self) -> None:
        cancelled = False
        reason = ""
        self._lock.acquire()
        try:
            cancelled = self.cancelled
            reason = self.reason
        finally:
            self._lock.release()
        if cancelled:
            raise RuntimeError(reason)


class BodyStream:
    """Bounded single-reader body stream fed by the connection owner.

    ``streaming=False`` preserves the host/test polling contract: an empty but
    unfinished stream returns ``b""``.  A native gateway opts into
    ``streaming=True`` and the sole reader parks on ``threading.Event`` while
    the connection virtual thread remains the sole socket/parser owner.  The
    data and space events form a bounded high/low-watermark handoff; neither
    side ever reads the other's transport.
    """

    def __init__(
        self,
        max_bytes: int = 16777216,
        low_watermark: int = 0,
        high_watermark: int = 0,
        streaming: bool = False,
    ) -> None:
        if max_bytes < 0:
            raise ValueError("body size limit must be non-negative")
        if high_watermark <= 0:
            high_watermark = max_bytes if max_bytes > 0 else 1
        if low_watermark < 0:
            raise ValueError("body low watermark must be non-negative")
        if low_watermark >= high_watermark:
            raise ValueError("body low watermark must be below high watermark")
        self.max_bytes = max_bytes
        self.low_watermark = low_watermark
        self.high_watermark = high_watermark
        self.streaming = streaming
        self.chunks = []
        self.received = 0
        self.read_index = 0
        self.consumed_bytes = 0
        self.queued_bytes = 0
        self.backpressured = False
        self.ended = False
        self.cancelled = False
        self.closed = False
        self._lock = threading.Lock()
        self._data_ready = threading.Event()
        self._space_ready = threading.Event()
        self._space_ready.set()

    def feed(self, data) -> bool:
        """Retain one parser-owned chunk and report high-water backpressure."""
        accepted = False
        self._lock.acquire()
        try:
            if self.ended or self.cancelled or self.closed:
                raise RuntimeError("body stream is not accepting data")
            data_len = len(data)
            if self.received + data_len > self.max_bytes:
                raise ValueError("request body limit exceeded")
            if data_len > 0:
                if isinstance(data, BufferView):
                    self.chunks.append(data.slice())
                else:
                    self.chunks.append(data)
                self.received += data_len
                self.queued_bytes += data_len
                accepted = True
                if self.queued_bytes >= self.high_watermark:
                    self.backpressured = True
                    self._space_ready.clear()
        finally:
            self._lock.release()
        if accepted:
            self._data_ready.set()
        return self.backpressured

    def finish(self) -> None:
        self._lock.acquire()
        try:
            self.ended = True
        finally:
            self._lock.release()
        self._data_ready.set()
        self._space_ready.set()

    def cancel(self) -> None:
        retained = []
        self._lock.acquire()
        try:
            self.cancelled = True
            retained = self.chunks
            self.chunks = []
            self.read_index = 0
            self.queued_bytes = 0
            self.backpressured = False
        finally:
            self._lock.release()
        self._data_ready.set()
        self._space_ready.set()
        for chunk in retained:
            if isinstance(chunk, BufferView) and not chunk.released:
                chunk.release()

    def read_chunk(self):
        """Read in FIFO order; a native streaming reader parks when empty."""
        while True:
            chunk = None
            wake_space = False
            should_wait = False
            self._lock.acquire()
            try:
                if self.cancelled:
                    raise RuntimeError("request body was cancelled")
                if self.read_index < len(self.chunks):
                    chunk = self.chunks[self.read_index]
                    self.read_index += 1
                    chunk_len = len(chunk)
                    self.queued_bytes -= chunk_len
                    self.consumed_bytes += chunk_len
                    # Consumed BufferViews are released below, but retaining
                    # their Python wrappers for an entire request would make
                    # one-byte chunk streams grow without relation to the byte
                    # watermark.  Compact geometrically so queue metadata is
                    # bounded by unread entries plus a small fixed prefix.
                    if (
                        self.read_index >= 64
                        and self.read_index * 2 >= len(self.chunks)
                    ):
                        self.chunks = self.chunks[self.read_index :]
                        self.read_index = 0
                    if (
                        self.backpressured
                        and self.queued_bytes <= self.low_watermark
                    ):
                        self.backpressured = False
                        wake_space = True
                elif self.ended or self.closed:
                    return None
                elif not self.streaming:
                    return b""
                else:
                    # Clear under the state lock.  feed/finish/cancel acquire
                    # the same lock before set(), so no wake can be lost in the
                    # clear-to-wait handoff.
                    self._data_ready.clear()
                    should_wait = True
            finally:
                self._lock.release()
            if should_wait:
                self._data_ready.wait()
                continue
            if wake_space:
                self._space_ready.set()
            if isinstance(chunk, BufferView):
                result = chunk.to_bytes()
                chunk.release()
                return result
            return chunk

    def read(self, limit: int = -1) -> bytes:
        output = bytearray(b"")
        while True:
            chunk = self.read_chunk()
            if chunk is None:
                break
            if chunk == b"" and not self.streaming:
                break
            if limit >= 0 and len(output) + len(chunk) > limit:
                raise ValueError("body read limit exceeded")
            output.extend(chunk)
        return bytes(output)

    def wait_writable(self, max_wait_ms: int = -1) -> bool:
        """Wait for low-water space, optionally for one bounded timer slice.

        The live connection always supplies a remaining deadline slice.  This
        prevents a stalled handler from leaving its parent parked forever on a
        space Event which has no timer arm of its own.
        """
        if not self.streaming:
            return True
        while True:
            should_wait = False
            self._lock.acquire()
            try:
                if self.cancelled or self.closed or self.ended:
                    return False
                if not self.backpressured:
                    return True
                self._space_ready.clear()
                should_wait = True
            finally:
                self._lock.release()
            if should_wait:
                if max_wait_ms < 0:
                    self._space_ready.wait()
                    continue
                if max_wait_ms == 0:
                    return False
                delay_ms = max_wait_ms
                if delay_ms > 25:
                    delay_ms = 25
                virtual_thread.sleep_current(delay_ms)
                self._lock.acquire()
                try:
                    return not self.backpressured
                finally:
                    self._lock.release()

    def is_backpressured(self) -> bool:
        """Lock-protected observation for diagnostics; producers should wait."""
        self._lock.acquire()
        try:
            return self.backpressured
        finally:
            self._lock.release()

    def producer_finished(self) -> bool:
        """Report whether no later body bytes can arrive under the state lock."""
        self._lock.acquire()
        try:
            return self.ended or self.cancelled or self.closed
        finally:
            self._lock.release()

    def is_ended(self) -> bool:
        self._lock.acquire()
        try:
            return self.ended
        finally:
            self._lock.release()

    def queued_size(self) -> int:
        self._lock.acquire()
        try:
            return self.queued_bytes
        finally:
            self._lock.release()

    def consumed_size(self) -> int:
        self._lock.acquire()
        try:
            return self.consumed_bytes
        finally:
            self._lock.release()

    def close(self) -> None:
        """Release unread retained views after handler/proxy completion."""
        retained = []
        index = 0
        self._lock.acquire()
        try:
            if self.closed:
                return
            self.closed = True
            retained = self.chunks
            index = self.read_index
            self.chunks = []
            self.read_index = 0
            self.queued_bytes = 0
            self.backpressured = False
        finally:
            self._lock.release()
        self._data_ready.set()
        self._space_ready.set()
        while index < len(retained):
            chunk = retained[index]
            if isinstance(chunk, BufferView) and not chunk.released:
                chunk.release()
            index += 1


def _percent_decode(value: str, plus_space: bool) -> str:
    output = bytearray(b"")
    encoded = value.encode("utf-8")
    index = 0
    while index < len(encoded):
        current = encoded[index]
        if current == 37:
            if index + 2 >= len(encoded):
                raise ValueError("truncated percent escape")
            raw = bytes(encoded[index + 1:index + 3])
            try:
                output.append(int(raw.decode("ascii"), 16))
            except ValueError as error:
                raise ValueError("invalid percent escape") from error
            index += 3
        elif plus_space and current == 43:
            output.append(32)
            index += 1
        else:
            output.append(current)
            index += 1
    return bytes(output).decode("utf-8")


def parse_query(query: str, max_fields: int = 128):
    output = {}
    if not query:
        return output
    fields = query.split("&")
    if len(fields) > max_fields:
        raise ValueError("query field limit exceeded")
    for field in fields:
        if "=" in field:
            key, value = field.split("=", 1)
        else:
            key, value = field, ""
        key = _percent_decode(key, True)
        value = _percent_decode(value, True)
        if key in output:
            output[key].append(value)
        else:
            output[key] = [value]
    return output


class Request:
    def __init__(
        self,
        method: str,
        target: str,
        version: str = "HTTP/1.1",
        headers=None,
        body=None,
        client_ip: str = "",
        scheme: str = "http",
        cancellation=None,
        content_length: int = 0,
        chunked_body: bool = False,
        expect_continue_handled: bool = False,
    ) -> None:
        if headers is None:
            headers = []
        if body is None:
            body = BodyStream()
            body.finish()
        if cancellation is None:
            cancellation = Cancellation()
        raw_path, query = split_target(target)
        self.method = method.upper()
        self.target = target
        self.raw_path = raw_path
        self.path = normalize_path(raw_path)
        self.query_string = query
        self.version = version
        self.headers = list(headers)
        self.body = body
        self.client_ip = client_ip
        self.scheme = scheme
        self.cancellation = cancellation
        # The parser-owned framing contract is immutable request metadata.
        # Keeping it on the declared object shape lets an early proxy stream
        # bytes before ``BodyStream.received`` reaches the final total.
        self.content_length = content_length
        self.chunked_body = chunked_body
        self.expect_continue_handled = expect_continue_handled
        self.trailers = []
        self.path_params = {}
        self.context = {}
        self._query = None

    def read_body(self, limit: int = -1) -> bytes:
        """Canonical compiled handler boundary for consuming the body."""
        return virtual_thread.call(self.body.read, limit)

    def read_body_chunk(self):
        """Canonical compiled handler boundary for incremental consumption."""
        return virtual_thread.call(self.body.read_chunk)

    def header(self, name: str, default=None):
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return default

    @property
    def query(self):
        if self._query is None:
            self._query = parse_query(self.query_string)
        return self._query


class Response:
    def __init__(self, status: int = 200, body=b"", headers=None, streaming: bool = False) -> None:
        # pcc.web Response represents one terminal framework response.  1xx
        # messages (including unsupported 101 upgrade) are protocol control
        # frames, not handler terminal values, and would corrupt pipeline
        # response ordering if accepted here.
        if status < 200 or status > 599:
            raise ValueError("final HTTP response status must be 200..599")
        if headers is None:
            headers = []
        self.status = status
        self.body = body
        self.headers = list(headers)
        self.streaming = streaming
        self.committed = False

    @classmethod
    def bytes(cls, body: bytes, status: int = 200, headers=None):
        return cls(status, body, headers)

    @classmethod
    def text(cls, body: str, status: int = 200, headers=None):
        if headers is None:
            headers = []
        headers = list(headers)
        headers.append(("content-type", "text/plain; charset=utf-8"))
        return cls(status, body.encode("utf-8"), headers)

    @classmethod
    def json(cls, value, status: int = 200, headers=None):
        if headers is None:
            headers = []
        headers = list(headers)
        headers.append(("content-type", "application/json"))
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return cls(status, body, headers)

    @classmethod
    def redirect(cls, location: str, status: int = 307):
        return cls(status, b"", [("location", location)])

    @classmethod
    def stream(cls, chunks, status: int = 200, headers=None):
        return cls(status, chunks, headers, True)


class HttpError(Exception):
    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
