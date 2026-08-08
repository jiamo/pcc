"""Live product canary for the pcc-owned HTTPS gateway.

This is deliberately an opt-in integration gate.  It compiles the product
fixture with a *current pcc1* using ``backend=self`` and
``python-libpython=off``, then starts one independent native process under each
``PCC_GC_BACKEND`` 0..4.  CPython owns only the test-side DNS server, upstream
HTTP server, verified TLS client, signals, and process inspection; it is never
the gateway process or a fallback inside that process.

What a green run proves is intentionally finite: a real loopback listener,
wire DNS, HTTP/1 health/stream/proxy traffic, the reviewed OpenSSL 3 provider,
CA/hostname-verified TLS, SIGHUP certificate-generation replacement, SIGTERM
drain, final metric closure, and no nginx/host-Python/libpython process owner.
It does not prove HTTP/2, Internet-facing deployment, zero-libc, throughput, or
event-driven idle scalability.

Run explicitly with ``PCC_RUN_GATEWAY_PRODUCT_CANARY=1``.  Once selected,
missing OpenSSL 3/build/process-inspection dependencies are hard failures, not
skips and not passing model evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

import pytest

from pcc.tools.runtime_archive_provenance import (
    PRODUCTION_POLICY,
    verify_runtime_archive_manifest,
)
from pcc1_gate import find_current_pcc1
from process_timeout import run_process_group_timeout
from tests.runtime_build_cache import cached_threaded_pcc_python_runtime


REPO = Path(__file__).resolve().parents[2]
PRODUCT_TEMPLATE = (
    REPO / "tests" / "fixtures" / "gateway" / "current_pcc1_gateway_product.py"
)
NATIVE_TLS_DIR = REPO / "pcc" / "gateway" / "native"
GATE_ENV = "PCC_RUN_GATEWAY_PRODUCT_CANARY"
TLS_SERVER_NAME = "gateway.pcc.test"
DNS_NAME = "backend.pcc.test"
PRODUCT_MARKER = "PCC1_GATEWAY_PRODUCT_OK"
GC_MARKER = "PCC1_GATEWAY_GC_BACKEND"
DRAIN_MARKER = "PCC1_GATEWAY_DRAIN_FORCED"
WAITSET_MARKER = "PCC1_GATEWAY_WAITSET_BACKEND"
RESOURCE_MARKER = "PCC1_GATEWAY_RESOURCE_CLOSURE"

_PLATFORM_REASON = None
if sys.platform not in ("darwin", "linux"):
    _PLATFORM_REASON = "gateway product canary supports Darwin and Linux only"

pytestmark = (
    pytest.mark.integration,
    pytest.mark.pcc_gate(
        env=GATE_ENV,
        probe="pcc1",
        unavailable=_PLATFORM_REASON,
    ),
    pytest.mark.xdist_group(name="gateway_product_canary"),
)


@dataclass(frozen=True)
class TlsAssets:
    provider: Path
    ca_certificate: Path
    old_certificate: Path
    old_private_key: Path
    new_certificate: Path
    new_private_key: Path


@dataclass(frozen=True)
class DnsObservation:
    transaction_id: int
    response_transaction_id: int
    name: str
    query_type: int
    query_class: int


@dataclass(frozen=True)
class UpstreamObservation:
    method: str
    target: str
    headers: dict[str, str]


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    chunks: tuple[bytes, ...]


def _gate_fail(message: str) -> None:
    pytest.fail(message, pytrace=False)


def _required_tool(name: str, purpose: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        _gate_fail(
            f"{purpose} requires executable {name!r}; this selected integration "
            "gate cannot be credited without it"
        )
    return str(resolved)


def _run_checked(
    command,
    *,
    label: str,
    timeout: float,
    cwd: Path = REPO,
    env=None,
) -> subprocess.CompletedProcess[str]:
    completed = run_process_group_timeout(
        [str(item) for item in command],
        cwd=cwd,
        env=env,
        timeout=timeout,
    )
    if completed.returncode != 0:
        _gate_fail(
            f"{label} failed (exit {completed.returncode})\n"
            f"command: {' '.join(str(item) for item in command)}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed


def _openssl_binary(environment: dict[str, str]) -> str:
    explicit = environment.get("PCC_GATEWAY_OPENSSL", "").strip()
    prefix = environment.get("PCC_OPENSSL_PREFIX", "").strip()
    if explicit:
        candidate = explicit
    elif prefix:
        candidate = str(Path(prefix) / "bin" / "openssl")
    else:
        candidate = "openssl"
    openssl = _required_tool(candidate, "verified TLS certificate generation")
    version = _run_checked(
        [openssl, "version"],
        label="OpenSSL version probe",
        timeout=10,
        env=environment,
    ).stdout.strip()
    match = re.match(r"^OpenSSL\s+(\d+)\.(\d+)", version)
    if match is None or int(match.group(1)) < 3:
        _gate_fail(
            "gateway product canary requires an OpenSSL >= 3 command, got "
            f"{version!r}; set PCC_GATEWAY_OPENSSL or PCC_OPENSSL_PREFIX"
        )
    return openssl


def _generate_leaf_certificate(
    openssl: str,
    directory: Path,
    ca_certificate: Path,
    ca_private_key: Path,
    extensions: Path,
    name: str,
    serial: int,
    environment: dict[str, str],
) -> tuple[Path, Path]:
    private_key = directory / f"{name}.key.pem"
    request = directory / f"{name}.csr.pem"
    certificate = directory / f"{name}.cert.pem"
    _run_checked(
        [
            openssl,
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-subj",
            f"/CN={TLS_SERVER_NAME}",
            "-keyout",
            private_key,
            "-out",
            request,
        ],
        label=f"{name} TLS key/CSR generation",
        timeout=30,
        env=environment,
    )
    _run_checked(
        [
            openssl,
            "x509",
            "-req",
            "-in",
            request,
            "-CA",
            ca_certificate,
            "-CAkey",
            ca_private_key,
            "-set_serial",
            str(serial),
            "-days",
            "2",
            "-sha256",
            "-extfile",
            extensions,
            "-out",
            certificate,
        ],
        label=f"{name} TLS certificate signing",
        timeout=30,
        env=environment,
    )
    return certificate.resolve(), private_key.resolve()


def _build_tls_assets(directory: Path) -> TlsAssets:
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    make = _required_tool(environment.get("MAKE", "make"), "TLS provider build")
    prefix = environment.get("PCC_OPENSSL_PREFIX", "").strip()
    if (
        not prefix
        and not environment.get("OPENSSL_CFLAGS", "").strip()
        and not environment.get("OPENSSL_LIBS", "").strip()
    ):
        _required_tool(
            environment.get("PKG_CONFIG", "pkg-config"),
            "OpenSSL 3 provider discovery",
        )
    openssl = _openssl_binary(environment)

    provider_dir = directory / "provider"
    provider_command = [
        make,
        "-C",
        NATIVE_TLS_DIR,
        f"OUT_DIR={provider_dir}",
    ]
    if prefix:
        provider_command.append(f"OPENSSL_PREFIX={prefix}")
    _run_checked(
        provider_command,
        label="pcc OpenSSL 3 TLS provider build",
        timeout=120,
        env=environment,
    )
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    provider = (provider_dir / f"libpcc_tls_openssl{suffix}").resolve()
    if not provider.is_file() or not provider.is_absolute():
        _gate_fail(f"TLS provider build did not produce {provider}")

    ca_private_key = directory / "ca.key.pem"
    ca_certificate = directory / "ca.cert.pem"
    _run_checked(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            "2",
            "-subj",
            "/CN=pcc gateway product test CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            ca_private_key,
            "-out",
            ca_certificate,
        ],
        label="gateway product test CA generation",
        timeout=30,
        env=environment,
    )
    extensions = directory / "server.extensions"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        f"subjectAltName=DNS:{TLS_SERVER_NAME}\n",
        encoding="ascii",
    )
    old_certificate, old_private_key = _generate_leaf_certificate(
        openssl,
        directory,
        ca_certificate,
        ca_private_key,
        extensions,
        "old",
        101,
        environment,
    )
    new_certificate, new_private_key = _generate_leaf_certificate(
        openssl,
        directory,
        ca_certificate,
        ca_private_key,
        extensions,
        "new",
        102,
        environment,
    )
    return TlsAssets(
        provider=provider,
        ca_certificate=ca_certificate.resolve(),
        old_certificate=old_certificate,
        old_private_key=old_private_key,
        new_certificate=new_certificate,
        new_private_key=new_private_key,
    )


@pytest.fixture(scope="session")
def gateway_product_tls_assets(tmp_path_factory) -> TlsAssets:
    return _build_tls_assets(tmp_path_factory.mktemp("gateway-product-tls"))


@pytest.fixture(scope="session")
def gateway_product_runtime_archive() -> Path:
    runtime = cached_threaded_pcc_python_runtime()
    archive = runtime / "libpy_runtime_pcc_py.a"
    if not archive.is_file():
        _gate_fail(f"threaded pcc-Python runtime archive is missing: {archive}")
    manifest = verify_runtime_archive_manifest(archive, runtime_root=runtime)
    records = manifest["members"]
    assert manifest["policy"] == PRODUCTION_POLICY
    assert {record["source_kind"] for record in records} == {"pcc-python"}
    assert {record["producer_kind"] for record in records} == {
        "pcc-python-library-ir-to-obj"
    }
    assert {record["uses_host_cc"] for record in records} == {False}
    return archive


class LocalDnsOracle:
    """Tiny authoritative UDP oracle for one A record, outside the SUT."""

    def __init__(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.1)
        self.port = int(self.socket.getsockname()[1])
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._observations: list[DnsObservation] = []
        self.errors: list[str] = []
        self.thread = threading.Thread(
            target=self._serve,
            name="pcc-gateway-dns-oracle",
        )

    @staticmethod
    def _question(packet: bytes) -> tuple[int, str, int, int]:
        if len(packet) < 17:
            raise ValueError("DNS query is shorter than one question")
        labels = []
        offset = 12
        while True:
            if offset >= len(packet):
                raise ValueError("DNS query name is truncated")
            length = packet[offset]
            offset += 1
            if length == 0:
                break
            if length > 63 or offset + length > len(packet):
                raise ValueError("DNS query label is invalid")
            labels.append(packet[offset:offset + length].decode("ascii"))
            offset += length
        if offset + 4 > len(packet):
            raise ValueError("DNS query type/class is truncated")
        query_type, query_class = struct.unpack("!HH", packet[offset:offset + 4])
        return offset + 4, ".".join(labels).lower(), query_type, query_class

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                packet, peer = self.socket.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as error:
                if not self._stop.is_set():
                    self.errors.append(f"DNS recv failed: {error}")
                break
            try:
                if len(packet) < 12:
                    raise ValueError("DNS header is truncated")
                transaction_id, _flags, questions, _answers, _ns, _extra = (
                    struct.unpack("!HHHHHH", packet[:12])
                )
                if questions != 1:
                    raise ValueError("DNS oracle expected exactly one question")
                question_end, name, query_type, query_class = self._question(packet)
                success = name == DNS_NAME and query_type == 1 and query_class == 1
                response_flags = 0x8180 if success else 0x8183
                answer_count = 1 if success else 0
                response = bytearray(
                    struct.pack(
                        "!HHHHHH",
                        transaction_id,
                        response_flags,
                        1,
                        answer_count,
                        0,
                        0,
                    )
                )
                response.extend(packet[12:question_end])
                if success:
                    response.extend(b"\xc0\x0c")
                    response.extend(struct.pack("!HHIH", 1, 1, 30, 4))
                    response.extend(socket.inet_aton("127.0.0.1"))
                self.socket.sendto(bytes(response), peer)
                observation = DnsObservation(
                    transaction_id,
                    struct.unpack("!H", response[:2])[0],
                    name,
                    query_type,
                    query_class,
                )
                with self._lock:
                    self._observations.append(observation)
            except Exception as error:  # oracle diagnostics, never SUT fallback
                self.errors.append(f"DNS oracle failed: {error}")

    def start(self) -> None:
        self.thread.start()

    def observations(self) -> tuple[DnsObservation, ...]:
        with self._lock:
            return tuple(self._observations)

    def stop(self) -> None:
        self._stop.set()
        self.socket.close()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)
        if self.thread.is_alive():
            self.errors.append("DNS oracle thread did not stop")


class LocalUpstreamOracle:
    """Bounded HTTP/1 origin used only to observe real proxy traffic."""

    def __init__(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.socket.settimeout(0.1)
        self.port = int(self.socket.getsockname()[1])
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._connection_lock = threading.Lock()
        self._active_connection: socket.socket | None = None
        self._observations: list[UpstreamObservation] = []
        self.first_fragment_sent = threading.Event()
        self.allow_second_fragment = threading.Event()
        self.slow_started = threading.Event()
        self.slow_release = threading.Event()
        self.upload_first_body_received = threading.Event()
        self.cancel_started = threading.Event()
        self.cancel_released = threading.Event()
        self.errors: list[str] = []
        self.thread = threading.Thread(
            target=self._serve,
            name="pcc-gateway-upstream-oracle",
        )

    @staticmethod
    def _read_head(connection: socket.socket) -> tuple[bytes, bytes]:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = connection.recv(4096)
            if not chunk:
                raise EOFError("upstream request ended before its headers")
            data.extend(chunk)
            if len(data) > 65536:
                raise ValueError("upstream request headers exceed oracle bound")
        end = data.index(b"\r\n\r\n") + 4
        return bytes(data[:end]), bytes(data[end:])

    def _observe(self, head: bytes) -> UpstreamObservation:
        lines = head[:-4].split(b"\r\n")
        request_line = lines[0].decode("ascii").split(" ")
        if len(request_line) != 3:
            raise ValueError("invalid upstream request line")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if not separator:
                raise ValueError("invalid upstream header")
            headers[name.decode("ascii").lower()] = value.strip().decode("latin1")
        observation = UpstreamObservation(
            request_line[0],
            request_line[1],
            headers,
        )
        with self._lock:
            self._observations.append(observation)
        return observation

    def _handle(self, connection: socket.socket) -> None:
        connection.settimeout(8)
        head, retained_body = self._read_head(connection)
        observation = self._observe(head)
        if observation.target == "/hello":
            connection.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"Connection: close\r\n"
                b"X-Pcc-Upstream: oracle\r\n\r\n"
                b"9\r\nupstream-\r\n"
            )
            self.first_fragment_sent.set()
            if not self.allow_second_fragment.wait(timeout=8):
                raise TimeoutError("client never released second upstream fragment")
            connection.sendall(b"a\r\nfragmented\r\n0\r\n\r\n")
            return
        if observation.target == "/upload":
            expected = int(observation.headers.get("content-length", "-1"))
            if expected != len(b"upload-stream"):
                raise ValueError(f"unexpected upload content length: {expected}")
            body = bytearray(retained_body)
            while not body:
                first = connection.recv(4096)
                if not first:
                    raise EOFError("upload ended before its first body fragment")
                body.extend(first)
            self.upload_first_body_received.set()
            while len(body) < expected:
                chunk = connection.recv(expected - len(body))
                if not chunk:
                    raise EOFError("upload ended before its declared body")
                body.extend(chunk)
            if bytes(body) != b"upload-stream":
                raise ValueError(f"unexpected upload body: {bytes(body)!r}")
            response = b"uploaded:" + bytes(body)
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                + str(len(response)).encode("ascii")
                + b"\r\n\r\n"
                + response
            )
            return
        if observation.target == "/cancel":
            self.cancel_started.set()
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    self.cancel_released.set()
                    return
        if observation.target == "/slow":
            self.slow_started.set()
            if not self.slow_release.wait(timeout=8):
                raise TimeoutError("drain harness never released slow upstream")
            body = b"drained-upstream"
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                + str(len(body)).encode("ascii")
                + b"\r\n\r\n"
                + body
            )
            return
        body = b"not found"
        connection.sendall(
            b"HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 9"
            b"\r\n\r\n" + body
        )

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _peer = self.socket.accept()
            except socket.timeout:
                continue
            except OSError as error:
                if not self._stop.is_set():
                    self.errors.append(f"upstream accept failed: {error}")
                break
            try:
                with self._connection_lock:
                    self._active_connection = connection
                with connection:
                    self._handle(connection)
            except Exception as error:  # oracle diagnostics, never SUT fallback
                self.errors.append(f"upstream oracle failed: {error}")
            finally:
                with self._connection_lock:
                    self._active_connection = None

    def start(self) -> None:
        self.thread.start()

    def observations(self) -> tuple[UpstreamObservation, ...]:
        with self._lock:
            return tuple(self._observations)

    def stop(self) -> None:
        self._stop.set()
        self.allow_second_fragment.set()
        self.slow_release.set()
        self.socket.close()
        with self._connection_lock:
            active = self._active_connection
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active.close()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)
        if self.thread.is_alive():
            self.errors.append("upstream oracle thread did not stop")


class VerifiedHttpsConnection:
    """CA/hostname-verified TLS client with an incremental HTTP/1 parser."""

    def __init__(self, context: ssl.SSLContext, port: int) -> None:
        raw = socket.create_connection(("127.0.0.1", port), timeout=1)
        try:
            self.socket = context.wrap_socket(raw, server_hostname=TLS_SERVER_NAME)
        except BaseException:
            raw.close()
            raise
        self.socket.settimeout(2)
        selected = self.socket.selected_alpn_protocol()
        if selected != "http/1.1":
            self.socket.close()
            raise AssertionError(f"gateway selected unexpected ALPN {selected!r}")
        peer = self.socket.getpeercert(binary_form=True)
        if not peer:
            self.socket.close()
            raise AssertionError("gateway TLS peer did not present a certificate")
        self.peer_fingerprint = hashlib.sha256(peer).hexdigest()
        self.buffer = bytearray()

    def close(self) -> None:
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _receive(self) -> None:
        chunk = self.socket.recv(4096)
        if not chunk:
            raise EOFError("gateway closed before the HTTP response completed")
        self.buffer.extend(chunk)

    def _line(self) -> bytes:
        while True:
            offset = self.buffer.find(b"\r\n")
            if offset >= 0:
                line = bytes(self.buffer[:offset])
                del self.buffer[:offset + 2]
                return line
            if len(self.buffer) > 65536:
                raise ValueError("gateway response line exceeds client bound")
            self._receive()

    def _exact(self, count: int) -> bytes:
        while len(self.buffer) < count:
            self._receive()
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def send_request_head(
        self,
        method: str,
        target: str,
        *,
        close: bool,
        content_length: int = 0,
    ) -> None:
        connection = "close" if close else "keep-alive"
        framing = ""
        if content_length > 0:
            framing = f"Content-Length: {content_length}\r\n"
        self.socket.sendall(
            (
                f"{method} {target} HTTP/1.1\r\n"
                f"Host: {TLS_SERVER_NAME}\r\n"
                f"Connection: {connection}\r\n"
                f"{framing}\r\n"
            ).encode("ascii")
        )

    def send_body(self, data: bytes) -> None:
        self.socket.sendall(data)

    def read_response(self, on_chunk=None) -> HttpResponse:
        status_line = self._line().decode("ascii").split(" ", 2)
        if len(status_line) < 2 or not status_line[0].startswith("HTTP/1."):
            raise ValueError(f"invalid gateway status line: {status_line!r}")
        status = int(status_line[1])
        headers: dict[str, str] = {}
        while True:
            line = self._line()
            if not line:
                break
            name, separator, value = line.partition(b":")
            if not separator:
                raise ValueError("invalid gateway response header")
            lower = name.decode("ascii").lower()
            decoded = value.strip().decode("latin1")
            if lower in headers:
                headers[lower] = headers[lower] + ", " + decoded
            else:
                headers[lower] = decoded

        chunks: list[bytes] = []
        if "chunked" in headers.get("transfer-encoding", "").lower():
            while True:
                size_text = self._line().split(b";", 1)[0]
                size = int(size_text, 16)
                if size == 0:
                    while self._line():
                        pass
                    break
                chunk = self._exact(size)
                if self._exact(2) != b"\r\n":
                    raise ValueError("invalid gateway chunk terminator")
                chunks.append(chunk)
                if on_chunk is not None:
                    on_chunk(chunk)
            body = b"".join(chunks)
        elif "content-length" in headers:
            length = int(headers["content-length"])
            body = self._exact(length)
        else:
            body_parts = [bytes(self.buffer)]
            self.buffer.clear()
            while True:
                chunk = self.socket.recv(4096)
                if not chunk:
                    break
                body_parts.append(chunk)
            body = b"".join(body_parts)
        return HttpResponse(status, headers, body, tuple(chunks))

    def request(self, target: str, *, close: bool, on_chunk=None) -> HttpResponse:
        self.send_request_head("GET", target, close=close)
        return self.read_response(on_chunk=on_chunk)


def _verified_client_context(ca_certificate: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(
        purpose=ssl.Purpose.SERVER_AUTH,
        cafile=str(ca_certificate),
    )
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    return context


def _certificate_fingerprint(certificate: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()


def _reserve_tcp_port() -> tuple[socket.socket, int]:
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    return reservation, int(reservation.getsockname()[1])


def _render_product_fixture(
    target: Path,
    *,
    listen_port: int,
    dns_port: int,
    upstream_port: int,
    waitset_backend: str,
    assets: TlsAssets,
) -> None:
    source = PRODUCT_TEMPLATE.read_text(encoding="utf-8")
    substitutions = {
        "__PCC_LISTEN_PORT__": str(listen_port),
        "__PCC_DNS_PORT__": str(dns_port),
        "__PCC_UPSTREAM_PORT__": str(upstream_port),
        "__PCC_WAITSET_BACKEND__": json.dumps(waitset_backend),
        "__PCC_PROVIDER_LIBRARY__": json.dumps(str(assets.provider)),
        "__PCC_PROVIDER_LIBRARY_SHA256__": json.dumps(
            hashlib.sha256(assets.provider.read_bytes()).hexdigest()
        ),
        "__PCC_CERTIFICATE_OLD__": json.dumps(str(assets.old_certificate)),
        "__PCC_PRIVATE_KEY_OLD__": json.dumps(str(assets.old_private_key)),
        "__PCC_CERTIFICATE_NEW__": json.dumps(str(assets.new_certificate)),
        "__PCC_PRIVATE_KEY_NEW__": json.dumps(str(assets.new_private_key)),
    }
    for token, replacement in substitutions.items():
        if source.count(token) != 1:
            raise AssertionError(f"product fixture token count changed: {token}")
        source = source.replace(token, replacement)
    assert "__PCC_" not in source
    target.write_text(source, encoding="utf-8")


def _native_magic(path: Path) -> None:
    magic = path.read_bytes()[:4]
    supported = {
        b"\x7fELF",
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }
    assert magic in supported, f"artifact is not a native ELF/Mach-O binary: {path}"
    assert os.access(path, os.X_OK), f"native artifact is not executable: {path}"


def _assert_no_foreign_owner(report: str, label: str) -> None:
    lowered = report.lower()
    assert "libpython" not in lowered, f"{label} contains libpython:\n{report}"
    assert "python.framework" not in lowered, (
        f"{label} contains host Python.framework:\n{report}"
    )
    assert "nginx" not in lowered, f"{label} contains nginx:\n{report}"
    python_owner = re.compile(
        r"(?:^|[/\s])python(?:\d+(?:\.\d+)*)?(?:$|[/\s])",
        re.IGNORECASE | re.MULTILINE,
    )
    assert python_owner.search(report) is None, (
        f"{label} contains a host Python executable owner:\n{report}"
    )


def _link_report(path: Path) -> str:
    if sys.platform == "darwin":
        tool = _required_tool("otool", "Darwin dependency closure inspection")
        command = [tool, "-L", path]
    else:
        tool = _required_tool("ldd", "Linux dependency closure inspection")
        command = [tool, path]
    completed = run_process_group_timeout(
        [str(item) for item in command],
        cwd=REPO,
        timeout=15,
    )
    report = completed.stdout + completed.stderr
    static_linux = sys.platform == "linux" and (
        "not a dynamic executable" in report.lower()
        or "statically linked" in report.lower()
    )
    if completed.returncode != 0 and not static_linux:
        _gate_fail(
            f"dependency inspection for {path} failed "
            f"(exit {completed.returncode}):\n{report}"
        )
    assert "not found" not in report.lower(), (
        f"unresolved dependency in {path} closure:\n{report}"
    )
    _assert_no_foreign_owner(report, f"{path.name} link closure")
    return report


def _assert_live_process_ownership(
    process: subprocess.Popen[str],
    executable: Path,
    provider: Path,
) -> None:
    ps = _required_tool("ps", "gateway process ownership inspection")
    process_table = _run_checked(
        [ps, "-ww", "-axo", "pid=,ppid=,pgid=,command="],
        label="gateway process-group inspection",
        timeout=10,
    ).stdout
    process_rows: list[tuple[int, int, int, str]] = []
    group_rows: list[tuple[int, str]] = []
    for line in process_table.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) != 4:
            continue
        try:
            pid = int(fields[0])
            parent_pid = int(fields[1])
            process_group = int(fields[2])
        except ValueError:
            continue
        process_rows.append((pid, parent_pid, process_group, fields[3]))
        if process_group == process.pid:
            group_rows.append((pid, fields[3]))
    assert group_rows == [(process.pid, str(executable))], (
        "the gateway session must contain exactly its native artifact; helper "
        f"processes are forbidden: {group_rows!r}"
    )
    for pid, row_command in group_rows:
        assert pid == process.pid
        _assert_no_foreign_owner(
            row_command,
            f"gateway process-group member {pid}",
        )

    # PGID inspection alone misses a helper that starts a new session.  Walk
    # the live PPID tree as well and reject every descendant, irrespective of
    # its process group.  The product contract permits exactly one native SUT
    # process and no daemonized compiler/Python/nginx helper.
    descendant_pids = {process.pid}
    descendants: list[tuple[int, int, str]] = []
    changed = True
    while changed:
        changed = False
        for pid, parent_pid, process_group, row_command in process_rows:
            if pid in descendant_pids or parent_pid not in descendant_pids:
                continue
            descendant_pids.add(pid)
            descendants.append((pid, process_group, row_command))
            changed = True
    assert descendants == [], (
        "gateway native process must not own child or re-sessioned helper "
        f"processes: {descendants!r}"
    )

    command = _run_checked(
        [ps, "-ww", "-p", str(process.pid), "-o", "command="],
        label="gateway process command inspection",
        timeout=10,
    ).stdout.strip()
    assert command, "gateway process command is empty"
    assert command.split()[0] == str(executable), (
        f"gateway is not directly owned by its native artifact: {command!r}"
    )
    _assert_no_foreign_owner(command, "gateway process command")

    if sys.platform == "linux":
        proc_exe = Path(f"/proc/{process.pid}/exe")
        assert proc_exe.resolve() == executable.resolve()
        maps_path = Path(f"/proc/{process.pid}/maps")
        try:
            modules = maps_path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            _gate_fail(f"cannot inspect live gateway mappings at {maps_path}: {error}")
    else:
        vmmap = _required_tool("vmmap", "Darwin live module closure inspection")
        completed = _run_checked(
            [vmmap, str(process.pid)],
            label="gateway vmmap inspection",
            timeout=20,
        )
        modules = completed.stdout + completed.stderr
    _assert_no_foreign_owner(modules, "gateway live module closure")
    assert provider.name.lower() in modules.lower(), (
        "real TLS handshake completed but the configured OpenSSL provider is "
        f"absent from the live module map:\n{modules}"
    )


def _assert_no_gateway_descendants(process: subprocess.Popen[str]) -> None:
    """Check PPID ownership without requiring startup/TLS to have completed."""

    ps = _required_tool("ps", "gateway descendant inspection")
    process_table = _run_checked(
        [ps, "-ww", "-axo", "pid=,ppid=,command="],
        label="gateway descendant inspection",
        timeout=10,
    ).stdout
    rows: list[tuple[int, int, str]] = []
    for line in process_table.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        try:
            rows.append((int(fields[0]), int(fields[1]), fields[2]))
        except ValueError:
            continue
    owners = {process.pid}
    descendants: list[tuple[int, str]] = []
    changed = True
    while changed:
        changed = False
        for pid, parent_pid, command in rows:
            if pid in owners or parent_pid not in owners:
                continue
            owners.add(pid)
            descendants.append((pid, command))
            changed = True
    assert descendants == [], (
        "gateway native process owned helper descendants outside the PGID "
        f"contract: {descendants!r}"
    )


def _early_process_failure(process: subprocess.Popen[str], phase: str) -> None:
    returncode = process.poll()
    if returncode is None:
        return
    stdout, stderr = process.communicate(timeout=1)
    _gate_fail(
        f"gateway exited during {phase} (exit {returncode})\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )


def _wait_for_gateway(
    process: subprocess.Popen[str],
    context: ssl.SSLContext,
    port: int,
) -> tuple[VerifiedHttpsConnection, HttpResponse]:
    deadline = time.monotonic() + 15
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        _early_process_failure(process, "TLS listener startup")
        connection = None
        try:
            connection = VerifiedHttpsConnection(context, port)
            response = connection.request("/health", close=False)
            if response.status != 200 or response.body != b"pcc-gateway-healthy":
                raise AssertionError(f"unexpected health response: {response}")
            return connection, response
        except (OSError, ssl.SSLError, EOFError, ValueError, AssertionError) as error:
            last_error = error
            if connection is not None:
                connection.close()
            time.sleep(0.025)
    _early_process_failure(process, "TLS listener startup timeout")
    _gate_fail(f"gateway did not become HTTPS-ready in 15s: {last_error}")


def _wait_for_new_certificate(
    process: subprocess.Popen[str],
    context: ssl.SSLContext,
    port: int,
    expected_fingerprint: str,
) -> VerifiedHttpsConnection:
    deadline = time.monotonic() + 8
    seen: list[str] = []
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        _early_process_failure(process, "SIGHUP TLS reload")
        connection = None
        try:
            connection = VerifiedHttpsConnection(context, port)
            seen.append(connection.peer_fingerprint)
            if connection.peer_fingerprint == expected_fingerprint:
                return connection
            connection.close()
        except (OSError, ssl.SSLError, EOFError, AssertionError) as error:
            last_error = error
            if connection is not None:
                connection.close()
        time.sleep(0.025)
    _gate_fail(
        "SIGHUP did not publish the new TLS certificate within 8s; "
        f"fingerprints={seen!r} last_error={last_error!r}"
    )


def _wait_for_listener_close(
    process: subprocess.Popen[str],
    port: int,
) -> None:
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        _early_process_failure(process, "SIGTERM drain before in-flight completion")
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.1)
        try:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        finally:
            probe.close()
        time.sleep(0.025)
    _gate_fail("SIGTERM drain did not close the listener within 1.5s")


def _process_group_exists(process_group: int) -> bool:
    if process_group <= 0:
        return False
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_owned_process(
    process: subprocess.Popen[str] | None,
    process_group: int,
) -> None:
    if process is None and process_group <= 0:
        return
    if _process_group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if process is not None:
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    deadline = time.monotonic() + 2
    while _process_group_exists(process_group) and time.monotonic() < deadline:
        time.sleep(0.025)
    if _process_group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 2
        while _process_group_exists(process_group) and time.monotonic() < deadline:
            time.sleep(0.025)
    if process is not None:
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                if process.poll() is None:
                    process.kill()
            except ProcessLookupError:
                pass
            process.communicate(timeout=1)


def _assert_process_group_gone(process_group: int) -> None:
    assert not _process_group_exists(process_group), (
        f"gateway process group {process_group} survived shutdown"
    )


def _assert_native_process_gone(process_pid: int, executable: Path) -> None:
    """Reject a surviving PID or re-sessioned copy of the native artifact."""

    ps = _required_tool("ps", "gateway post-shutdown process inspection")
    process_table = _run_checked(
        [ps, "-ww", "-axo", "pid=,command="],
        label="gateway post-shutdown process inspection",
        timeout=10,
    ).stdout
    survivors: list[tuple[int, str]] = []
    executable_text = str(executable)
    for line in process_table.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        command = fields[1]
        command_head = command.split(None, 1)[0] if command else ""
        if pid == process_pid or command_head == executable_text:
            survivors.append((pid, command))
    assert survivors == [], (
        "gateway PID or a re-sessioned native artifact survived shutdown: "
        f"{survivors!r}"
    )


def _assert_port_bindable(port: int, socket_type: int) -> None:
    probe = socket.socket(socket.AF_INET, socket_type)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
    finally:
        probe.close()


def _parse_metrics(stdout: str) -> tuple[int, ...]:
    lines = [line for line in stdout.splitlines() if line.startswith(PRODUCT_MARKER)]
    assert len(lines) == 1, (
        f"expected one final {PRODUCT_MARKER} marker after graceful exit:\n{stdout}"
    )
    fields = lines[0].split()
    assert len(fields) == 10, f"gateway product marker shape changed: {lines[0]!r}"
    return tuple(int(field) for field in fields[1:])


def _parse_gc_backend(stdout: str) -> int:
    lines = [line for line in stdout.splitlines() if line.startswith(GC_MARKER)]
    assert len(lines) == 1, (
        f"expected one final {GC_MARKER} marker from the native process:\n{stdout}"
    )
    fields = lines[0].split()
    assert len(fields) == 2, f"gateway GC marker shape changed: {lines[0]!r}"
    return int(fields[1])


def _parse_drain_forced(stdout: str) -> int:
    lines = [line for line in stdout.splitlines() if line.startswith(DRAIN_MARKER)]
    assert len(lines) == 1, (
        f"expected one final {DRAIN_MARKER} marker from the native process:\n{stdout}"
    )
    fields = lines[0].split()
    assert len(fields) == 2, f"gateway drain marker shape changed: {lines[0]!r}"
    return int(fields[1])


def _parse_waitset_backend(stdout: str) -> int:
    lines = [line for line in stdout.splitlines() if line.startswith(WAITSET_MARKER)]
    assert len(lines) == 1, (
        f"expected one final {WAITSET_MARKER} marker from the native process:\n{stdout}"
    )
    fields = lines[0].split()
    assert len(fields) == 2, f"gateway waitset marker shape changed: {lines[0]!r}"
    return int(fields[1])


def _parse_resource_closure(
    stdout: str,
) -> tuple[int, int, int, int, int, int, int, int, int, int, int]:
    lines = [line for line in stdout.splitlines() if line.startswith(RESOURCE_MARKER)]
    assert len(lines) == 1, (
        f"expected one final {RESOURCE_MARKER} marker from the native process:\n"
        f"{stdout}"
    )
    fields = lines[0].split()
    assert len(fields) == 12, f"gateway resource marker shape changed: {lines[0]!r}"
    return tuple(int(field) for field in fields[1:])


def _best_effort_cleanup(cleanup_errors, label: str, action, *args) -> None:
    """Run one independent cleanup/check without stranding later owners."""

    try:
        action(*args)
    except BaseException as error:
        cleanup_errors.append((label, error))


def _assert_thread_stopped(thread: threading.Thread | None, label: str) -> None:
    if thread is None:
        return
    thread.join(timeout=4)
    assert not thread.is_alive(), f"{label} survived product-gate cleanup"


def _assert_oracle_clean(errors: list[str], label: str) -> None:
    assert not errors, f"{label} errors: {errors!r}"


def _report_cleanup_errors(
    cleanup_errors: list[tuple[str, BaseException]],
    primary_error: BaseException | None,
) -> None:
    if not cleanup_errors:
        return
    details = []
    for label, error in cleanup_errors:
        details.append(f"{label}: {type(error).__name__}: {error}")
    message = "gateway product cleanup failures:\n" + "\n".join(details)
    if primary_error is not None:
        add_note = getattr(primary_error, "add_note", None)
        if callable(add_note):
            add_note(message)
            return
    _gate_fail(message)


@pytest.mark.parametrize(
    "gc_backend",
    ("0", "1", "2", "3", "4"),
    ids=lambda value: f"gc{value}",
)
def test_current_pcc1_native_https_gateway_product_canary(
    tmp_path: Path,
    gc_backend: str,
    gateway_product_tls_assets: TlsAssets,
    gateway_product_runtime_archive: Path,
) -> None:
    """Exercise one separately compiled/live current-pcc1 gateway process."""

    pcc1 = find_current_pcc1(REPO)
    if pcc1 is None:
        _gate_fail("current pcc1 is required for the selected gateway product gate")
    pcc1 = pcc1.resolve()
    _native_magic(pcc1)
    _link_report(pcc1)
    _link_report(gateway_product_tls_assets.provider)

    dns = LocalDnsOracle()
    upstream = LocalUpstreamOracle()
    listener_reservation, listen_port = _reserve_tcp_port()
    source = tmp_path / f"gateway_product_gc{gc_backend}.py"
    executable = tmp_path / f"gateway_product_gc{gc_backend}"
    waitset_backend = "kqueue" if sys.platform == "darwin" else "epoll"
    _render_product_fixture(
        source,
        listen_port=listen_port,
        dns_port=dns.port,
        upstream_port=upstream.port,
        waitset_backend=waitset_backend,
        assets=gateway_product_tls_assets,
    )
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    environment.update(
        {
            "PCC_RUNTIME_ARCHIVE": str(gateway_product_runtime_archive),
            "PCC_WITH_THREADS": "1",
            "PCC_GC_BACKEND": gc_backend,
            "PCC_VTHREAD_IO_BACKEND": waitset_backend,
        }
    )
    compile_command = [
        pcc1,
        "--backend",
        "self",
        "--python-libpython=off",
        "--ir-scaffold=on",
        source,
        "-o",
        executable,
    ]

    process: subprocess.Popen[str] | None = None
    old_connection: VerifiedHttpsConnection | None = None
    new_connection: VerifiedHttpsConnection | None = None
    cancel_connection: VerifiedHttpsConnection | None = None
    slow_thread: threading.Thread | None = None
    proxy_thread: threading.Thread | None = None
    slow_result: list[HttpResponse] = []
    slow_errors: list[BaseException] = []
    proxy_result: list[HttpResponse] = []
    proxy_errors: list[BaseException] = []
    client_proxy_chunks: list[bytes] = []
    client_first_proxy_chunk = threading.Event()
    stdout = ""
    stderr = ""
    process_group = -1

    try:
        _run_checked(
            compile_command,
            label=f"current-pcc1 self/no-libpython gateway compile for GC{gc_backend}",
            timeout=600,
            env=environment,
        )
        assert executable.is_file()
        _native_magic(executable)
        _link_report(executable)

        dns.start()
        upstream.start()
        client_context = _verified_client_context(
            gateway_product_tls_assets.ca_certificate
        )
        old_expected = _certificate_fingerprint(
            gateway_product_tls_assets.old_certificate
        )
        new_expected = _certificate_fingerprint(
            gateway_product_tls_assets.new_certificate
        )
        assert old_expected != new_expected

        # Hold the candidate port through compile/setup.  The production ABI
        # cannot inherit a pre-bound test fd, so a narrow close/exec race is an
        # explicit harness boundary rather than something claimed away here.
        listener_reservation.close()
        process = subprocess.Popen(
            [str(executable)],
            cwd=REPO,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        process_group = process.pid
        old_connection, health = _wait_for_gateway(
            process, client_context, listen_port
        )
        assert health.status == 200
        assert old_connection.peer_fingerprint == old_expected
        _assert_live_process_ownership(
            process,
            executable,
            gateway_product_tls_assets.provider,
        )

        with VerifiedHttpsConnection(client_context, listen_port) as stream_client:
            streamed = stream_client.request("/stream", close=True)
        assert streamed.status == 200
        assert streamed.headers.get("transfer-encoding", "").lower() == "chunked"
        assert streamed.chunks == (b"stream-", b"from-", b"pcc1")
        assert streamed.body == b"stream-from-pcc1"

        def observe_proxy_chunk(chunk: bytes) -> None:
            client_proxy_chunks.append(chunk)
            client_first_proxy_chunk.set()

        def proxy_request() -> None:
            try:
                with VerifiedHttpsConnection(client_context, listen_port) as client:
                    proxy_result.append(
                        client.request(
                            "/api/hello",
                            close=True,
                            on_chunk=observe_proxy_chunk,
                        )
                    )
            except BaseException as error:
                proxy_errors.append(error)

        proxy_thread = threading.Thread(
            target=proxy_request,
            name=f"gateway-proxy-client-gc{gc_backend}",
        )
        proxy_thread.start()
        assert upstream.first_fragment_sent.wait(timeout=5), (
            "real proxy request did not reach the local upstream"
        )
        assert client_first_proxy_chunk.wait(timeout=5), (
            "gateway did not forward the first upstream body fragment before EOF"
        )
        assert client_proxy_chunks == [b"upstream-"]
        upstream.allow_second_fragment.set()
        proxy_thread.join(timeout=5)
        assert not proxy_thread.is_alive(), "proxy client thread did not finish"
        assert not proxy_errors, proxy_errors
        assert len(proxy_result) == 1
        proxied = proxy_result[0]
        assert proxied.status == 200
        assert proxied.headers.get("x-pcc-upstream") == "oracle"
        assert proxied.chunks == (b"upstream-", b"fragmented")
        assert proxied.body == b"upstream-fragmented"

        observations = upstream.observations()
        assert observations
        hello = observations[0]
        assert hello.method == "GET"
        assert hello.target == "/hello"
        assert hello.headers.get("host") == f"{DNS_NAME}:{upstream.port}"
        assert hello.headers.get("x-forwarded-proto") == "https"
        assert hello.headers.get("x-forwarded-host") == TLS_SERVER_NAME
        assert hello.headers.get("x-forwarded-for") == "127.0.0.1"
        dns_observations = dns.observations()
        assert dns_observations
        assert all(item.name == DNS_NAME for item in dns_observations)
        assert all(item.query_type == 1 for item in dns_observations)
        assert all(item.query_class == 1 for item in dns_observations)
        assert all(item.transaction_id != 0 for item in dns_observations)
        assert all(
            item.response_transaction_id == item.transaction_id
            for item in dns_observations
        )

        # Client-to-upstream streaming is proven by withholding the tail until
        # the origin has observed the first body fragment.  A gateway that
        # waits for RequestEnd or buffers the whole body cannot pass this.
        upload_body = b"upload-stream"
        with VerifiedHttpsConnection(client_context, listen_port) as upload_client:
            upload_client.send_request_head(
                "POST",
                "/api/upload",
                close=True,
                content_length=len(upload_body),
            )
            upload_client.send_body(b"upload-")
            assert upstream.upload_first_body_received.wait(timeout=3), (
                "origin did not receive the first request-body fragment before "
                "the client released the tail"
            )
            upload_client.send_body(b"stream")
            uploaded = upload_client.read_response()
        assert uploaded.status == 200
        assert uploaded.body == b"uploaded:upload-stream"

        os.kill(process.pid, signal.SIGHUP)
        new_connection = _wait_for_new_certificate(
            process,
            client_context,
            listen_port,
            new_expected,
        )
        reloaded_health = new_connection.request("/health", close=True)
        assert reloaded_health.status == 200
        assert reloaded_health.body == b"pcc-gateway-healthy"
        new_connection.close()
        new_connection = None

        # The reload publishes a new generation for new handshakes; the
        # already-admitted old-generation keepalive connection must survive.
        old_health = old_connection.request("/health", close=False)
        assert old_health.status == 200
        assert old_health.body == b"pcc-gateway-healthy"
        assert old_connection.peer_fingerprint == old_expected
        old_connection.close()
        old_connection = None

        # A downstream disconnect must cancel its stalled upstream lease; a
        # shutdown-only cleanup would leave this origin connection open.
        cancel_connection = VerifiedHttpsConnection(client_context, listen_port)
        cancel_connection.send_request_head("GET", "/api/cancel", close=True)
        assert upstream.cancel_started.wait(timeout=5), (
            "cancel probe did not become active at the upstream"
        )
        cancel_connection.close()
        cancel_connection = None
        assert upstream.cancel_released.wait(timeout=1), (
            "downstream disconnect did not promptly close the upstream lease"
        )

        def slow_request() -> None:
            try:
                with VerifiedHttpsConnection(client_context, listen_port) as client:
                    slow_result.append(client.request("/api/slow", close=True))
            except BaseException as error:
                slow_errors.append(error)

        slow_thread = threading.Thread(
            target=slow_request,
            name=f"gateway-drain-client-gc{gc_backend}",
        )
        slow_thread.start()
        assert upstream.slow_started.wait(timeout=5), (
            "slow proxy request did not become in-flight at the upstream"
        )
        _assert_live_process_ownership(
            process,
            executable,
            gateway_product_tls_assets.provider,
        )
        os.kill(process.pid, signal.SIGTERM)
        time.sleep(0.025)
        _wait_for_listener_close(process, listen_port)
        assert process.poll() is None, (
            "gateway exited before its admitted slow request was released"
        )
        upstream.slow_release.set()
        slow_thread.join(timeout=5)
        assert not slow_thread.is_alive(), "draining TLS client thread did not finish"
        assert not slow_errors, slow_errors
        assert len(slow_result) == 1
        assert slow_result[0].status == 200
        assert slow_result[0].body == b"drained-upstream"

        stdout, stderr = process.communicate(timeout=10)
        returncode = process.returncode
        assert returncode == 0, (
            f"gateway GC{gc_backend} did not drain cleanly (exit {returncode})\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )
        (
            requests_started,
            dns_queries,
            tls_handshakes,
            tls_reloads,
            connections_active,
            requests_active,
            requests_queued,
            buffered_bytes,
            upstream_active,
        ) = _parse_metrics(stdout)
        assert _parse_gc_backend(stdout) == int(gc_backend)
        expected_waitset = 1 if waitset_backend == "kqueue" else 2
        assert _parse_waitset_backend(stdout) == expected_waitset
        assert _parse_drain_forced(stdout) == 0
        assert _parse_resource_closure(stdout) == (
            0,  # upstream leases
            0,  # idle upstream sockets
            0,  # active gateway-generation references
            1,  # current gateway generation released
            0,  # server-owned connection objects
            0,  # scheduler-visible connection virtual-thread owners
            0,  # lifecycle-retired generations awaiting collection
            1,  # TLS generation manager closed
            1,  # native TLS provider closed/library unload eligible
            0,  # native TLS contexts
            0,  # native TLS sessions
        )
        assert requests_started == 8
        assert dns_queries == 4
        assert tls_handshakes >= 7
        assert tls_reloads == 1
        assert (
            connections_active,
            requests_active,
            requests_queued,
            buffered_bytes,
            upstream_active,
        ) == (0, 0, 0, 0, 0)
        assert len(dns.observations()) == 4
        assert [item.target for item in upstream.observations()] == [
            "/hello",
            "/upload",
            "/cancel",
            "/slow",
        ]
    finally:
        # Never let one failed close/assertion strand the remaining sockets,
        # threads, process, or ports.  Preserve the original product failure
        # and attach the complete cleanup report to it when possible.
        primary_error = sys.exc_info()[1]
        cleanup_errors: list[tuple[str, BaseException]] = []
        upstream.allow_second_fragment.set()
        upstream.slow_release.set()
        if old_connection is not None:
            _best_effort_cleanup(
                cleanup_errors,
                "old TLS client close",
                old_connection.close,
            )
        if new_connection is not None:
            _best_effort_cleanup(
                cleanup_errors,
                "new TLS client close",
                new_connection.close,
            )
        if cancel_connection is not None:
            _best_effort_cleanup(
                cleanup_errors,
                "cancel TLS client close",
                cancel_connection.close,
            )
        if process is not None and process.poll() is None:
            _best_effort_cleanup(
                cleanup_errors,
                "pre-termination native ownership",
                _assert_no_gateway_descendants,
                process,
            )
        _best_effort_cleanup(
            cleanup_errors,
            "gateway process termination",
            _terminate_owned_process,
            process,
            process_group,
        )
        _best_effort_cleanup(
            cleanup_errors,
            "proxy client thread join",
            _assert_thread_stopped,
            proxy_thread,
            "proxy client thread",
        )
        _best_effort_cleanup(
            cleanup_errors,
            "slow client thread join",
            _assert_thread_stopped,
            slow_thread,
            "slow client thread",
        )
        _best_effort_cleanup(
            cleanup_errors,
            "listener reservation close",
            listener_reservation.close,
        )
        _best_effort_cleanup(cleanup_errors, "DNS oracle stop", dns.stop)
        _best_effort_cleanup(
            cleanup_errors,
            "upstream oracle stop",
            upstream.stop,
        )
        _best_effort_cleanup(
            cleanup_errors,
            "DNS oracle thread closure",
            _assert_thread_stopped,
            dns.thread,
            "DNS oracle thread",
        )
        _best_effort_cleanup(
            cleanup_errors,
            "upstream oracle thread closure",
            _assert_thread_stopped,
            upstream.thread,
            "upstream oracle thread",
        )
        if process_group > 0:
            _best_effort_cleanup(
                cleanup_errors,
                "gateway process-group closure",
                _assert_process_group_gone,
                process_group,
            )
        if process is not None:
            _best_effort_cleanup(
                cleanup_errors,
                "gateway native-process closure",
                _assert_native_process_gone,
                process.pid,
                executable,
            )
        _best_effort_cleanup(
            cleanup_errors,
            "gateway listen-port release",
            _assert_port_bindable,
            listen_port,
            socket.SOCK_STREAM,
        )
        _best_effort_cleanup(
            cleanup_errors,
            "upstream listen-port release",
            _assert_port_bindable,
            upstream.port,
            socket.SOCK_STREAM,
        )
        _best_effort_cleanup(
            cleanup_errors,
            "DNS listen-port release",
            _assert_port_bindable,
            dns.port,
            socket.SOCK_DGRAM,
        )
        _best_effort_cleanup(
            cleanup_errors,
            "DNS oracle diagnostics",
            _assert_oracle_clean,
            dns.errors,
            "DNS oracle",
        )
        _best_effort_cleanup(
            cleanup_errors,
            "upstream oracle diagnostics",
            _assert_oracle_clean,
            upstream.errors,
            "upstream oracle",
        )
        _best_effort_cleanup(
            cleanup_errors,
            "proxy client diagnostics",
            _assert_oracle_clean,
            [str(error) for error in proxy_errors],
            "proxy client",
        )
        _best_effort_cleanup(
            cleanup_errors,
            "slow client diagnostics",
            _assert_oracle_clean,
            [str(error) for error in slow_errors],
            "slow client",
        )
        _report_cleanup_errors(cleanup_errors, primary_error)

    assert process_group > 0
