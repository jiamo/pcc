"""GC backend 4 must reuse the one pcc package environment."""

from tests.python.package_environment_profile_contract import (
    assert_profile_environment_invariance,
)


def test_package_environment_invariant_under_gc4(tmp_path):
    assert_profile_environment_invariance(tmp_path, {"PCC_GC_BACKEND": "4"})
