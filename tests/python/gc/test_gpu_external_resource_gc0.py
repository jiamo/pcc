"""GPU external resource seam contract for GC backend 0."""

from gpu_external_resource_contract import assert_external_resource_contract


def test_gpu_external_resource_gc0():
    assert_external_resource_contract(0)
