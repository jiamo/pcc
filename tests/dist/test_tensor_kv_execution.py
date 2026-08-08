from __future__ import annotations

import hashlib
import multiprocessing
import socket
from pathlib import Path

import pytest

from pcc.dist import transport
from pcc.dist.kv import BlockManager
from pcc.dist.tensor_kv_execution import (
    KVOwnership,
    PccOwnedCpuTensor,
    TensorKVExecutionError,
    synchronize_gradient,
    transfer_kv_ownership,
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
        "tensor-kv-execution",
        [
            {
                "rank": rank,
                "host": f"127.0.0.1:{port}",
                "transport": "tcp-ring",
            }
            for rank, port in enumerate(ports)
        ],
    )


def _execution_worker(manifest, rank: int, start, expected_kv_state: str, results) -> None:
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
        inputs = ([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0])
        ownership = None
        if rank == 0:
            ownership = KVOwnership(0, BlockManager.deserialize(expected_kv_state))
        with owner:
            gradient = synchronize_gradient(
                owner,
                PccOwnedCpuTensor(list(inputs[rank])),
                inputs,
            )
            kv_result = transfer_kv_ownership(owner, ownership)
        destination_state = None
        if kv_result.local_ownership is not None:
            destination_state = kv_result.local_ownership.manager.serialize()
        results.put(
            (
                "ok",
                rank,
                gradient.tensor.read(),
                gradient.oracle_equal,
                gradient.execution.operation_sequence,
                gradient.execution.fallback_used,
                gradient.lifetime.fence_completed,
                gradient.lifetime.final_state,
                kv_result.execution.operation_sequence,
                kv_result.execution.fallback_used,
                kv_result.oracle_equal,
                kv_result.source_released,
                kv_result.destination_owned,
                kv_result.lifetime.fence_completed,
                kv_result.lifetime.final_state,
                kv_result.state_sha256,
                destination_state,
                ownership.released if ownership is not None else False,
                owner.transport.is_open,
            )
        )
    except BaseException as exc:
        results.put(("error", rank, type(exc).__name__, str(exc)))


def _kv_oracle_state() -> str:
    manager = BlockManager(block_tokens=2, capacity=8)
    handles = manager.allocate([11, 12, 13, 14, 15])
    manager.pin(handles[0])
    return manager.serialize()


def test_owned_gradient_and_kv_transfer_execute_through_tcp_collectives():
    manifest = _manifest(_free_loopback_ports(2))
    expected_kv_state = _kv_oracle_state()
    expected_digest = hashlib.sha256(expected_kv_state.encode("utf-8")).hexdigest()
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    children = [
        ctx.Process(
            target=_execution_worker,
            args=(manifest, rank, start, expected_kv_state, results),
        )
        for rank in range(2)
    ]
    try:
        for child in children:
            child.start()
        start.set()
        received = sorted(results.get(timeout=10.0) for _ in children)
        assert received == [
            (
                "ok",
                0,
                [11.0, 22.0, 33.0, 44.0],
                True,
                0,
                False,
                True,
                "freed",
                1,
                False,
                True,
                True,
                False,
                True,
                "freed",
                expected_digest,
                None,
                True,
                False,
            ),
            (
                "ok",
                1,
                [11.0, 22.0, 33.0, 44.0],
                True,
                0,
                False,
                True,
                "freed",
                1,
                False,
                True,
                False,
                True,
                True,
                "freed",
                expected_digest,
                expected_kv_state,
                False,
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


def test_owned_tensor_rejects_non_pod_and_released_reads():
    with pytest.raises(TensorKVExecutionError, match="numeric POD"):
        PccOwnedCpuTensor([object()])  # type: ignore[list-item]
    tensor = PccOwnedCpuTensor([1.0])
    tensor.handle.state = tensor.handle.state.FREED
    with pytest.raises(TensorKVExecutionError, match="released"):
        tensor.read()


def test_execution_bridge_claim_boundary_and_lifetime_source_guard():
    source = (
        Path(__file__).parents[2] / "pcc/dist/tensor_kv_execution.py"
    ).read_text(encoding="utf-8")
    assert "PccBufferHandle" in source
    assert "PccFenceToken" in source
    assert "PccDeferredFreeQueue" in source
    assert "owner.allreduce" in source
    assert "owner.broadcast" in source
    assert "BlockManager.deserialize" in source
    assert "torch" not in source.lower()
    assert "mlx" not in source.lower()
