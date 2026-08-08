"""D-P0-DIST-SHARDING: CPU-only sharding metadata oracle (no tensors).

Maps model parameters to per-rank shards under two strategies and validates the
collective schedule each strategy implies against :mod:`pcc.dist.collective`:

    DDP  — every rank holds a full replica of every parameter; gradients are
           synchronized with an ``allreduce`` (mean). No parameter sharding.
    FSDP — each parameter is sharded (flattened, padded, split) across ranks;
           the training step is a ``reduce_scatter`` of gradients followed by an
           ``all_gather`` of the updated shards. The oracle checks that
           reduce-scatter+all-gather reconstructs the full allreduce result.

There are NO real tensors here — a "parameter" is just a name + element count.
This is pure placement/schedule metadata. No PyTorch/MLX/pcc-native tensor
training is implemented or claimed. Parameter-server is represented ONLY as an
explicit non-default mode marker, not an implementation.

Standalone-importable: ``import pcc.dist.sharding``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from . import collective

STRATEGY_DDP = "ddp"
STRATEGY_FSDP = "fsdp"
# Explicit non-default marker only; not implemented as a training mode here.
STRATEGY_PARAMETER_SERVER = "parameter-server"

_STRATEGIES = frozenset({STRATEGY_DDP, STRATEGY_FSDP})


class ShardingError(Exception):
    """Raised for invalid parameter specs, world sizes, or schedule mismatches."""


@dataclass(frozen=True)
class ParamSpec:
    """A model parameter as placement metadata: a name and an element count."""

    name: str
    numel: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ShardingError("parameter name must be non-empty")
        if self.numel <= 0:
            raise ShardingError(f"parameter {self.name!r} numel must be positive, got {self.numel}")


@dataclass(frozen=True)
class ParamShard:
    """One rank's slice of one parameter (or full replica under DDP)."""

    param: str
    rank: int
    start: int          # element offset into the flattened parameter
    length: int         # elements this rank owns (may include padding tail)
    padded: int = 0     # padding elements included at the very end (FSDP)

    @property
    def is_full_replica(self) -> bool:
        return self.start == 0 and self.padded == 0 and self.length > 0


@dataclass(frozen=True)
class ShardPlan:
    """The full placement + schedule for one strategy over a set of parameters."""

    strategy: str
    world_size: int
    shards: tuple[ParamShard, ...]
    collective_schedule: tuple[str, ...]
    detail: dict = field(default_factory=dict)

    def shards_for_rank(self, rank: int) -> tuple[ParamShard, ...]:
        return tuple(s for s in self.shards if s.rank == rank)

    def shards_for_param(self, name: str) -> tuple[ParamShard, ...]:
        return tuple(s for s in self.shards if s.param == name)


def _check_world(world_size: int) -> None:
    if world_size <= 0:
        raise ShardingError(f"world_size must be positive, got {world_size}")


def plan_ddp(params: Sequence[ParamSpec], world_size: int) -> ShardPlan:
    """DDP: every rank holds a full replica; gradients synced via allreduce."""
    _check_world(world_size)
    if not params:
        raise ShardingError("no parameters to shard")
    shards: list[ParamShard] = []
    for p in params:
        for r in range(world_size):
            shards.append(ParamShard(p.name, r, 0, p.numel, padded=0))
    return ShardPlan(
        STRATEGY_DDP,
        world_size,
        tuple(shards),
        collective_schedule=("allreduce",),
        detail={"replicated": True},
    )


def plan_fsdp(params: Sequence[ParamSpec], world_size: int) -> ShardPlan:
    """FSDP: flatten + pad each parameter to a multiple of world_size, split evenly.

    Padding is placed at the tail of the last rank's shard so that shard lengths
    are equal (a requirement for ring reduce-scatter/all-gather). The schedule
    is ``reduce_scatter`` (grads) then ``all_gather`` (updated shards).
    """
    _check_world(world_size)
    if not params:
        raise ShardingError("no parameters to shard")
    shards: list[ParamShard] = []
    total_padding = 0
    for p in params:
        remainder = p.numel % world_size
        pad = (world_size - remainder) % world_size
        total_padding += pad
        padded_numel = p.numel + pad
        chunk = padded_numel // world_size
        for r in range(world_size):
            start = r * chunk
            # Padding lives entirely in the final chunk's tail.
            pad_in_shard = 0
            if r == world_size - 1:
                pad_in_shard = pad
            shards.append(ParamShard(p.name, r, start, chunk, padded=pad_in_shard))
    return ShardPlan(
        STRATEGY_FSDP,
        world_size,
        tuple(shards),
        collective_schedule=("reduce_scatter", "all_gather"),
        detail={"total_padding": total_padding},
    )


def build_plan(strategy: str, params: Sequence[ParamSpec], world_size: int) -> ShardPlan:
    if strategy == STRATEGY_DDP:
        return plan_ddp(params, world_size)
    if strategy == STRATEGY_FSDP:
        return plan_fsdp(params, world_size)
    if strategy == STRATEGY_PARAMETER_SERVER:
        raise ShardingError(
            "parameter-server is an explicit non-default mode and is NOT implemented "
            "in this metadata slice; use ddp or fsdp"
        )
    raise ShardingError(f"unknown strategy {strategy!r}; known: {sorted(_STRATEGIES)}")


def validate_plan(plan: ShardPlan) -> None:
    """Structural checks: full coverage, equal FSDP shard sizes, no overlaps."""
    by_param: dict[str, list[ParamShard]] = {}
    for s in plan.shards:
        by_param.setdefault(s.param, []).append(s)
    for name, group in by_param.items():
        ranks = sorted(s.rank for s in group)
        if ranks != list(range(plan.world_size)):
            raise ShardingError(
                f"param {name!r} shards must cover ranks 0..{plan.world_size - 1}, got {ranks}"
            )
        if plan.strategy == STRATEGY_FSDP:
            lengths = {s.length for s in group}
            if len(lengths) != 1:
                raise ShardingError(
                    f"FSDP param {name!r} has unequal shard lengths {sorted(lengths)}"
                )
            ordered = sorted(group, key=lambda s: s.rank)
            expected_start = 0
            for s in ordered:
                if s.start != expected_start:
                    raise ShardingError(
                        f"FSDP param {name!r} rank {s.rank} start {s.start} != {expected_start}"
                    )
                expected_start += s.length


def validate_schedule_against_collective(
    plan: ShardPlan, sample_grads: Sequence[Sequence[collective.Number]]
) -> None:
    """Prove the plan's collective schedule matches :mod:`pcc.dist.collective`.

    ``sample_grads`` is one buffer per rank (POD lists). For DDP the schedule
    must be a single allreduce. For FSDP, reduce_scatter+all_gather must
    reconstruct the same vector as allreduce (the ring-FSDP identity), and the
    buffer length must be divisible by the world size.
    """
    world = plan.world_size
    if len(sample_grads) != world:
        raise ShardingError(
            f"need one gradient buffer per rank ({world}), got {len(sample_grads)}"
        )
    if plan.strategy == STRATEGY_DDP:
        if plan.collective_schedule != ("allreduce",):
            raise ShardingError(f"DDP schedule must be ('allreduce',), got {plan.collective_schedule}")
        collective.allreduce(sample_grads, "sum")  # raises on shape/dtype mismatch
        return
    if plan.strategy == STRATEGY_FSDP:
        if plan.collective_schedule != ("reduce_scatter", "all_gather"):
            raise ShardingError(
                f"FSDP schedule must be ('reduce_scatter', 'all_gather'), got {plan.collective_schedule}"
            )
        reference, _ = collective.allreduce(sample_grads, "sum")
        reconstructed = collective.reduce_scatter_then_all_gather(sample_grads, "sum")
        if reconstructed != reference[0]:
            raise ShardingError(
                "FSDP reduce_scatter+all_gather does not reconstruct the allreduce result"
            )
        return
    raise ShardingError(f"cannot validate schedule for strategy {plan.strategy!r}")


def strategies() -> tuple[str, ...]:
    return tuple(sorted(_STRATEGIES))
