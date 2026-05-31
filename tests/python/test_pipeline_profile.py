from __future__ import annotations

from pcc.profile_events import ProfileRecorder
from pcc.py_frontend.pipeline_profile import ROADMAP_PHASES, run_profiled_phase, seed_expected_phase_counters


def test_pipeline_profile_records_named_phase():
    rec = ProfileRecorder()
    result = run_profiled_phase(rec, "parse", lambda x: x + 1, 41)
    assert result.name == "parse"
    assert result.value == 42
    data = rec.to_json()
    assert data["events"][0]["name"] == "parse"


def test_pipeline_profile_exposes_all_roadmap_phase_names():
    rec = ProfileRecorder()
    seed_expected_phase_counters(rec)
    for name in ROADMAP_PHASES:
        assert "phase.expected." + name in rec.counters
