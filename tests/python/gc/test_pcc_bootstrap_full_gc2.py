"""Full pcc1 -> pcc2 -> pcc3 bootstrap gate for GC backend 2."""

from __future__ import annotations

import pytest

from tests.python.test_pcc_bootstrap_full import (
    bootstrap_gc_parallel_slots,
    run_full_three_stage_bootstrap_self_gc,
    shared_stage1_pcc1,
)

pytestmark = pytest.mark.integration


def test_full_three_stage_bootstrap_self_gc2(
    shared_stage1_pcc1,
    pcc_py_runtime_archive,
    bootstrap_gc_parallel_slots,
) -> None:
    run_full_three_stage_bootstrap_self_gc(
        "2",
        shared_stage1_pcc1,
        pcc_py_runtime_archive,
        parallel_slots=bootstrap_gc_parallel_slots,
    )
