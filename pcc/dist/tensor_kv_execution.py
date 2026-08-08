"""Bounded tensor-gradient and KV-ownership execution over owned collectives.

This is a CPU/POD execution bridge, not a framework training or serving
runtime.  Tensors own a ``PccBufferHandle`` and f64 host values.  KV transfer
uses a fixed-size u8 packet carrying the existing BlockManager serialization.
Both operations release their transport scratch buffer only after a completed
``PccFenceToken``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Sequence

from pcc.kernel_ir.hmm_fence import (
    BufferState,
    PccBufferHandle,
    PccDeferredFreeQueue,
    PccFenceToken,
)

from . import collective
from .kv import BlockManager
from .transport_collective import CollectiveExecution, TCPCollectiveOwner


class TensorKVExecutionError(ValueError):
    """A bounded tensor/KV execution or ownership invariant failed."""


@dataclass
class PccOwnedCpuTensor:
    """A small f64 CPU tensor with an explicit pcc buffer owner."""

    values: list[float]
    handle: PccBufferHandle = field(init=False)

    def __post_init__(self) -> None:
        if not self.values:
            raise TensorKVExecutionError("owned tensor must contain at least one value")
        converted: list[float] = []
        for value in self.values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TensorKVExecutionError(
                    f"owned f64 tensor requires numeric POD values, got {value!r}"
                )
            converted.append(float(value))
        self.values = converted
        self.handle = PccBufferHandle(
            nbytes=len(converted) * 8,
            dtype="f64",
            device="cpu",
        )

    def read(self) -> list[float]:
        if self.handle.state is BufferState.FREED:
            raise TensorKVExecutionError("cannot read a released owned tensor")
        return list(self.values)


@dataclass(frozen=True)
class TransferLifetime:
    buffer_id: int
    fence_id: int
    fence_completed: bool
    reclaimed_ids: tuple[int, ...]
    final_state: str


@dataclass(frozen=True)
class GradientSyncResult:
    tensor: PccOwnedCpuTensor
    execution: CollectiveExecution
    oracle_values: tuple[float, ...]
    oracle_equal: bool
    lifetime: TransferLifetime


@dataclass
class KVOwnership:
    owner_rank: int
    manager: BlockManager
    released: bool = False

    def require_live(self) -> None:
        if self.released:
            raise TensorKVExecutionError("KV ownership has already been transferred")


@dataclass(frozen=True)
class KVTransferResult:
    local_ownership: KVOwnership | None
    execution: CollectiveExecution
    state_sha256: str
    oracle_equal: bool
    source_released: bool
    destination_owned: bool
    lifetime: TransferLifetime


def _finish_transfer_lifetime(
    scratch: PccBufferHandle,
    fence: PccFenceToken,
    queue: PccDeferredFreeQueue,
) -> TransferLifetime:
    fence.complete()
    reclaimed = tuple(queue.reclaim())
    if reclaimed != (scratch.handle_id,) or scratch.state is not BufferState.FREED:
        raise TensorKVExecutionError("transport scratch buffer was not fence-reclaimed")
    return TransferLifetime(
        buffer_id=scratch.handle_id,
        fence_id=fence.fence_id,
        fence_completed=fence.completed,
        reclaimed_ids=reclaimed,
        final_state=scratch.state.value,
    )


def synchronize_gradient(
    owner: TCPCollectiveOwner,
    tensor: PccOwnedCpuTensor,
    all_rank_oracle_inputs: Sequence[Sequence[collective.Number]],
) -> GradientSyncResult:
    """Allreduce one owned f64 gradient and verify the CPU oracle result."""
    if tensor.handle.state is not BufferState.LIVE:
        raise TensorKVExecutionError("gradient tensor must be live")
    if len(all_rank_oracle_inputs) != owner.transport.manifest.world_size:
        raise TensorKVExecutionError("gradient oracle inputs must cover every rank")
    scratch = PccBufferHandle(
        nbytes=tensor.handle.nbytes,
        dtype=tensor.handle.dtype,
        device="cpu",
    )
    fence = PccFenceToken()
    queue = PccDeferredFreeQueue()
    queue.schedule_free(scratch, fence)
    try:
        reduced, execution = owner.allreduce(tensor.read(), "sum")
    finally:
        lifetime = _finish_transfer_lifetime(scratch, fence, queue)
    reference, _ = collective.allreduce(all_rank_oracle_inputs, "sum")
    expected = tuple(float(value) for value in reference[owner.transport.rank])
    actual = tuple(float(value) for value in reduced)
    if actual != expected:
        raise TensorKVExecutionError(
            f"transport gradient {actual!r} differs from CPU oracle {expected!r}"
        )
    tensor.values = list(actual)
    return GradientSyncResult(
        tensor=tensor,
        execution=execution,
        oracle_values=expected,
        oracle_equal=True,
        lifetime=lifetime,
    )


def _encode_state_packet(state: str, packet_bytes: int) -> list[int]:
    payload = state.encode("utf-8")
    if packet_bytes < 8:
        raise TensorKVExecutionError("KV packet_bytes must be at least 8")
    if len(payload) > packet_bytes - 4:
        raise TensorKVExecutionError(
            f"KV state needs {len(payload) + 4} bytes, packet has {packet_bytes}"
        )
    prefix = len(payload).to_bytes(4, "big")
    return list(prefix + payload + bytes(packet_bytes - 4 - len(payload)))


def _decode_state_packet(packet: Sequence[collective.Number]) -> str:
    try:
        raw = bytes(int(value) for value in packet)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TensorKVExecutionError("KV transfer packet is not u8 POD") from exc
    if len(raw) < 4:
        raise TensorKVExecutionError("KV transfer packet is truncated")
    length = int.from_bytes(raw[:4], "big")
    if length > len(raw) - 4:
        raise TensorKVExecutionError("KV transfer packet length exceeds payload")
    try:
        return raw[4 : 4 + length].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TensorKVExecutionError("KV transfer packet is not UTF-8") from exc


def transfer_kv_ownership(
    owner: TCPCollectiveOwner,
    ownership: KVOwnership | None,
    *,
    source_rank: int = 0,
    destination_rank: int = 1,
    packet_bytes: int = 4096,
) -> KVTransferResult:
    """Broadcast one serialized BlockManager and move ownership to one rank."""
    world_size = owner.transport.manifest.world_size
    rank = owner.transport.rank
    if not (0 <= source_rank < world_size and 0 <= destination_rank < world_size):
        raise TensorKVExecutionError("KV source/destination rank is out of range")
    if source_rank == destination_rank:
        raise TensorKVExecutionError("KV ownership transfer needs distinct ranks")
    if rank == source_rank:
        if ownership is None or ownership.owner_rank != source_rank:
            raise TensorKVExecutionError("source rank must provide live KV ownership")
        ownership.require_live()
        source_state = ownership.manager.serialize()
        local_packet = _encode_state_packet(source_state, packet_bytes)
    else:
        if ownership is not None:
            raise TensorKVExecutionError("non-source rank must not provide KV ownership")
        source_state = ""
        local_packet = [0] * packet_bytes

    scratch = PccBufferHandle(nbytes=packet_bytes, dtype="u8", device="cpu")
    fence = PccFenceToken()
    queue = PccDeferredFreeQueue()
    queue.schedule_free(scratch, fence)
    try:
        received, execution = owner.broadcast(local_packet, root=source_rank)
    finally:
        lifetime = _finish_transfer_lifetime(scratch, fence, queue)
    received_state = _decode_state_packet(received)
    restored = BlockManager.deserialize(received_state)
    canonical = restored.serialize()
    if canonical != received_state:
        raise TensorKVExecutionError("received KV state is not canonical")
    state_sha256 = hashlib.sha256(received_state.encode("utf-8")).hexdigest()

    local_ownership: KVOwnership | None = None
    source_released = False
    if rank == source_rank:
        assert ownership is not None
        if source_state != received_state:
            raise TensorKVExecutionError("source KV broadcast differs from local oracle")
        ownership.released = True
        source_released = True
    elif rank == destination_rank:
        local_ownership = KVOwnership(destination_rank, restored)
    return KVTransferResult(
        local_ownership=local_ownership,
        execution=execution,
        state_sha256=state_sha256,
        oracle_equal=True,
        source_released=source_released,
        destination_owned=local_ownership is not None,
        lifetime=lifetime,
    )


__all__ = [
    "TensorKVExecutionError",
    "PccOwnedCpuTensor",
    "TransferLifetime",
    "GradientSyncResult",
    "KVOwnership",
    "KVTransferResult",
    "synchronize_gradient",
    "transfer_kv_ownership",
]
