"""Strict process-isolated transport/collective gate.

The historical filename is retained so task-board and CI routing stay stable.
The proof itself needs no second Mac: every rank owns an independent spawned
process, TCP endpoint, authenticated admission state, and collective owner.
"""
from __future__ import annotations

import multiprocessing
import os
import socket

from pcc.dist import transport


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
        "strict-process-isolated",
        [
            {
                "rank": rank,
                "host": f"127.0.0.1:{port}",
                "transport": "tcp-ring",
            }
            for rank, port in enumerate(ports)
        ],
    )


def _strict_process_worker(manifest, rank: int, key: bytes, start, results) -> None:
    owner = None
    try:
        owner = transport.select_collective_owner(
            "tcp-ring",
            manifest,
            rank,
            admission_key=key,
            connect_timeout_s=5.0,
            io_timeout_s=5.0,
        )
        if not start.wait(5.0):
            raise RuntimeError("start event timed out")
        with owner:
            reduced, m0 = owner.allreduce([rank + 1, 10 - rank], "sum")
            scattered, m1 = owner.reduce_scatter([rank + 1] * 4, "sum")
            gathered, m2 = owner.all_gather([rank, rank + 10])
            broadcast, m3 = owner.broadcast([100 + rank], root=0)
            m4 = owner.barrier()
        executions = (m0, m1, m2, m3, m4)
        results.put(
            (
                "ok",
                rank,
                os.getpid(),
                reduced,
                scattered,
                gathered,
                broadcast,
                all(
                    execution.rank == rank
                    and execution.world_size == 2
                    and execution.requested_backend == "tcp-ring"
                    and execution.actual_backend == "tcp-ring"
                    and execution.fallback_used is False
                    and execution.rounds == 1
                    for execution in executions
                ),
                owner.selection.scope,
                owner.selection.authenticated,
                owner.selection.secure,
                owner.transport.is_open,
            )
        )
    except BaseException as exc:
        results.put(("error", rank, os.getpid(), type(exc).__name__, str(exc)))
    finally:
        if owner is not None:
            owner.close()


def test_two_process_authenticated_transport_collective_vectors():
    manifest = _manifest(_free_loopback_ports(2))
    key = bytes(range(32))
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    children = [
        ctx.Process(
            target=_strict_process_worker,
            args=(manifest, rank, key, start, results),
        )
        for rank in range(2)
    ]
    try:
        for child in children:
            child.start()
        start.set()
        received = [results.get(timeout=15.0) for _ in children]
        assert all(item[0] == "ok" for item in received), received
        ordered = sorted(received, key=lambda item: item[1])
        assert len({item[2] for item in ordered}) == 2
        for rank, item in enumerate(ordered):
            assert item[1] == rank
            assert item[3:7] == ([3, 19], [3, 3], [0, 10, 1, 11], [100])
            assert item[7:] == (
                True,
                "localhost-multiprocess",
                True,
                False,
                False,
            )
        for child in children:
            child.join(timeout=3.0)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=1.0)
        results.close()
