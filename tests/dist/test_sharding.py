"""Oracle tests for pcc.dist.sharding (D-P0-DIST-SHARDING).

CPU-only sharding metadata: DDP replica plans, FSDP shard plans with padding,
structural validation, and schedule validation against the collective oracle.
Parameter-server is an explicit non-default rejection, not an implementation.
"""
import pytest

from pcc.dist import sharding
from pcc.dist.sharding import ParamSpec


def _grads(world, length, base=1):
    return [[base + r + i for i in range(length)] for r in range(world)]


# --- DDP -------------------------------------------------------------------
def test_ddp_replicates_every_param_on_every_rank():
    params = [ParamSpec("w0", 10), ParamSpec("b0", 4)]
    plan = sharding.plan_ddp(params, 3)
    assert plan.strategy == sharding.STRATEGY_DDP
    assert plan.collective_schedule == ("allreduce",)
    for name, numel in (("w0", 10), ("b0", 4)):
        shards = plan.shards_for_param(name)
        assert len(shards) == 3  # one per rank
        for s in shards:
            assert s.is_full_replica and s.length == numel


def test_ddp_schedule_validates_against_collective():
    plan = sharding.plan_ddp([ParamSpec("w", 8)], 4)
    sharding.validate_plan(plan)
    sharding.validate_schedule_against_collective(plan, _grads(4, 8))


# --- FSDP ------------------------------------------------------------------
def test_fsdp_even_split_no_padding():
    plan = sharding.plan_fsdp([ParamSpec("w", 8)], 4)
    shards = plan.shards_for_param("w")
    assert [s.length for s in shards] == [2, 2, 2, 2]
    assert [s.start for s in shards] == [0, 2, 4, 6]
    assert plan.detail["total_padding"] == 0
    assert plan.collective_schedule == ("reduce_scatter", "all_gather")


def test_fsdp_pads_uneven_param_on_last_rank():
    plan = sharding.plan_fsdp([ParamSpec("w", 7)], 4)  # 7 -> padded to 8
    shards = sorted(plan.shards_for_param("w"), key=lambda s: s.rank)
    assert [s.length for s in shards] == [2, 2, 2, 2]  # equal shard sizes
    assert shards[-1].padded == 1  # the pad lives on the last rank
    assert sum(s.padded for s in shards[:-1]) == 0
    assert plan.detail["total_padding"] == 1


def test_fsdp_structural_validation():
    plan = sharding.plan_fsdp([ParamSpec("w", 15), ParamSpec("b", 4)], 4)
    sharding.validate_plan(plan)  # equal shard sizes, contiguous, full coverage


def test_fsdp_schedule_reconstructs_allreduce():
    plan = sharding.plan_fsdp([ParamSpec("w", 8)], 4)
    sharding.validate_plan(plan)
    # length must be divisible by world for reduce_scatter; 8 % 4 == 0
    sharding.validate_schedule_against_collective(plan, _grads(4, 8))


def test_fsdp_schedule_mismatch_length_raises():
    plan = sharding.plan_fsdp([ParamSpec("w", 8)], 4)
    with pytest.raises(Exception):  # reduce_scatter needs divisible length
        sharding.validate_schedule_against_collective(plan, _grads(4, 6))


# --- strategy dispatch + errors -------------------------------------------
def test_build_plan_dispatch():
    params = [ParamSpec("w", 8)]
    assert sharding.build_plan("ddp", params, 2).strategy == "ddp"
    assert sharding.build_plan("fsdp", params, 2).strategy == "fsdp"


def test_parameter_server_is_explicit_nondefault_rejection():
    with pytest.raises(sharding.ShardingError):
        sharding.build_plan(sharding.STRATEGY_PARAMETER_SERVER, [ParamSpec("w", 4)], 2)


def test_unknown_strategy_raises():
    with pytest.raises(sharding.ShardingError):
        sharding.build_plan("megatron", [ParamSpec("w", 4)], 2)


def test_param_and_world_validation():
    with pytest.raises(sharding.ShardingError):
        ParamSpec("", 4)
    with pytest.raises(sharding.ShardingError):
        ParamSpec("w", 0)
    with pytest.raises(sharding.ShardingError):
        sharding.plan_ddp([ParamSpec("w", 4)], 0)
    with pytest.raises(sharding.ShardingError):
        sharding.plan_fsdp([], 4)


def test_schedule_validation_requires_one_buffer_per_rank():
    plan = sharding.plan_ddp([ParamSpec("w", 4)], 4)
    with pytest.raises(sharding.ShardingError):
        sharding.validate_schedule_against_collective(plan, _grads(2, 4))


def test_strategies_listing():
    assert set(sharding.strategies()) == {"ddp", "fsdp"}
