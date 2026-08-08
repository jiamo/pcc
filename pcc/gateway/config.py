"""Validated immutable-generation configuration records for pcc.gateway."""

from .lifecycle import AdmissionLimits


TLS_PROVIDER_DEFAULT_MAX_BYTES = 268435456


def _valid_sha256_hex(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    for character in value:
        if character not in "0123456789abcdef":
            return False
    return True


class ListenerConfig:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        backlog: int = 128,
        reuse_port: bool = False,
        tls_provider: str = "",
        tls_config=None,
        tls_provider_library: str = "",
        tls_provider_library_sha256: str = "",
        tls_provider_max_bytes: int = TLS_PROVIDER_DEFAULT_MAX_BYTES,
    ) -> None:
        if port <= 0 or port > 65535:
            raise ValueError("listener port is out of range")
        if backlog <= 0 or backlog > 65535:
            raise ValueError("listener backlog is out of range")
        self.host = host
        self.port = port
        self.backlog = backlog
        self.reuse_port = reuse_port
        self.tls_provider = tls_provider
        self.tls_config = tls_config
        self.tls_provider_library = tls_provider_library
        self.tls_provider_library_sha256 = tls_provider_library_sha256
        self.tls_provider_max_bytes = tls_provider_max_bytes
        if not tls_provider and (tls_config is not None or tls_provider_library):
            raise ValueError("TLS config/library requires a named TLS provider")
        if tls_provider and tls_config is None:
            raise ValueError("TLS listener requires a TLS configuration")
        if tls_provider_library_sha256 and not tls_provider_library:
            raise ValueError("TLS provider digest requires a provider library")
        if tls_provider_library and not tls_provider_library_sha256:
            raise ValueError("TLS provider library requires an expected SHA-256")
        if tls_provider_library_sha256 and not _valid_sha256_hex(
            tls_provider_library_sha256
        ):
            raise ValueError(
                "TLS provider SHA-256 must be 64 lowercase hexadecimal characters"
            )
        if (
            not isinstance(tls_provider_max_bytes, int)
            or isinstance(tls_provider_max_bytes, bool)
            or tls_provider_max_bytes <= 0
            or tls_provider_max_bytes > 0x7FFFFFFFFFFFFFFF
        ):
            raise ValueError("TLS provider byte limit is out of range")


class Http1Limits:
    def __init__(
        self,
        request_line_bytes: int = 8192,
        header_bytes: int = 32768,
        header_count: int = 100,
        body_bytes: int = 16777216,
        chunk_bytes: int = 1048576,
        header_timeout_ms: int = 10000,
        body_timeout_ms: int = 30000,
        idle_timeout_ms: int = 30000,
    ) -> None:
        values = (
            request_line_bytes,
            header_bytes,
            header_count,
            body_bytes,
            chunk_bytes,
            header_timeout_ms,
            body_timeout_ms,
            idle_timeout_ms,
        )
        for value in values:
            if value <= 0:
                raise ValueError("HTTP/1 limits must be positive")
        if chunk_bytes > body_bytes:
            raise ValueError("chunk limit cannot exceed body limit")
        self.request_line_bytes = request_line_bytes
        self.header_bytes = header_bytes
        self.header_count = header_count
        self.body_bytes = body_bytes
        self.chunk_bytes = chunk_bytes
        self.header_timeout_ms = header_timeout_ms
        self.body_timeout_ms = body_timeout_ms
        self.idle_timeout_ms = idle_timeout_ms


class BufferLimits:
    def __init__(
        self,
        segment_bytes: int = 16384,
        low_watermark: int = 32768,
        high_watermark: int = 65536,
        connection_bytes: int = 1048576,
    ) -> None:
        if segment_bytes <= 0:
            raise ValueError("buffer segment size must be positive")
        if low_watermark < 0 or high_watermark <= low_watermark:
            raise ValueError("buffer watermarks must satisfy 0 <= low < high")
        if connection_bytes < high_watermark:
            raise ValueError("connection buffer limit must cover high watermark")
        self.segment_bytes = segment_bytes
        self.low_watermark = low_watermark
        self.high_watermark = high_watermark
        self.connection_bytes = connection_bytes


class GatewayConfig:
    """One publishable generation; callers replace rather than mutate it."""

    def __init__(
        self,
        listeners=(),
        carrier_count: int = 0,
        http1=None,
        buffers=None,
        admission=None,
        drain_timeout_ms: int = 30000,
        waitset_backend: str = "auto",
        max_requests_per_connection: int = 1000,
        write_timeout_ms: int = 30000,
        accept_poll_ms: int = 1000,
        control_poll_ms: int = 10,
        tls_handshake_timeout_ms: int = 10000,
        tls_close_timeout_ms: int = 3000,
        install_signal_handlers: bool = True,
    ) -> None:
        if not listeners:
            listeners = (ListenerConfig(),)
        if carrier_count < 0 or carrier_count > 64:
            raise ValueError("carrier count must be between 0 and 64")
        if drain_timeout_ms <= 0:
            raise ValueError("drain timeout must be positive")
        for value in (
            max_requests_per_connection,
            write_timeout_ms,
            accept_poll_ms,
            control_poll_ms,
            tls_handshake_timeout_ms,
            tls_close_timeout_ms,
        ):
            if value <= 0:
                raise ValueError("gateway runtime limits must be positive")
        if waitset_backend not in ("auto", "kqueue", "epoll", "poll"):
            raise ValueError("unknown waitset backend")
        self.listeners = tuple(listeners)
        self.carrier_count = carrier_count
        self.http1 = http1 or Http1Limits()
        self.buffers = buffers or BufferLimits()
        self.admission = admission or AdmissionLimits()
        self.drain_timeout_ms = drain_timeout_ms
        self.waitset_backend = waitset_backend
        self.max_requests_per_connection = max_requests_per_connection
        self.write_timeout_ms = write_timeout_ms
        self.accept_poll_ms = accept_poll_ms
        self.control_poll_ms = control_poll_ms
        self.tls_handshake_timeout_ms = tls_handshake_timeout_ms
        self.tls_close_timeout_ms = tls_close_timeout_ms
        self.install_signal_handlers = bool(install_signal_handlers)

    def replace(
        self,
        listeners=None,
        carrier_count: int = -1,
        http1=None,
        buffers=None,
        admission=None,
        drain_timeout_ms: int = -1,
        waitset_backend: str = "",
        max_requests_per_connection: int = -1,
        write_timeout_ms: int = -1,
        accept_poll_ms: int = -1,
        control_poll_ms: int = -1,
        tls_handshake_timeout_ms: int = -1,
        tls_close_timeout_ms: int = -1,
        install_signal_handlers=None,
    ):
        return GatewayConfig(
            self.listeners if listeners is None else listeners,
            self.carrier_count if carrier_count < 0 else carrier_count,
            self.http1 if http1 is None else http1,
            self.buffers if buffers is None else buffers,
            self.admission if admission is None else admission,
            self.drain_timeout_ms if drain_timeout_ms < 0 else drain_timeout_ms,
            self.waitset_backend if not waitset_backend else waitset_backend,
            self.max_requests_per_connection
            if max_requests_per_connection < 0
            else max_requests_per_connection,
            self.write_timeout_ms if write_timeout_ms < 0 else write_timeout_ms,
            self.accept_poll_ms if accept_poll_ms < 0 else accept_poll_ms,
            self.control_poll_ms if control_poll_ms < 0 else control_poll_ms,
            self.tls_handshake_timeout_ms
            if tls_handshake_timeout_ms < 0
            else tls_handshake_timeout_ms,
            self.tls_close_timeout_ms
            if tls_close_timeout_ms < 0
            else tls_close_timeout_ms,
            self.install_signal_handlers
            if install_signal_handlers is None
            else bool(install_signal_handlers),
        )
