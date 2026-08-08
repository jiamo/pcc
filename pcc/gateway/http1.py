"""Bounded sans-I/O HTTP/1.0 and HTTP/1.1 server codec.

Transport code feeds bytes and consumes typed events.  The codec performs no
socket calls, owns no event loop and has no host-framework dependency.  Its
framing checks deliberately fail closed before handler dispatch.
"""

from .buffer import BufferSegment


class Http1Error(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class RequestHead:
    def __init__(
        self,
        method: str,
        target: str,
        version: str,
        headers,
        keep_alive: bool,
        expect_continue: bool,
        content_length: int = 0,
        chunked: bool = False,
    ) -> None:
        self.method = method
        self.target = target
        self.version = version
        self.headers = headers
        self.keep_alive = keep_alive
        self.expect_continue = expect_continue
        self.content_length = content_length
        self.chunked = chunked

    def header(self, name: str, default=None):
        wanted = name.lower()
        for key, value in self.headers:
            if key == wanted:
                return value
        return default


class BodyChunk:
    """One retained body view; ``data`` remains a compatibility accessor."""

    def __init__(self, data) -> None:
        if hasattr(data, "to_bytes") and hasattr(data, "release"):
            self.view = data
        else:
            segment = BufferSegment(len(data) if len(data) > 0 else 1)
            segment.write(data)
            self.view = segment.view(0, len(data))
            segment.release()
        self.released = False

    @property
    def data(self) -> bytes:
        return self.view.to_bytes()

    def release(self) -> int:
        if self.released:
            return 0
        self.released = True
        return self.view.release()


class RequestEnd:
    def __init__(self, trailers=None) -> None:
        if trailers is None:
            trailers = []
        self.trailers = trailers


class ConnectionClosed:
    def __init__(self, reason: str) -> None:
        self.reason = reason


_TOKEN_EXTRA = b"!#$%&'*+-.^_`|~"


def _is_token(data: bytes) -> bool:
    if len(data) == 0:
        return False
    for value in data:
        if 48 <= value <= 57 or 65 <= value <= 90 or 97 <= value <= 122:
            continue
        if value in _TOKEN_EXTRA:
            continue
        return False
    return True


def _contains_bad_value_byte(data: bytes) -> bool:
    for value in data:
        if value == 0 or value == 10 or value == 13 or value == 127:
            return True
        if value < 32 and value != 9:
            return True
    return False


def _find_double_crlf(data: bytearray) -> int:
    index = 0
    limit = len(data) - 3
    while index < limit:
        if (
            data[index] == 13
            and data[index + 1] == 10
            and data[index + 2] == 13
            and data[index + 3] == 10
        ):
            return index
        index += 1
    return -1


def _find_crlf(data: bytearray) -> int:
    index = 0
    while index + 1 < len(data):
        if data[index] == 13 and data[index + 1] == 10:
            return index
        index += 1
    return -1


def _split_commas(value: str):
    output = []
    for item in value.split(","):
        token = item.strip().lower()
        if token:
            output.append(token)
    return output


def _quoted_chunk_extension_value(value: bytes) -> bool:
    if len(value) < 2 or value[0] != 34 or value[-1] != 34:
        return False
    index = 1
    end = len(value) - 1
    while index < end:
        current = value[index]
        if current == 92:
            index += 1
            if index >= end:
                return False
            current = value[index]
            if not (
                current == 9
                or current == 32
                or 33 <= current <= 126
                or current >= 128
            ):
                return False
        elif not (
            current == 9
            or current == 32
            or current == 33
            or 35 <= current <= 91
            or 93 <= current <= 126
            or current >= 128
        ):
            return False
        index += 1
    return True


def _parse_chunk_size_line(line: bytes) -> int:
    """Parse strict ``1*HEXDIG *(; token [= token/quoted-string])``."""
    semicolon = line.find(b";")
    size_end = len(line) if semicolon < 0 else semicolon
    if size_end == 0 or size_end > 16:
        raise Http1Error(400, "bad-chunk-size", "invalid chunk size")
    size = 0
    index = 0
    while index < size_end:
        current = line[index]
        if 48 <= current <= 57:
            digit = current - 48
        elif 65 <= current <= 70:
            digit = current - 55
        elif 97 <= current <= 102:
            digit = current - 87
        else:
            raise Http1Error(400, "bad-chunk-size", "non-hex chunk size")
        size = size * 16 + digit
        index += 1

    while index < len(line):
        if line[index] != 59:
            raise Http1Error(400, "bad-chunk-extension", "invalid chunk extension")
        index += 1
        name_start = index
        while index < len(line) and line[index] not in (59, 61):
            index += 1
        if not _is_token(line[name_start:index]):
            raise Http1Error(400, "bad-chunk-extension", "invalid chunk extension name")
        if index >= len(line) or line[index] == 59:
            continue
        index += 1
        if index >= len(line):
            raise Http1Error(400, "bad-chunk-extension", "empty chunk extension value")
        if line[index] == 34:
            value_start = index
            index += 1
            escaped = False
            while index < len(line):
                current = line[index]
                if escaped:
                    escaped = False
                elif current == 92:
                    escaped = True
                elif current == 34:
                    index += 1
                    break
                index += 1
            value = line[value_start:index]
            if not _quoted_chunk_extension_value(value):
                raise Http1Error(
                    400,
                    "bad-chunk-extension",
                    "invalid quoted chunk extension value",
                )
            if index < len(line) and line[index] != 59:
                raise Http1Error(
                    400,
                    "bad-chunk-extension",
                    "bytes follow quoted chunk extension value",
                )
        else:
            value_start = index
            while index < len(line) and line[index] != 59:
                index += 1
            if not _is_token(line[value_start:index]):
                raise Http1Error(
                    400, "bad-chunk-extension", "invalid chunk extension value"
                )
    return size


def _parse_headers(lines, max_count: int):
    headers = []
    names = {}
    for line in lines:
        if len(line) == 0:
            continue
        if line[0] == 32 or line[0] == 9:
            raise Http1Error(400, "obs-fold", "folded header lines are rejected")
        colon = line.find(b":")
        if colon <= 0:
            raise Http1Error(400, "bad-header", "header is missing a field name")
        name_bytes = line[:colon]
        value_bytes = line[colon + 1:]
        if not _is_token(name_bytes):
            raise Http1Error(400, "bad-header-name", "invalid header field name")
        if _contains_bad_value_byte(value_bytes):
            raise Http1Error(400, "bad-header-value", "invalid header field value")
        if len(headers) >= max_count:
            raise Http1Error(431, "too-many-headers", "header count limit exceeded")
        name = name_bytes.decode("ascii").lower()
        value = value_bytes.decode("latin1").strip(" \t")
        headers.append((name, value))
        if name in names:
            names[name].append(value)
        else:
            names[name] = [value]
    return headers, names


class Http1ServerCodec:
    """Incremental request decoder with explicit framing and memory bounds."""

    def __init__(
        self,
        max_request_line: int = 8192,
        max_header_bytes: int = 32768,
        max_header_count: int = 100,
        max_body_bytes: int = 16777216,
        max_chunk_bytes: int = 1048576,
    ) -> None:
        if max_request_line <= 0 or max_header_bytes <= 0:
            raise ValueError("HTTP line/header limits must be positive")
        self.max_request_line = max_request_line
        self.max_header_bytes = max_header_bytes
        self.max_header_count = max_header_count
        self.max_body_bytes = max_body_bytes
        self.max_chunk_bytes = max_chunk_bytes
        self.buffer = bytearray(b"")
        self.state = "HEAD"
        self.remaining = 0
        self.body_received = 0
        self.closed = False

    def _reset_message(self) -> None:
        self.state = "HEAD"
        self.remaining = 0
        self.body_received = 0

    def _parse_head(self, block: bytes):
        lines = block.split(b"\r\n")
        if not lines or len(lines[0]) == 0:
            raise Http1Error(400, "empty-request-line", "request line is empty")
        request_line = lines[0]
        if len(request_line) > self.max_request_line:
            raise Http1Error(414, "request-line-too-long", "request line limit exceeded")
        if b"\t" in request_line or request_line.count(b" ") != 2:
            raise Http1Error(400, "bad-request-line", "invalid request-line spacing")
        method_bytes, target_bytes, version_bytes = request_line.split(b" ")
        if not _is_token(method_bytes):
            raise Http1Error(400, "bad-method", "invalid HTTP method")
        if len(target_bytes) == 0:
            raise Http1Error(400, "bad-target", "empty request target")
        # This gateway currently routes origin-form requests only.  Reject
        # absolute-form, authority-form and asterisk-form here so path
        # normalization cannot escape the named HTTP error boundary later in
        # application dispatch.
        if target_bytes[0] != 47:
            raise Http1Error(
                400,
                "unsupported-target-form",
                "gateway requires an origin-form request target",
            )
        for value in target_bytes:
            if value <= 32 or value == 127:
                raise Http1Error(400, "bad-target", "invalid request target byte")
        if version_bytes != b"HTTP/1.1" and version_bytes != b"HTTP/1.0":
            raise Http1Error(505, "bad-version", "unsupported HTTP version")
        headers, names = _parse_headers(lines[1:], self.max_header_count)
        if version_bytes == b"HTTP/1.1" and "host" not in names:
            raise Http1Error(400, "missing-host", "HTTP/1.1 requires Host")
        if "host" in names and len(names["host"]) != 1:
            raise Http1Error(400, "duplicate-host", "multiple Host fields rejected")

        content_length = -1
        if "content-length" in names:
            values = names["content-length"]
            if len(values) != 1 or "," in values[0]:
                raise Http1Error(400, "ambiguous-length", "multiple Content-Length values rejected")
            raw_length = values[0]
            if len(raw_length) == 0:
                raise Http1Error(400, "bad-content-length", "empty Content-Length")
            for char in raw_length:
                if char < "0" or char > "9":
                    raise Http1Error(400, "bad-content-length", "non-decimal Content-Length")
            content_length = int(raw_length)
            if content_length > self.max_body_bytes:
                raise Http1Error(413, "body-too-large", "request body limit exceeded")

        chunked = False
        if "transfer-encoding" in names:
            if content_length >= 0:
                raise Http1Error(400, "ambiguous-framing", "Transfer-Encoding with Content-Length rejected")
            codings = []
            for value in names["transfer-encoding"]:
                codings.extend(_split_commas(value))
            if codings != ["chunked"]:
                raise Http1Error(501, "unsupported-transfer-coding", "only a final chunked coding is supported")
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

        expect_continue = False
        if "expect" in names:
            expects = []
            for value in names["expect"]:
                expects.extend(_split_commas(value))
            if expects != ["100-continue"]:
                raise Http1Error(417, "unsupported-expectation", "unsupported Expect value")
            expect_continue = True

        event = RequestHead(
            method_bytes.decode("ascii"),
            target_bytes.decode("latin1"),
            version,
            headers,
            keep_alive,
            expect_continue,
            content_length if content_length >= 0 else 0,
            chunked,
        )
        if chunked:
            self.state = "CHUNK_SIZE"
        elif content_length > 0:
            self.state = "FIXED_BODY"
            self.remaining = content_length
        else:
            self._reset_message()
        return event, chunked or content_length > 0

    def feed(self, data: bytes, max_messages: int = 0):
        """Decode bytes, optionally stopping after a bounded message count.

        ``max_messages == 0`` preserves the sans-I/O codec's pipeline behavior.
        A transport owner can request one message at a time so a malformed
        later pipelined request cannot discard already-decoded earlier events.
        """
        events = []
        try:
            return self._feed_pending(data, max_messages, events)
        except Http1Error:
            # Nothing in ``events`` has crossed the public return boundary yet.
            # Release every retained parser body owner before propagating the
            # protocol error; successful returns transfer those owners to the
            # caller unchanged.
            for event in events:
                if isinstance(event, BodyChunk) and not event.released:
                    event.release()
            raise

    def _feed_pending(self, data: bytes, max_messages: int, events):
        if max_messages < 0:
            raise ValueError("max_messages must be non-negative")
        if self.closed:
            raise Http1Error(400, "connection-closed", "bytes received after close")
        self.buffer.extend(data)
        completed_messages = 0
        while True:
            if self.state == "HEAD":
                marker = _find_double_crlf(self.buffer)
                if marker < 0:
                    if len(self.buffer) > self.max_header_bytes:
                        raise Http1Error(431, "headers-too-large", "header byte limit exceeded")
                    line_end = _find_crlf(self.buffer)
                    if line_end < 0 and len(self.buffer) > self.max_request_line:
                        raise Http1Error(414, "request-line-too-long", "request line limit exceeded")
                    break
                if marker + 4 > self.max_header_bytes:
                    raise Http1Error(431, "headers-too-large", "header byte limit exceeded")
                block = bytes(self.buffer[:marker])
                del self.buffer[:marker + 4]
                head, has_body = self._parse_head(block)
                events.append(head)
                if not has_body:
                    events.append(RequestEnd())
                    completed_messages += 1
                    if max_messages > 0 and completed_messages >= max_messages:
                        break
                    continue

            elif self.state == "FIXED_BODY":
                if len(self.buffer) == 0:
                    break
                take = self.remaining
                if take > len(self.buffer):
                    take = len(self.buffer)
                chunk = bytes(self.buffer[:take])
                del self.buffer[:take]
                self.remaining -= take
                self.body_received += take
                if take > 0:
                    events.append(BodyChunk(chunk))
                if self.remaining == 0:
                    events.append(RequestEnd())
                    self._reset_message()
                    completed_messages += 1
                    if max_messages > 0 and completed_messages >= max_messages:
                        break
                    continue
                break

            elif self.state == "CHUNK_SIZE":
                marker = _find_crlf(self.buffer)
                if marker < 0:
                    if len(self.buffer) > 128:
                        raise Http1Error(400, "chunk-line-too-long", "chunk-size line limit exceeded")
                    break
                if marker > 128:
                    raise Http1Error(400, "chunk-line-too-long", "chunk-size line limit exceeded")
                raw_line = bytes(self.buffer[:marker])
                del self.buffer[:marker + 2]
                size = _parse_chunk_size_line(raw_line)
                if size > self.max_chunk_bytes:
                    raise Http1Error(413, "chunk-too-large", "chunk size limit exceeded")
                if self.body_received + size > self.max_body_bytes:
                    raise Http1Error(413, "body-too-large", "request body limit exceeded")
                if size == 0:
                    self.state = "TRAILERS"
                else:
                    self.remaining = size
                    self.state = "CHUNK_DATA"

            elif self.state == "CHUNK_DATA":
                if len(self.buffer) < self.remaining + 2:
                    break
                if self.buffer[self.remaining] != 13 or self.buffer[self.remaining + 1] != 10:
                    raise Http1Error(400, "bad-chunk-ending", "chunk data missing CRLF")
                chunk = bytes(self.buffer[:self.remaining])
                del self.buffer[:self.remaining + 2]
                self.body_received += self.remaining
                self.remaining = 0
                events.append(BodyChunk(chunk))
                self.state = "CHUNK_SIZE"

            elif self.state == "TRAILERS":
                if len(self.buffer) >= 2 and self.buffer[0] == 13 and self.buffer[1] == 10:
                    del self.buffer[:2]
                    events.append(RequestEnd([]))
                    self._reset_message()
                    completed_messages += 1
                    if max_messages > 0 and completed_messages >= max_messages:
                        break
                    continue
                marker = _find_double_crlf(self.buffer)
                if marker < 0:
                    if len(self.buffer) > self.max_header_bytes:
                        raise Http1Error(431, "trailers-too-large", "trailer byte limit exceeded")
                    break
                if marker + 4 > self.max_header_bytes:
                    raise Http1Error(
                        431,
                        "trailers-too-large",
                        "trailer byte limit exceeded",
                    )
                block = bytes(self.buffer[:marker])
                del self.buffer[:marker + 4]
                trailers, names = _parse_headers(block.split(b"\r\n"), self.max_header_count)
                for forbidden in ("content-length", "transfer-encoding", "host"):
                    if forbidden in names:
                        raise Http1Error(400, "forbidden-trailer", "framing or routing field in trailers")
                events.append(RequestEnd(trailers))
                self._reset_message()
                completed_messages += 1
                if max_messages > 0 and completed_messages >= max_messages:
                    break
                continue
            else:
                raise Http1Error(500, "codec-state", "unknown HTTP decoder state")
        return events

    def eof(self):
        self.closed = True
        if self.state != "HEAD" or len(self.buffer) != 0:
            raise Http1Error(400, "incomplete-request", "connection closed during request")
        return ConnectionClosed("eof")


_REASONS = {
    100: "Continue",
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Content Too Large",
    417: "Expectation Failed",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


def _validate_output_header(name: str, value: str) -> None:
    try:
        name_bytes = name.encode("ascii")
        value_bytes = value.encode("latin1")
    except UnicodeError as error:
        raise Http1Error(500, "bad-response-header", "response header encoding rejected") from error
    if not _is_token(name_bytes) or _contains_bad_value_byte(value_bytes):
        raise Http1Error(500, "bad-response-header", "unsafe response header rejected")


class Http1ResponseEncoder:
    def head(
        self,
        status: int,
        headers=None,
        content_length: int = -1,
        chunked: bool = False,
        keep_alive: bool = True,
    ) -> bytes:
        if headers is None:
            headers = []
        if content_length >= 0 and chunked:
            raise ValueError("response cannot be both fixed-length and chunked")
        reason = _REASONS.get(status, "")
        output = bytearray(("HTTP/1.1 " + str(status) + " " + reason + "\r\n").encode("ascii"))
        for name, value in headers:
            _validate_output_header(name, value)
            lower = name.lower()
            if lower in ("content-length", "transfer-encoding", "connection"):
                raise Http1Error(
                    500,
                    "response-framing-owned",
                    "response framing headers belong to the gateway encoder",
                )
            output.extend(name.encode("ascii"))
            output.extend(b": ")
            output.extend(value.encode("latin1"))
            output.extend(b"\r\n")
        if content_length >= 0:
            output.extend(("Content-Length: " + str(content_length) + "\r\n").encode("ascii"))
        if chunked:
            output.extend(b"Transfer-Encoding: chunked\r\n")
        if not keep_alive:
            output.extend(b"Connection: close\r\n")
        output.extend(b"\r\n")
        return bytes(output)

    def chunk(self, data: bytes) -> bytes:
        if len(data) == 0:
            return b""
        return (format(len(data), "x").encode("ascii") + b"\r\n" + data + b"\r\n")

    def end_chunks(self, trailers=None) -> bytes:
        if trailers is None:
            trailers = []
        output = bytearray(b"0\r\n")
        for name, value in trailers:
            _validate_output_header(name, value)
            if name.lower() in (
                "content-length",
                "transfer-encoding",
                "connection",
                "host",
            ):
                raise Http1Error(500, "bad-response-trailer", "framing field in response trailer")
            output.extend(name.encode("ascii"))
            output.extend(b": ")
            output.extend(value.encode("latin1"))
            output.extend(b"\r\n")
        output.extend(b"\r\n")
        return bytes(output)
