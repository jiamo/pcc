"""Stage A/B binds one external resource envelope to Stage1 and Stage2.

The earlier operational pair mixed caps (host Stage1 resolved against ~48 GiB,
capped Stage2 at 8 GiB), so a Stage2/Stage1 ratio was not same-resource.  The
harness now records a ``resource_envelope`` on every stage record and refuses
to accept an arm whose two stages differ on any envelope-parity key
(cap / CPU / jobs / gc / cache policy).  Peak RSS and admitted worker count are
observations INSIDE the envelope, recorded but not required equal.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

import pytest

STAGE_AB = Path(__file__).absolute().parents[2] / "scripts" / "run_pcc_stage_ab.py"


def _load_stage_ab():
    spec = importlib.util.spec_from_file_location("pcc_stage_ab_test_module", STAGE_AB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    scripts = str(STAGE_AB.parent)
    sys.path.insert(0, scripts)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
    return module


def _args(**overrides):
    base = dict(
        max_tree_rss_bytes=8 * 1024 * 1024 * 1024,
        frontend_jobs=2,
        self_backend_jobs=2,
        gc_backend=0,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


_ENV = {
    "PCC_PY_FRONTEND_IR_CACHE": "0",
    "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
    "PYTHONPYCACHEPREFIX": "/tmp/private-pycache",
}


def _proc(**overrides):
    base = dict(
        peak_tree_rss_bytes=4_000_000_000,
        peak_process_count=9,
        status="COMPLETE",
        returncode=0,
    )
    base.update(overrides)
    return base


def test_resource_envelope_records_the_cap_and_observations():
    stage_ab = _load_stage_ab()
    envelope = stage_ab._resource_envelope(
        args=_args(), environment=_ENV, process=_proc(peak_process_count=6)
    )
    assert envelope["max_tree_rss_bytes"] == 8 * 1024 * 1024 * 1024
    assert envelope["cpu_count"] >= 1
    assert envelope["cache_policy"]["PCC_PY_FRONTEND_IR_CACHE"] == "0"
    assert envelope["cache_policy"]["private_pycache"] == "True"
    # observations, not parity keys
    assert envelope["observed_peak_tree_rss_bytes"] == 4_000_000_000
    assert envelope["observed_peak_process_count"] == 6


def test_same_envelope_passes_even_with_different_observed_rss():
    stage_ab = _load_stage_ab()
    args = _args()
    stage1 = stage_ab._resource_envelope(
        args=args, environment=_ENV, process=_proc(peak_tree_rss_bytes=3_000_000_000, peak_process_count=2)
    )
    stage2 = stage_ab._resource_envelope(
        args=args, environment=_ENV, process=_proc(peak_tree_rss_bytes=7_900_000_000, peak_process_count=10)
    )
    # A live-RSS-driven admission difference inside the same cap must NOT fail.
    assert stage_ab.envelope_parity_key(stage1) == stage_ab.envelope_parity_key(stage2)
    stage_ab.assert_stage_envelope_parity(
        {"stage1": {"resource_envelope": stage1}, "stage2": {"resource_envelope": stage2}},
        arm="baseline",
    )


@pytest.mark.parametrize(
    ("override", "drift"),
    [
        ({"max_tree_rss_bytes": 48 * 1024 * 1024 * 1024}, "max_tree_rss_bytes"),
        ({"frontend_jobs": 1}, "frontend_jobs"),
        ({"gc_backend": 3}, "gc_backend"),
    ],
)
def test_mixed_cap_or_jobs_or_gc_is_refused(override, drift):
    stage_ab = _load_stage_ab()
    stage1 = stage_ab._resource_envelope(
        args=_args(), environment=_ENV, process=_proc()
    )
    stage2 = stage_ab._resource_envelope(
        args=_args(**override), environment=_ENV, process=_proc()
    )
    with pytest.raises(stage_ab.StageABError, match=drift):
        stage_ab.assert_stage_envelope_parity(
            {"stage1": {"resource_envelope": stage1}, "stage2": {"resource_envelope": stage2}},
            arm="candidate",
        )


def test_different_cache_policy_is_refused():
    stage_ab = _load_stage_ab()
    stage1 = stage_ab._resource_envelope(args=_args(), environment=_ENV, process=_proc())
    warm = dict(_ENV, PCC_SELF_BACKEND_OBJECT_CACHE="1")
    stage2 = stage_ab._resource_envelope(args=_args(), environment=warm, process=_proc())
    with pytest.raises(stage_ab.StageABError, match="cache_policy"):
        stage_ab.assert_stage_envelope_parity(
            {"stage1": {"resource_envelope": stage1}, "stage2": {"resource_envelope": stage2}},
            arm="baseline",
        )


def test_missing_envelope_fails_closed():
    stage_ab = _load_stage_ab()
    stage1 = stage_ab._resource_envelope(args=_args(), environment=_ENV, process=_proc())
    with pytest.raises(stage_ab.StageABError, match="missing its resource envelope"):
        stage_ab.assert_stage_envelope_parity(
            {"stage1": {"resource_envelope": stage1}, "stage2": {}},
            arm="candidate",
        )


def test_stage1_only_arm_needs_no_stage2_envelope():
    stage_ab = _load_stage_ab()
    stage1 = stage_ab._resource_envelope(args=_args(), environment=_ENV, process=_proc())
    # No stage2 key: a stage1-only arm is fine (no ratio to protect).
    stage_ab.assert_stage_envelope_parity(
        {"stage1": {"resource_envelope": stage1}}, arm="baseline"
    )
