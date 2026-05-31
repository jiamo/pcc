from __future__ import annotations

from tests.parallel_jobs import translation_unit_jobs


def test_translation_unit_jobs_defaults_to_requested_parallelism(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    assert translation_unit_jobs() == 2
    assert translation_unit_jobs(default=3) == 3


def test_translation_unit_jobs_collapses_nested_parallelism_under_xdist(monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")

    assert translation_unit_jobs() == 1
    assert translation_unit_jobs(default=3) == 1
