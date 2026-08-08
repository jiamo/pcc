from __future__ import annotations

import multiprocessing
import socket
import time

import pytest

from pcc.dist import transport
from pcc.dist.results import DistUnavailableError, STATUS_SKIPPED
from pcc.dist.tcp_transport import (
    FrameCodec,
    FrameIntegrityError,
    TCPTransportError,
    TCPTransportTimeout,
)


def _free_loopback_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _manifest_for_ports(ports: list[int], cluster_id: str = "local-owner"):
    return transport.build_manifest(
        cluster_id,
        [
            {
                "rank": rank,
                "host": f"127.0.0.1:{port}",
                "transport": "tcp-ring",
            }
            for rank, port in enumerate(ports)
        ],
    )


def _ring_worker(manifest, rank: int, start, results) -> None:
    try:
        owner = transport.select_owner(
            "tcp-ring",
            manifest,
            rank,
            connect_timeout_s=3.0,
            io_timeout_s=3.0,
        )
        if not start.wait(3.0):
            raise RuntimeError("start event timed out")
        with owner:
            owner.send(f"from-rank-{rank}".encode())
            received = owner.recv().decode()
            results.put(
                (
                    "ok",
                    rank,
                    received,
                    owner.selection.requested_backend,
                    owner.selection.actual_backend,
                    owner.selection.fallback_used,
                )
            )
    except BaseException as exc:
        results.put(("error", rank, type(exc).__name__, str(exc)))


def _authenticated_ring_worker(manifest, rank: int, start, results, key: bytes) -> None:
    try:
        owner = transport.select_owner(
            "tcp-ring",
            manifest,
            rank,
            connect_timeout_s=3.0,
            io_timeout_s=3.0,
            allow_remote=True,
            admission_key=key,
        )
        if not start.wait(3.0):
            raise RuntimeError("start event timed out")
        with owner:
            owner.send(f"authenticated-rank-{rank}".encode())
            results.put(
                (
                    "ok",
                    rank,
                    owner.recv().decode(),
                    owner.selection.scope,
                    owner.selection.authenticated,
                    owner.selection.secure,
                )
            )
    except BaseException as exc:
        results.put(("error", rank, type(exc).__name__, str(exc)))


def test_explicit_owner_selection_is_exact_and_default_probe_stays_fail_closed():
    manifest = _manifest_for_ports(_free_loopback_ports(1))
    assert transport.probe("tcp-ring").status == STATUS_SKIPPED

    owner = transport.select_owner("tcp-ring", manifest, 0)
    assert owner.selection.requested_backend == "tcp-ring"
    assert owner.selection.actual_backend == "tcp-ring"
    assert owner.selection.fallback_used is False
    assert owner.selection.scope == "localhost-multiprocess"
    assert owner.selection.secure is False

    with pytest.raises(DistUnavailableError):
        transport.select_owner("quic", manifest, 0)
    with pytest.raises(transport.ManifestError):
        transport.select_owner("not-a-backend", manifest, 0)


def test_owner_rejects_non_loopback_or_mixed_backend_manifests():
    remote = transport.build_manifest(
        "remote",
        [{"rank": 0, "host": "192.0.2.1:7000", "transport": "tcp-ring"}],
    )
    with pytest.raises(TCPTransportError, match="localhost-only"):
        transport.select_owner("tcp-ring", remote, 0)

    mixed = transport.build_manifest(
        "mixed",
        [{"rank": 0, "host": "127.0.0.1:7000", "transport": "quic"}],
    )
    with pytest.raises(TCPTransportError, match="expected 'tcp-ring'"):
        transport.select_owner("tcp-ring", mixed, 0)


def test_remote_owner_requires_authenticated_admission_and_never_claims_tls():
    remote = transport.build_manifest(
        "remote-authenticated",
        [
            {"rank": 0, "host": "192.0.2.10:7000", "transport": "tcp-ring"},
            {"rank": 1, "host": "192.0.2.11:7000", "transport": "tcp-ring"},
        ],
    )
    with pytest.raises(TCPTransportError, match="authenticated admission"):
        transport.select_owner("tcp-ring", remote, 0, allow_remote=True)
    with pytest.raises(TCPTransportError, match="at least 256 bits"):
        transport.select_owner(
            "tcp-ring", remote, 0, allow_remote=True, admission_key=b"short"
        )
    owner = transport.select_owner(
        "tcp-ring", remote, 0, allow_remote=True, admission_key=b"a" * 32
    )
    assert owner.selection.scope == "multi-host-authenticated"
    assert owner.selection.authenticated is True
    assert owner.selection.secure is False


def test_frame_codec_rejects_payload_rank_sequence_and_digest_corruption():
    frame = FrameCodec.encode(
        b"payload",
        source_rank=0,
        destination_rank=1,
        sequence=4,
        max_frame_bytes=64,
    )
    assert FrameCodec.decode(
        frame,
        expected_source=0,
        expected_destination=1,
        expected_sequence=4,
        max_frame_bytes=64,
    ) == b"payload"

    with pytest.raises(FrameIntegrityError, match="rank mismatch"):
        FrameCodec.decode(
            frame,
            expected_source=2,
            expected_destination=1,
            expected_sequence=4,
            max_frame_bytes=64,
        )
    with pytest.raises(FrameIntegrityError, match="sequence"):
        FrameCodec.decode(
            frame,
            expected_source=0,
            expected_destination=1,
            expected_sequence=5,
            max_frame_bytes=64,
        )
    corrupted = frame[:-1] + bytes([frame[-1] ^ 0xFF])
    with pytest.raises(FrameIntegrityError, match="SHA-256"):
        FrameCodec.decode(
            corrupted,
            expected_source=0,
            expected_destination=1,
            expected_sequence=4,
            max_frame_bytes=64,
        )
    with pytest.raises(FrameIntegrityError, match="exceeds limit"):
        FrameCodec.encode(
            b"too large",
            source_rank=0,
            destination_rank=1,
            sequence=0,
            max_frame_bytes=2,
        )


def test_handshake_is_bound_to_cluster_manifest():
    ports = _free_loopback_ports(1)
    owner_a = transport.select_owner("tcp-ring", _manifest_for_ports(ports, "a"), 0)
    owner_b = transport.select_owner("tcp-ring", _manifest_for_ports(ports, "b"), 0)
    with pytest.raises(FrameIntegrityError, match="manifest mismatch"):
        owner_a._validate_handshake(owner_b._handshake(0, 0))


def test_authenticated_handshake_binds_fresh_challenge_manifest_rank_and_key():
    manifest = _manifest_for_ports(_free_loopback_ports(1), "authenticated")
    owner_a = transport.select_owner(
        "tcp-ring",
        manifest,
        0,
        allow_remote=True,
        admission_key=b"a" * 32,
    )
    owner_b = transport.select_owner(
        "tcp-ring",
        manifest,
        0,
        allow_remote=True,
        admission_key=b"b" * 32,
    )
    challenge = owner_a._auth_challenge()
    frame = owner_a._authenticated_handshake(challenge, 0, 0)
    owner_a._validate_authenticated_handshake(frame, challenge)
    with pytest.raises(FrameIntegrityError, match="authentication failed"):
        owner_a._validate_authenticated_handshake(
            owner_b._authenticated_handshake(challenge, 0, 0), challenge
        )
    with pytest.raises(FrameIntegrityError, match="authentication failed"):
        owner_a._validate_authenticated_handshake(frame, owner_a._auth_challenge())


def test_two_process_localhost_ring_sends_rank_bound_frames_without_fallback():
    manifest = _manifest_for_ports(_free_loopback_ports(2))
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    children = [
        ctx.Process(target=_ring_worker, args=(manifest, rank, start, results))
        for rank in range(2)
    ]
    try:
        for child in children:
            child.start()
        start.set()
        received = [results.get(timeout=8.0) for _ in children]
        assert sorted(received) == [
            ("ok", 0, "from-rank-1", "tcp-ring", "tcp-ring", False),
            ("ok", 1, "from-rank-0", "tcp-ring", "tcp-ring", False),
        ]
        for child in children:
            child.join(timeout=3.0)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=1.0)
        results.close()


def test_two_process_ring_executes_authenticated_admission_before_frames():
    manifest = _manifest_for_ports(_free_loopback_ports(2), "auth-owner")
    key = bytes(range(32))
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    children = [
        ctx.Process(
            target=_authenticated_ring_worker,
            args=(manifest, rank, start, results, key),
        )
        for rank in range(2)
    ]
    try:
        for child in children:
            child.start()
        start.set()
        received = [results.get(timeout=8.0) for _ in children]
        assert sorted(received) == [
            (
                "ok",
                0,
                "authenticated-rank-1",
                "multi-host-authenticated",
                True,
                False,
            ),
            (
                "ok",
                1,
                "authenticated-rank-0",
                "multi-host-authenticated",
                True,
                False,
            ),
        ]
        for child in children:
            child.join(timeout=3.0)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=1.0)
        results.close()


def test_connect_timeout_is_bounded_and_close_is_idempotent():
    # Rank 0 listens successfully but rank 1 never starts, so outbound connect
    # must expire at the configured owner deadline and clean up its listener.
    manifest = _manifest_for_ports(_free_loopback_ports(2))
    owner = transport.select_owner(
        "tcp-ring",
        manifest,
        0,
        connect_timeout_s=0.15,
        io_timeout_s=0.15,
    )
    started = time.monotonic()
    with pytest.raises(TCPTransportTimeout, match="connect"):
        owner.open()
    assert time.monotonic() - started < 1.0
    assert owner.is_open is False
    owner.close()
    owner.close()
