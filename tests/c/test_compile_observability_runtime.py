from __future__ import annotations

import json

import pytest

from pcc.compile_observability import ObservabilityOptions, ObservedCompileError, observed_compile


def test_observed_compile_writes_profile_json(tmp_path):
    profile = tmp_path / "profile.json"
    def compile_fn(src, out):
        return f"{src}->{out}"
    result = observed_compile(
        compile_fn,
        "a.py", "a.out",
        options=ObservabilityOptions(profile_json=str(profile), phase="unit"),
    )
    assert result == "a.py->a.out"
    data = json.loads(profile.read_text())
    assert data["schema"] == "pcc.profile.v1"
    assert data["events"][0]["name"] == "unit"


def test_observed_compile_formats_json_error():
    def compile_fn():
        raise RuntimeError("boom")
    with pytest.raises(ObservedCompileError) as caught:
        observed_compile(
            compile_fn,
            options=ObservabilityOptions(diagnostic_format="json", phase="unit"),
        )
    data = json.loads(caught.value.formatted)
    assert data["diagnostics"][0]["code"] == "PCC-PY-COMPILE-001"
    assert data["diagnostics"][0]["phase"] == "unit"
