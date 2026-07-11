"""GPU external resource seam contract for GC backend 4."""

from gpu_external_resource_contract import assert_external_resource_contract


def test_gpu_external_resource_gc4():
    assert_external_resource_contract(4)
