"""GPU external resource seam contract for GC backend 3."""

from gpu_external_resource_contract import assert_external_resource_contract


def test_gpu_external_resource_gc3():
    assert_external_resource_contract(3)
