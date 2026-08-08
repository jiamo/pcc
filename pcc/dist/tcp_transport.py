"""Explicit TCP-ring transport owner.

This module is deliberately separate from the default local-only transport
oracle.  Importing :mod:`pcc.dist` never opens a socket; a caller must select
``tcp-ring`` explicitly through :func:`pcc.dist.transport.select_owner` and
then call :meth:`TCPRingOwner.open`.

The default owner remains localhost-only.  A caller may explicitly admit
non-loopback endpoints only by supplying a >=256-bit admission key and setting
``allow_remote=True``.  That path performs a fresh-nonce HMAC challenge before
accepting rank traffic.  It authenticates cluster admission but does not
encrypt TCP payloads and therefore does not claim TLS/mTLS security.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass

from .results import DistError
from .transport import ClusterManifest


BACKEND_NAME = "tcp-ring"
_PROTOCOL_VERSION = 1
_HANDSHAKE = struct.Struct("!4sBII32s")
_AUTH_CHALLENGE = struct.Struct("!4sB32s")
_AUTH_HANDSHAKE = struct.Struct("!4sBII32s32s")
_FRAME_HEADER = struct.Struct("!4sBIIQI32s")
_HANDSHAKE_MAGIC = b"PCCR"
_AUTH_CHALLENGE_MAGIC = b"PCCQ"
_AUTH_HANDSHAKE_MAGIC = b"PCCA"
_FRAME_MAGIC = b"PCCD"
_AUTH_CONTEXT = b"pcc-dist-tcp-ring-admission-v1\0"


class TCPTransportError(DistError):
    """Base error for the explicitly selected TCP transport owner."""


class TCPTransportTimeout(TCPTransportError):
    """A bounded connect, accept, read, or write operation expired."""


class FrameIntegrityError(TCPTransportError):
    """A handshake or data frame failed identity/integrity validation."""


@dataclass(frozen=True)
class TransportOwnerSelection:
    """Machine-checkable proof that backend selection did not fall back."""

    requested_backend: str
    actual_backend: str
    fallback_used: bool
    scope: str
    secure: bool
    authenticated: bool = False


class FrameCodec:
    """Length-delimited, rank-bound and digest-protected wire frames."""

    @staticmethod
    def encode(
        payload: bytes,
        *,
        source_rank: int,
        destination_rank: int,
        sequence: int,
        max_frame_bytes: int,
    ) -> bytes:
        if not isinstance(payload, bytes):
            raise TypeError("TCP transport payload must be bytes")
        if len(payload) > max_frame_bytes:
            raise FrameIntegrityError(
                f"payload length {len(payload)} exceeds limit {max_frame_bytes}"
            )
        digest = hashlib.sha256(payload).digest()
        return _FRAME_HEADER.pack(
            _FRAME_MAGIC,
            _PROTOCOL_VERSION,
            source_rank,
            destination_rank,
            sequence,
            len(payload),
            digest,
        ) + payload

    @staticmethod
    def decode(
        frame: bytes,
        *,
        expected_source: int,
        expected_destination: int,
        expected_sequence: int,
        max_frame_bytes: int,
    ) -> bytes:
        if len(frame) < _FRAME_HEADER.size:
            raise FrameIntegrityError("truncated TCP transport frame header")
        header = frame[: _FRAME_HEADER.size]
        payload = frame[_FRAME_HEADER.size :]
        (
            magic,
            version,
            source,
            destination,
            sequence,
            payload_len,
            expected_digest,
        ) = _FRAME_HEADER.unpack(header)
        if magic != _FRAME_MAGIC or version != _PROTOCOL_VERSION:
            raise FrameIntegrityError("invalid TCP transport frame magic/version")
        if source != expected_source or destination != expected_destination:
            raise FrameIntegrityError(
                "TCP transport frame rank mismatch: "
                f"got {source}->{destination}, expected "
                f"{expected_source}->{expected_destination}"
            )
        if sequence != expected_sequence:
            raise FrameIntegrityError(
                f"TCP transport frame sequence {sequence}, expected {expected_sequence}"
            )
        if payload_len > max_frame_bytes:
            raise FrameIntegrityError(
                f"TCP transport frame length {payload_len} exceeds limit {max_frame_bytes}"
            )
        if payload_len != len(payload):
            raise FrameIntegrityError(
                f"TCP transport frame length {payload_len}, received {len(payload)}"
            )
        if not hashlib.sha256(payload).digest() == expected_digest:
            raise FrameIntegrityError("TCP transport frame SHA-256 mismatch")
        return payload


def _parse_endpoint(value: str, *, allow_remote: bool) -> tuple[str, int]:
    host: str
    port_text: str
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or end + 1 >= len(value) or value[end + 1] != ":":
            raise TCPTransportError(f"invalid bracketed endpoint {value!r}")
        host, port_text = value[1:end], value[end + 2 :]
    else:
        try:
            host, port_text = value.rsplit(":", 1)
        except ValueError:
            raise TCPTransportError(
                f"tcp-ring node host must be host:port, got {value!r}"
            ) from None
    if host.lower() == "localhost":
        host = "127.0.0.1"
    else:
        try:
            address = ipaddress.ip_address(host)
            if not allow_remote and not address.is_loopback:
                raise TCPTransportError(
                    f"tcp-ring owner is localhost-only, got host {host!r}"
                )
            if address.is_unspecified:
                raise TCPTransportError(
                    f"tcp-ring advertised endpoint cannot be unspecified: {host!r}"
                )
        except ValueError:
            if not allow_remote:
                raise TCPTransportError(
                    f"tcp-ring owner requires a loopback address, got host {host!r}"
                ) from None
            if not host or any(ch.isspace() for ch in host):
                raise TCPTransportError(
                    f"invalid TCP host in endpoint {value!r}"
                ) from None
    try:
        port = int(port_text)
    except ValueError:
        raise TCPTransportError(f"invalid TCP port in endpoint {value!r}") from None
    if not (1 <= port <= 65535):
        raise TCPTransportError(f"TCP port out of range in endpoint {value!r}")
    return host, port


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        try:
            chunk = sock.recv(remaining)
        except socket.timeout as exc:
            raise TCPTransportTimeout("bounded TCP read timed out") from exc
        except OSError as exc:
            raise TCPTransportError(f"TCP read failed: {exc}") from exc
        if not chunk:
            raise TCPTransportError(
                f"TCP peer closed with {remaining} frame bytes still expected"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class TCPRingOwner:
    """One rank's explicitly selected TCP-ring endpoint."""

    def __init__(
        self,
        manifest: ClusterManifest,
        rank: int,
        *,
        connect_timeout_s: float = 3.0,
        io_timeout_s: float = 3.0,
        max_frame_bytes: int = 16 * 1024 * 1024,
        allow_remote: bool = False,
        admission_key: bytes | None = None,
    ) -> None:
        if not (0 <= rank < manifest.world_size):
            raise TCPTransportError(
                f"rank {rank} out of range for world_size {manifest.world_size}"
            )
        if connect_timeout_s <= 0 or io_timeout_s <= 0:
            raise TCPTransportError("TCP owner timeouts must be positive")
        if max_frame_bytes <= 0:
            raise TCPTransportError("max_frame_bytes must be positive")
        if admission_key is not None:
            if not isinstance(admission_key, bytes):
                raise TCPTransportError("admission_key must be bytes")
            if len(admission_key) < 32:
                raise TCPTransportError("admission_key must contain at least 256 bits")
        if allow_remote and admission_key is None:
            raise TCPTransportError(
                "remote TCP-ring endpoints require authenticated admission"
            )
        endpoints: dict[int, tuple[str, int]] = {}
        for node in manifest.nodes:
            if node.transport != BACKEND_NAME:
                raise TCPTransportError(
                    f"rank {node.rank} manifest transport {node.transport!r}; "
                    f"expected {BACKEND_NAME!r}"
                )
            endpoints[node.rank] = _parse_endpoint(
                node.host, allow_remote=allow_remote
            )
        self.manifest = manifest
        self.rank = rank
        self.selection = TransportOwnerSelection(
            requested_backend=BACKEND_NAME,
            actual_backend=BACKEND_NAME,
            fallback_used=False,
            scope=(
                "multi-host-authenticated"
                if allow_remote
                else "localhost-multiprocess"
            ),
            secure=False,
            authenticated=admission_key is not None,
        )
        self._endpoints = endpoints
        self._connect_timeout_s = float(connect_timeout_s)
        self._io_timeout_s = float(io_timeout_s)
        self._max_frame_bytes = int(max_frame_bytes)
        self._allow_remote = bool(allow_remote)
        self._admission_key = admission_key
        self._manifest_digest = hashlib.sha256(
            manifest.canonical_body().encode("utf-8")
        ).digest()
        self._listener: socket.socket | None = None
        self._outgoing: socket.socket | None = None
        self._incoming: socket.socket | None = None
        self._send_sequence = 0
        self._recv_sequence = 0

    @property
    def next_rank(self) -> int:
        return (self.rank + 1) % self.manifest.world_size

    @property
    def previous_rank(self) -> int:
        return (self.rank - 1) % self.manifest.world_size

    @property
    def is_open(self) -> bool:
        return self._outgoing is not None and self._incoming is not None

    def _handshake(self, source: int, destination: int) -> bytes:
        return _HANDSHAKE.pack(
            _HANDSHAKE_MAGIC,
            _PROTOCOL_VERSION,
            source,
            destination,
            self._manifest_digest,
        )

    def _validate_handshake(self, data: bytes) -> None:
        magic, version, source, destination, digest = _HANDSHAKE.unpack(data)
        if magic != _HANDSHAKE_MAGIC or version != _PROTOCOL_VERSION:
            raise FrameIntegrityError("invalid TCP-ring handshake magic/version")
        if source != self.previous_rank or destination != self.rank:
            raise FrameIntegrityError(
                f"TCP-ring handshake rank mismatch: got {source}->{destination}, "
                f"expected {self.previous_rank}->{self.rank}"
            )
        if digest != self._manifest_digest:
            raise FrameIntegrityError("TCP-ring handshake manifest mismatch")

    def _auth_challenge(self) -> bytes:
        return _AUTH_CHALLENGE.pack(
            _AUTH_CHALLENGE_MAGIC,
            _PROTOCOL_VERSION,
            os.urandom(32),
        )

    def _authenticated_handshake(
        self, challenge: bytes, source: int, destination: int
    ) -> bytes:
        if self._admission_key is None:
            raise TCPTransportError("authenticated admission is not configured")
        magic, version, nonce = _AUTH_CHALLENGE.unpack(challenge)
        if magic != _AUTH_CHALLENGE_MAGIC or version != _PROTOCOL_VERSION:
            raise FrameIntegrityError("invalid TCP-ring admission challenge")
        rank_binding = struct.pack("!II", source, destination)
        digest = hmac.new(
            self._admission_key,
            _AUTH_CONTEXT + nonce + self._manifest_digest + rank_binding,
            hashlib.sha256,
        ).digest()
        return _AUTH_HANDSHAKE.pack(
            _AUTH_HANDSHAKE_MAGIC,
            _PROTOCOL_VERSION,
            source,
            destination,
            self._manifest_digest,
            digest,
        )

    def _validate_authenticated_handshake(
        self, data: bytes, challenge: bytes
    ) -> None:
        if self._admission_key is None:
            raise TCPTransportError("authenticated admission is not configured")
        magic, version, source, destination, manifest_digest, received = (
            _AUTH_HANDSHAKE.unpack(data)
        )
        if magic != _AUTH_HANDSHAKE_MAGIC or version != _PROTOCOL_VERSION:
            raise FrameIntegrityError("invalid TCP-ring authenticated handshake")
        if source != self.previous_rank or destination != self.rank:
            raise FrameIntegrityError(
                f"TCP-ring handshake rank mismatch: got {source}->{destination}, "
                f"expected {self.previous_rank}->{self.rank}"
            )
        if manifest_digest != self._manifest_digest:
            raise FrameIntegrityError("TCP-ring handshake manifest mismatch")
        expected_frame = self._authenticated_handshake(
            challenge, source, destination
        )
        expected = _AUTH_HANDSHAKE.unpack(expected_frame)[-1]
        if not hmac.compare_digest(received, expected):
            raise FrameIntegrityError("TCP-ring admission authentication failed")

    def open(self) -> "TCPRingOwner":
        if self.is_open:
            return self
        if self._outgoing is not None or self._incoming is not None:
            raise TCPTransportError("TCP-ring owner is partially open")
        deadline = time.monotonic() + self._connect_timeout_s
        local_host, _ = self._endpoints[self.rank]
        family = socket.AF_INET6 if ":" in local_host else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        self._listener = listener
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(self._endpoints[self.rank])
            listener.listen(1)

            outgoing: socket.socket | None = None
            last_error: OSError | None = None
            while outgoing is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = f": {last_error}" if last_error is not None else ""
                    raise TCPTransportTimeout(
                        f"tcp-ring connect to rank {self.next_rank} timed out{detail}"
                    )
                try:
                    outgoing = socket.create_connection(
                        self._endpoints[self.next_rank], timeout=min(remaining, 0.1)
                    )
                except OSError as exc:
                    last_error = exc
                    time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TCPTransportTimeout("tcp-ring accept timed out")
            listener.settimeout(remaining)
            try:
                incoming, _ = listener.accept()
            except socket.timeout as exc:
                raise TCPTransportTimeout("tcp-ring accept timed out") from exc
            except OSError as exc:
                raise TCPTransportError(f"TCP accept failed: {exc}") from exc
            incoming.settimeout(self._io_timeout_s)
            self._incoming = incoming
            outgoing.settimeout(self._io_timeout_s)
            self._outgoing = outgoing
            try:
                if self._admission_key is None:
                    outgoing.sendall(self._handshake(self.rank, self.next_rank))
                    self._validate_handshake(_recv_exact(incoming, _HANDSHAKE.size))
                else:
                    local_challenge = self._auth_challenge()
                    incoming.sendall(local_challenge)
                    remote_challenge = _recv_exact(outgoing, _AUTH_CHALLENGE.size)
                    outgoing.sendall(
                        self._authenticated_handshake(
                            remote_challenge, self.rank, self.next_rank
                        )
                    )
                    self._validate_authenticated_handshake(
                        _recv_exact(incoming, _AUTH_HANDSHAKE.size),
                        local_challenge,
                    )
            except socket.timeout as exc:
                raise TCPTransportTimeout("bounded TCP handshake I/O timed out") from exc
            except OSError as exc:
                raise TCPTransportError(f"TCP handshake I/O failed: {exc}") from exc
            listener.close()
            self._listener = None
            return self
        except Exception:
            self.close()
            raise

    def send(self, payload: bytes) -> None:
        if self._outgoing is None:
            raise TCPTransportError("TCP-ring owner is not open")
        frame = FrameCodec.encode(
            payload,
            source_rank=self.rank,
            destination_rank=self.next_rank,
            sequence=self._send_sequence,
            max_frame_bytes=self._max_frame_bytes,
        )
        try:
            self._outgoing.sendall(frame)
        except socket.timeout as exc:
            raise TCPTransportTimeout("bounded TCP frame write timed out") from exc
        except OSError as exc:
            raise TCPTransportError(f"TCP frame write failed: {exc}") from exc
        self._send_sequence += 1

    def recv(self) -> bytes:
        if self._incoming is None:
            raise TCPTransportError("TCP-ring owner is not open")
        header = _recv_exact(self._incoming, _FRAME_HEADER.size)
        (
            _,
            _,
            _,
            _,
            _,
            payload_len,
            _,
        ) = _FRAME_HEADER.unpack(header)
        if payload_len > self._max_frame_bytes:
            raise FrameIntegrityError(
                f"TCP transport frame length {payload_len} exceeds limit "
                f"{self._max_frame_bytes}"
            )
        payload = _recv_exact(self._incoming, payload_len)
        decoded = FrameCodec.decode(
            header + payload,
            expected_source=self.previous_rank,
            expected_destination=self.rank,
            expected_sequence=self._recv_sequence,
            max_frame_bytes=self._max_frame_bytes,
        )
        self._recv_sequence += 1
        return decoded

    def exchange(self, payload: bytes) -> bytes:
        """Send to the next rank while receiving from the previous rank.

        Sending large frames synchronously on every rank can fill every socket
        buffer before any rank starts reading.  One bounded sender thread keeps
        the opposite side draining while preserving the owner's independent
        send/receive sequence checks.
        """
        send_error: list[BaseException] = []

        def run_send() -> None:
            try:
                self.send(payload)
            except BaseException as exc:
                send_error.append(exc)

        sender = threading.Thread(target=run_send, name="pcc-tcp-ring-send", daemon=True)
        sender.start()
        try:
            received = self.recv()
        except BaseException:
            self.close()
            sender.join(timeout=self._io_timeout_s + 0.1)
            raise
        sender.join(timeout=self._io_timeout_s + 0.1)
        if sender.is_alive():
            self.close()
            raise TCPTransportTimeout("bounded TCP frame exchange write timed out")
        if send_error:
            raise send_error[0]
        return received

    def close(self) -> None:
        for sock in (self._outgoing, self._incoming, self._listener):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._outgoing = None
        self._incoming = None
        self._listener = None

    def __enter__(self) -> "TCPRingOwner":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
