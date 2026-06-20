"""K-P0-TILELANG-COMPAT — accepted/rejected construct matrix (inspect-only)."""

import pytest

from pcc.kernel_ir.tilelang_compat import (
    Support,
    TileLangCompatError,
    accepted_names,
    classify,
    is_accepted,
    rejected_names,
)


@pytest.mark.parametrize(
    "name",
    ["Kernel", "Tensor", "Buffer", "alloc_shared", "alloc_fragment",
     "Parallel", "vectorized", "Pipelined", "use_swizzle", "annotate_layout",
     "copy", "async_copy", "gemm", "clear", "fill"],
)
def test_high_value_subset_accepted(name):
    info = classify(name)
    assert info.support is Support.ACCEPTED
    assert info.pcc_construct is not None


@pytest.mark.parametrize(
    "name",
    ["alloc_tmem", "alloc_tcgen", "alloc_wgmma", "cutedsl", "tma_load", "tma_store"],
)
def test_out_of_scope_rejected(name):
    info = classify(name)
    assert info.support is Support.REJECTED
    assert info.pcc_construct is None


def test_t_prefix_accepted():
    assert is_accepted("T.copy")
    assert is_accepted("T.gemm")
    assert not is_accepted("T.alloc_tmem")


def test_unknown_construct_is_not_silently_accepted():
    # An unknown construct must raise, not be treated as supported.
    with pytest.raises(TileLangCompatError, match="not in the pcc compatibility"):
        classify("T.some_future_intrinsic")


def test_accepted_and_rejected_are_disjoint_and_nonempty():
    a = set(accepted_names())
    r = set(rejected_names())
    assert a and r
    assert a.isdisjoint(r)


def test_async_copy_accepted_but_maps_to_fence_needing_op():
    info = classify("async_copy")
    assert info.support is Support.ACCEPTED
    assert "fence" in info.reason.lower()
