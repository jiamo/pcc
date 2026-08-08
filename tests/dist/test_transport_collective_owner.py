from __future__ import annotations

import multiprocessing
import socket
import time

import pytest

from pcc.dist import transport
from pcc.dist.tcp_transport import TCPTransportTimeout
from pcc.dist.transport_collective import (
    CancellationToken,
    CollectiveCancelled,
    TransportCollectiveError,
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


def _manifest(ports: list[int]):
    return transport.build_manifest(
        "collective-owner",
        [
            {
                "rank": rank,
                "host": f"127.0.0.1:{port}",
                "transport": "tcp-ring",
            }
            for rank, port in enumerate(ports)
        ],
    )


def _collective_worker(manifest, rank: int, start, results) -> None:
    try:
        owner = transport.select_collective_owner(
            "tcp-ring",
            manifest,
            rank,
            connect_timeout_s=3.0,
            io_timeout_s=3.0,
        )
        if not start.wait(3.0):
            raise RuntimeError("start event timed out")
        with owner:
            # Ascending-rank float order is observable: (1e16 + -1e16) + 1 == 1.
            allreduce_inputs = ([1.0e16], [-1.0e16], [1.0])
            reduced, m0 = owner.allreduce(allreduce_inputs[rank], "sum")
            scattered, m1 = owner.reduce_scatter([rank + 1] * 6, "sum")
            gathered, m2 = owner.all_gather([rank, rank + 10])
            broadcast, m3 = owner.broadcast([100 + rank], root=2)
            m4 = owner.barrier()
            results.put(
                (
                    "ok",
                    rank,
                    reduced,
                    scattered,
                    gathered,
                    broadcast,
                    [m.operation_sequence for m in (m0, m1, m2, m3, m4)],
                    all(
                        m.requested_backend == m.actual_backend == "tcp-ring"
                        and m.fallback_used is False
                        and m.world_size == 3
                        and m.rounds == 2
                        for m in (m0, m1, m2, m3, m4)
                    ),
                )
            )
    except BaseException as exc:
        results.put(("error", rank, type(exc).__name__, str(exc)))


def _idle_collective_peer(manifest, ready, results) -> None:
    try:
        owner = transport.select_collective_owner(
            "tcp-ring",
            manifest,
            1,
            connect_timeout_s=3.0,
            io_timeout_s=0.15,
        )
        with owner:
            ready.set()
            time.sleep(0.5)
        results.put(("ok", 1))
    except BaseException as exc:
        results.put(("error", 1, type(exc).__name__, str(exc)))


def test_three_process_tcp_owner_matches_all_five_collective_oracles():
    manifest = _manifest(_free_loopback_ports(3))
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    children = [
        ctx.Process(target=_collective_worker, args=(manifest, rank, start, results))
        for rank in range(3)
    ]
    try:
        for child in children:
            child.start()
        start.set()
        received = [results.get(timeout=10.0) for _ in children]
        assert sorted(received) == [
            (
                "ok",
                rank,
                [1.0],
                [6, 6],
                [0, 10, 1, 11, 2, 12],
                [102],
                [0, 1, 2, 3, 4],
                True,
            )
            for rank in range(3)
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


def test_collective_cancellation_is_checked_before_ring_traffic():
    manifest = _manifest(_free_loopback_ports(1))
    owner = transport.select_collective_owner("tcp-ring", manifest, 0)
    token = CancellationToken()
    token.cancel()
    with owner:
        with pytest.raises(CollectiveCancelled, match="cancelled"):
            owner.allreduce([1], cancel=token)


def test_collective_receive_from_nonparticipating_peer_times_out():
    manifest = _manifest(_free_loopback_ports(2))
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    results = ctx.Queue()
    peer = ctx.Process(target=_idle_collective_peer, args=(manifest, ready, results))
    peer.start()
    owner = transport.select_collective_owner(
        "tcp-ring",
        manifest,
        0,
        connect_timeout_s=3.0,
        io_timeout_s=0.15,
    )
    try:
        with owner:
            assert ready.wait(3.0)
            started = time.monotonic()
            with pytest.raises(TCPTransportTimeout, match="read timed out"):
                owner.allreduce([1])
            assert time.monotonic() - started < 1.0
        assert results.get(timeout=3.0) == ("ok", 1)
        peer.join(timeout=2.0)
        assert peer.exitcode == 0
    finally:
        if peer.is_alive():
            peer.terminate()
        peer.join(timeout=1.0)
        results.close()


def test_collective_envelope_rejects_operation_mismatch_and_non_pod_values():
    owner = transport.select_collective_owner(
        "tcp-ring", _manifest(_free_loopback_ports(1)), 0
    )
    packet = owner._encode_packet("allreduce", 4, 0, [1])
    with pytest.raises(TransportCollectiveError, match="operation mismatch"):
        owner._decode_packet(packet, "broadcast", 4)
    with pytest.raises(Exception, match="int/float POD"):
        with owner:
            owner.allreduce(["not-pod"])

