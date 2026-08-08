"""Deterministic collectives executed through an owned TCP ring.

Every rank circulates its local contribution around the ring, validates an
operation/rank envelope, then calls the existing single-process oracle over
buffers ordered by origin rank.  The network layer therefore owns movement and
failure bounds while :mod:`pcc.dist.collective` remains the sole semantic
authority.
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from typing import Sequence

from . import collective
from .tcp_transport import TCPRingOwner, TCPTransportError


_COLLECTIVE_PROTOCOL_VERSION = 1


class TransportCollectiveError(TCPTransportError):
    """A transport collective envelope or lifecycle invariant failed."""


class CollectiveCancelled(TransportCollectiveError):
    """The caller cancelled a collective at a bounded round boundary."""


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class CollectiveExecution:
    kind: str
    rank: int
    world_size: int
    operation_sequence: int
    requested_backend: str
    actual_backend: str
    fallback_used: bool
    rounds: int
    status: str = "completed"


def _validate_buffer(buffer: Sequence[collective.Number]) -> list[collective.Number]:
    values = list(buffer)
    if not values:
        raise collective.CollectiveError("collective buffers must be non-empty")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise collective.CollectiveError(
                f"transport collective supports int/float POD values, got {value!r}"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise collective.CollectiveError(
                f"transport collective requires finite float values, got {value!r}"
            )
    return values


class TCPCollectiveOwner:
    """Five collective operations backed by an already selected TCP owner."""

    def __init__(self, transport_owner: TCPRingOwner) -> None:
        selection = transport_owner.selection
        if selection.requested_backend != "tcp-ring":
            raise TransportCollectiveError(
                f"collective owner requires requested tcp-ring, got {selection.requested_backend!r}"
            )
        if selection.actual_backend != selection.requested_backend:
            raise TransportCollectiveError("collective owner refuses backend substitution")
        if selection.fallback_used:
            raise TransportCollectiveError("collective owner refuses transport fallback")
        self.transport = transport_owner
        self.selection = selection
        self._operation_sequence = 0

    def open(self) -> "TCPCollectiveOwner":
        self.transport.open()
        return self

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "TCPCollectiveOwner":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _check_cancelled(
        self, token: CancellationToken | None, kind: str, sequence: int
    ) -> None:
        if token is not None and token.cancelled:
            raise CollectiveCancelled(
                f"{kind} operation {sequence} cancelled at a bounded ring round"
            )

    def _encode_packet(
        self,
        kind: str,
        sequence: int,
        origin_rank: int,
        buffer: list[collective.Number] | None,
    ) -> bytes:
        try:
            return json.dumps(
                {
                    "version": _COLLECTIVE_PROTOCOL_VERSION,
                    "kind": kind,
                    "sequence": sequence,
                    "origin_rank": origin_rank,
                    "buffer": buffer,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise collective.CollectiveError(
                f"{kind} buffer is not finite JSON POD data: {exc}"
            ) from exc

    def _decode_packet(
        self, payload: bytes, kind: str, sequence: int
    ) -> tuple[int, list[collective.Number] | None]:
        try:
            packet = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportCollectiveError(
                f"invalid {kind} transport envelope: {exc}"
            ) from exc
        if not isinstance(packet, dict):
            raise TransportCollectiveError(f"{kind} transport envelope must be an object")
        if packet.get("version") != _COLLECTIVE_PROTOCOL_VERSION:
            raise TransportCollectiveError(f"{kind} transport protocol version mismatch")
        if packet.get("kind") != kind or packet.get("sequence") != sequence:
            raise TransportCollectiveError(
                f"collective operation mismatch: received "
                f"{packet.get('kind')}#{packet.get('sequence')}, expected {kind}#{sequence}"
            )
        origin = packet.get("origin_rank")
        if isinstance(origin, bool) or not isinstance(origin, int):
            raise TransportCollectiveError(f"{kind} envelope has invalid origin rank")
        if not (0 <= origin < self.transport.manifest.world_size):
            raise TransportCollectiveError(
                f"{kind} envelope origin rank {origin} is out of range"
            )
        raw_buffer = packet.get("buffer")
        if raw_buffer is None:
            return origin, None
        if not isinstance(raw_buffer, list):
            raise TransportCollectiveError(f"{kind} envelope buffer must be a list")
        return origin, _validate_buffer(raw_buffer)

    def _circulate(
        self,
        kind: str,
        local_buffer: list[collective.Number] | None,
        cancel: CancellationToken | None,
    ) -> tuple[dict[int, list[collective.Number] | None], CollectiveExecution]:
        if not self.transport.is_open:
            raise TransportCollectiveError("transport collective owner is not open")
        sequence = self._operation_sequence
        self._operation_sequence += 1
        self._check_cancelled(cancel, kind, sequence)
        packet = self._encode_packet(kind, sequence, self.transport.rank, local_buffer)
        gathered: dict[int, list[collective.Number] | None] = {
            self.transport.rank: local_buffer
        }
        rounds = self.transport.manifest.world_size - 1
        for _ in range(rounds):
            self._check_cancelled(cancel, kind, sequence)
            packet = self.transport.exchange(packet)
            origin, buffer = self._decode_packet(packet, kind, sequence)
            if origin in gathered:
                raise TransportCollectiveError(
                    f"{kind} received duplicate origin rank {origin}"
                )
            gathered[origin] = buffer
            self._check_cancelled(cancel, kind, sequence)
        expected = set(range(self.transport.manifest.world_size))
        if set(gathered) != expected:
            raise TransportCollectiveError(
                f"{kind} rank coverage {sorted(gathered)}, expected {sorted(expected)}"
            )
        execution = CollectiveExecution(
            kind=kind,
            rank=self.transport.rank,
            world_size=self.transport.manifest.world_size,
            operation_sequence=sequence,
            requested_backend=self.selection.requested_backend,
            actual_backend=self.selection.actual_backend,
            fallback_used=self.selection.fallback_used,
            rounds=rounds,
        )
        return gathered, execution

    @staticmethod
    def _ordered_buffers(
        gathered: dict[int, list[collective.Number] | None]
    ) -> list[list[collective.Number]]:
        ordered: list[list[collective.Number]] = []
        for rank in range(len(gathered)):
            buffer = gathered[rank]
            if buffer is None:
                raise TransportCollectiveError(
                    f"collective rank {rank} supplied no buffer"
                )
            ordered.append(buffer)
        return ordered

    def allreduce(
        self,
        local_buffer: Sequence[collective.Number],
        op: str = "sum",
        *,
        cancel: CancellationToken | None = None,
    ) -> tuple[list[collective.Number], CollectiveExecution]:
        gathered, execution = self._circulate(
            "allreduce", _validate_buffer(local_buffer), cancel
        )
        outputs, _ = collective.allreduce(self._ordered_buffers(gathered), op)
        return outputs[self.transport.rank], execution

    def reduce_scatter(
        self,
        local_buffer: Sequence[collective.Number],
        op: str = "sum",
        *,
        cancel: CancellationToken | None = None,
    ) -> tuple[list[collective.Number], CollectiveExecution]:
        gathered, execution = self._circulate(
            "reduce_scatter", _validate_buffer(local_buffer), cancel
        )
        outputs, _ = collective.reduce_scatter(self._ordered_buffers(gathered), op)
        return outputs[self.transport.rank], execution

    def all_gather(
        self,
        local_buffer: Sequence[collective.Number],
        *,
        cancel: CancellationToken | None = None,
    ) -> tuple[list[collective.Number], CollectiveExecution]:
        gathered, execution = self._circulate(
            "all_gather", _validate_buffer(local_buffer), cancel
        )
        outputs, _ = collective.all_gather(self._ordered_buffers(gathered))
        return outputs[self.transport.rank], execution

    def broadcast(
        self,
        local_buffer: Sequence[collective.Number],
        root: int = 0,
        *,
        cancel: CancellationToken | None = None,
    ) -> tuple[list[collective.Number], CollectiveExecution]:
        gathered, execution = self._circulate(
            "broadcast", _validate_buffer(local_buffer), cancel
        )
        outputs, _ = collective.broadcast(self._ordered_buffers(gathered), root)
        return outputs[self.transport.rank], execution

    def barrier(
        self, *, cancel: CancellationToken | None = None
    ) -> CollectiveExecution:
        gathered, execution = self._circulate("barrier", None, cancel)
        if any(buffer is not None for buffer in gathered.values()):
            raise TransportCollectiveError("barrier envelope unexpectedly carried data")
        collective.barrier(self.transport.manifest.world_size)
        return execution
