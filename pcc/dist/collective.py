"""D-P0-DIST-COLLECTIVE: single-process deterministic collective oracle.

The research priority is "collective API first, transport later". This module
is the *reference semantics* of the collective set — allreduce, reduce-scatter,
all-gather, broadcast, barrier — computed deterministically in one process over
fake ranks and POD (list) buffers. It is what a real TCP-ring / QUIC / RDMA
backend must reproduce bit-for-bit; here there is NO transport.

Design invariants the tests pin down:

    * every rank contributes one equal-shaped, equal-dtype buffer
    * shape / dtype / count mismatches raise :class:`CollectiveError`
    * ops are deterministic and order-independent for commutative reductions
      (sum/max/min/prod computed left-to-right by ascending rank)
    * reduce-scatter over N ranks requires the buffer length divisible by N
    * all-gather concatenates in ascending rank order
    * a :class:`CollectiveOp` carries timeout/cancel metadata (never fired here;
      it is descriptive so the API shape matches a real backend)

Standalone-importable: ``import pcc.dist.collective``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

Number = int | float


class CollectiveError(Exception):
    """Raised for shape/dtype/rank-count mismatches or bad reduce ops."""


# Reduction operators. Kept as pure functions so the oracle is obviously
# deterministic and matches what a real backend's reduce kernel must compute.
_REDUCERS: dict[str, Callable[[Number, Number], Number]] = {
    "sum": lambda a, b: a + b,
    "max": lambda a, b: a if a >= b else b,
    "min": lambda a, b: a if a <= b else b,
    "prod": lambda a, b: a * b,
}


def reduce_ops() -> tuple[str, ...]:
    return tuple(_REDUCERS)


@dataclass(frozen=True)
class CollectiveOp:
    """Descriptive metadata for a collective invocation.

    ``timeout_s`` / ``cancellable`` are carried so the oracle's API shape
    matches a transport-backed backend. They are metadata only in this slice:
    the local computation is synchronous and never times out or cancels, and
    ``status`` stays ``"completed"``.
    """

    kind: str
    world_size: int
    reduce: str | None = None
    timeout_s: float | None = None
    cancellable: bool = False
    status: str = "completed"
    detail: dict = field(default_factory=dict)


def _dtype_of(buf: Sequence[Number]) -> str:
    """POD dtype label: 'int' iff every element is a bool-free int, else 'float'."""
    is_int = all(isinstance(x, int) and not isinstance(x, bool) for x in buf)
    return "int" if is_int else "float"


def _check_uniform(buffers: Sequence[Sequence[Number]], world_size: int) -> tuple[int, str]:
    if world_size <= 0:
        raise CollectiveError(f"world_size must be positive, got {world_size}")
    if len(buffers) != world_size:
        raise CollectiveError(
            f"expected {world_size} rank buffers, got {len(buffers)}"
        )
    lengths = {len(b) for b in buffers}
    if len(lengths) != 1:
        raise CollectiveError(f"buffers have mismatched shapes: lengths={sorted(lengths)}")
    length = lengths.pop()
    if length == 0:
        raise CollectiveError("collective buffers must be non-empty")
    dtypes = {_dtype_of(b) for b in buffers}
    if len(dtypes) != 1:
        raise CollectiveError(f"buffers have mismatched dtypes: {sorted(dtypes)}")
    return length, dtypes.pop()


def _elementwise_reduce(
    buffers: Sequence[Sequence[Number]], length: int, op: str
) -> list[Number]:
    if op not in _REDUCERS:
        raise CollectiveError(f"unknown reduce op {op!r}; known: {sorted(_REDUCERS)}")
    fn = _REDUCERS[op]
    # Left-to-right over ascending rank order -> deterministic.
    acc = list(buffers[0])
    for buf in buffers[1:]:
        for i in range(length):
            acc[i] = fn(acc[i], buf[i])
    return acc


def allreduce(
    buffers: Sequence[Sequence[Number]],
    op: str = "sum",
    *,
    timeout_s: float | None = None,
) -> tuple[list[list[Number]], CollectiveOp]:
    """Every rank ends with the elementwise reduction of all rank buffers."""
    length, _ = _check_uniform(buffers, len(buffers))
    reduced = _elementwise_reduce(buffers, length, op)
    out = [list(reduced) for _ in buffers]
    meta = CollectiveOp("allreduce", len(buffers), reduce=op, timeout_s=timeout_s)
    return out, meta


def reduce_scatter(
    buffers: Sequence[Sequence[Number]],
    op: str = "sum",
    *,
    timeout_s: float | None = None,
) -> tuple[list[list[Number]], CollectiveOp]:
    """Reduce elementwise then scatter equal contiguous chunks to each rank.

    Requires the buffer length divisible by the world size. Rank ``r`` receives
    chunk ``r`` of the reduced vector.
    """
    world = len(buffers)
    length, _ = _check_uniform(buffers, world)
    if length % world != 0:
        raise CollectiveError(
            f"reduce_scatter needs length divisible by world_size {world}, got length {length}"
        )
    chunk = length // world
    reduced = _elementwise_reduce(buffers, length, op)
    out = [reduced[r * chunk:(r + 1) * chunk] for r in range(world)]
    meta = CollectiveOp("reduce_scatter", world, reduce=op, timeout_s=timeout_s,
                        detail={"chunk": chunk})
    return out, meta


def all_gather(
    buffers: Sequence[Sequence[Number]],
    *,
    timeout_s: float | None = None,
) -> tuple[list[list[Number]], CollectiveOp]:
    """Every rank ends with the concatenation of all rank buffers (ascending)."""
    length, _ = _check_uniform(buffers, len(buffers))
    gathered: list[Number] = []
    for buf in buffers:
        gathered.extend(buf)
    out = [list(gathered) for _ in buffers]
    meta = CollectiveOp("all_gather", len(buffers), timeout_s=timeout_s,
                        detail={"per_rank_len": length})
    return out, meta


def broadcast(
    buffers: Sequence[Sequence[Number]],
    root: int = 0,
    *,
    timeout_s: float | None = None,
) -> tuple[list[list[Number]], CollectiveOp]:
    """Every rank ends with a copy of ``root``'s buffer."""
    world = len(buffers)
    if not (0 <= root < world):
        raise CollectiveError(f"root {root} out of range for world_size {world}")
    _check_uniform(buffers, world)
    src = list(buffers[root])
    out = [list(src) for _ in buffers]
    meta = CollectiveOp("broadcast", world, timeout_s=timeout_s, detail={"root": root})
    return out, meta


def barrier(world_size: int, *, timeout_s: float | None = None) -> CollectiveOp:
    """A no-op synchronization point (single process). Returns completed metadata."""
    if world_size <= 0:
        raise CollectiveError(f"world_size must be positive, got {world_size}")
    return CollectiveOp("barrier", world_size, timeout_s=timeout_s)


def reduce_scatter_then_all_gather(
    buffers: Sequence[Sequence[Number]], op: str = "sum"
) -> list[Number]:
    """Compose reduce-scatter + all-gather; must equal allreduce (invariant).

    This is the identity that ring-based FSDP relies on: a reduce-scatter of the
    gradients followed by an all-gather of the reduced shards reconstructs the
    full allreduce result on every rank. Returns the single reconstructed
    vector (identical on every rank). The sharding oracle validates schedules
    against this.
    """
    scattered, _ = reduce_scatter(buffers, op)
    gathered, _ = all_gather(scattered)
    # Every rank holds the identical concatenation; return one copy.
    return gathered[0]
