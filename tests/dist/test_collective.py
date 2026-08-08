"""Oracle tests for pcc.dist.collective (D-P0-DIST-COLLECTIVE).

Deterministic single-process semantics for allreduce, reduce-scatter,
all-gather, broadcast, barrier over fake ranks + POD buffers, plus shape/dtype
mismatch errors and timeout/cancel metadata.
"""
import pytest

from pcc.dist import collective


# --- allreduce -------------------------------------------------------------
def test_allreduce_sum():
    out, meta = collective.allreduce([[1, 2], [3, 4], [5, 6]], "sum")
    assert out == [[9, 12], [9, 12], [9, 12]]  # every rank identical
    assert meta.kind == "allreduce" and meta.world_size == 3 and meta.reduce == "sum"


@pytest.mark.parametrize("op,expected", [
    ("sum", [9, 12]),
    ("max", [5, 6]),
    ("min", [1, 2]),
    ("prod", [15, 48]),
])
def test_allreduce_ops(op, expected):
    out, _ = collective.allreduce([[1, 2], [3, 4], [5, 6]], op)
    assert out[0] == expected


def test_allreduce_is_deterministic_and_order_independent_for_commutative():
    a = collective.allreduce([[1, 2], [3, 4], [5, 6]], "sum")[0][0]
    b = collective.allreduce([[5, 6], [1, 2], [3, 4]], "sum")[0][0]
    assert a == b  # sum is commutative -> rank order does not change result


def test_allreduce_float_dtype():
    out, _ = collective.allreduce([[1.5], [2.5]], "sum")
    assert out[0] == [4.0]


# --- reduce_scatter --------------------------------------------------------
def test_reduce_scatter_chunks():
    out, meta = collective.reduce_scatter([[1, 2, 3, 4], [10, 20, 30, 40]], "sum")
    # reduced = [11,22,33,44]; world=2 -> rank0=[11,22], rank1=[33,44]
    assert out == [[11, 22], [33, 44]]
    assert meta.detail["chunk"] == 2


def test_reduce_scatter_requires_divisible_length():
    with pytest.raises(collective.CollectiveError):
        collective.reduce_scatter([[1, 2, 3], [4, 5, 6]], "sum")  # 3 not divisible by 2


# --- all_gather ------------------------------------------------------------
def test_all_gather_concat_ascending():
    out, meta = collective.all_gather([[1, 2], [3, 4], [5, 6]])
    assert out == [[1, 2, 3, 4, 5, 6]] * 3
    assert meta.detail["per_rank_len"] == 2


# --- broadcast -------------------------------------------------------------
def test_broadcast_from_root():
    out, meta = collective.broadcast([[1, 1], [2, 2], [3, 3]], root=1)
    assert out == [[2, 2], [2, 2], [2, 2]]
    assert meta.detail["root"] == 1


def test_broadcast_bad_root():
    with pytest.raises(collective.CollectiveError):
        collective.broadcast([[1], [2]], root=5)


# --- barrier ---------------------------------------------------------------
def test_barrier_metadata():
    meta = collective.barrier(4, timeout_s=1.0)
    assert meta.kind == "barrier" and meta.world_size == 4
    assert meta.status == "completed" and meta.timeout_s == 1.0


def test_barrier_bad_world():
    with pytest.raises(collective.CollectiveError):
        collective.barrier(0)


# --- error paths -----------------------------------------------------------
def test_shape_mismatch_raises():
    with pytest.raises(collective.CollectiveError):
        collective.allreduce([[1, 2], [3, 4, 5]], "sum")


def test_dtype_mismatch_raises():
    with pytest.raises(collective.CollectiveError):
        collective.allreduce([[1, 2], [3.0, 4.0]], "sum")  # int vs float


def test_empty_buffers_raise():
    with pytest.raises(collective.CollectiveError):
        collective.allreduce([[], []], "sum")


def test_unknown_reduce_op_raises():
    with pytest.raises(collective.CollectiveError):
        collective.allreduce([[1], [2]], "median")


# --- ring FSDP identity ----------------------------------------------------
def test_reduce_scatter_then_all_gather_equals_allreduce():
    bufs = [[1, 2, 3, 4], [10, 20, 30, 40], [100, 200, 300, 400], [1, 1, 1, 1]]
    reconstructed = collective.reduce_scatter_then_all_gather(bufs, "sum")
    reference = collective.allreduce(bufs, "sum")[0][0]
    assert reconstructed == reference


def test_timeout_metadata_carried_but_never_fires():
    _, meta = collective.allreduce([[1], [2]], "sum", timeout_s=0.001)
    assert meta.timeout_s == 0.001
    assert meta.status == "completed"  # local oracle is synchronous, never times out
