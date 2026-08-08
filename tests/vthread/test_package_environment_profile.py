"""Thread and virtual-thread scheduler policy must reuse one environment."""

from tests.python.package_environment_profile_contract import (
    assert_profile_environment_invariance,
)


def test_package_environment_invariant_under_threaded_runtime(tmp_path):
    assert_profile_environment_invariance(tmp_path, {"PCC_WITH_THREADS": "1"})


def test_package_environment_invariant_under_scheduler_policy(tmp_path):
    assert_profile_environment_invariance(
        tmp_path,
        {"PCC_VTHREAD_PARKED": "1", "PCC_WITH_THREADS": "1", "PCC_DS": "1"},
    )
