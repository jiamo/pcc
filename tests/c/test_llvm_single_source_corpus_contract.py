from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.llvm_single_source_cases import (
    REQUIRED_FEATURES,
    SCHEMA,
    load_llvm_single_source_corpus,
)


REPO_ROOT = Path(__file__).absolute().parents[2]
MANIFEST = REPO_ROOT / "tests" / "llvm_single_source_manifest.json"


def test_llvm_single_source_manifest_is_pinned_bounded_and_complete():
    corpus = load_llvm_single_source_corpus(REPO_ROOT, MANIFEST)

    assert corpus.commit == "824802c01e93a8d49a77384da4e68c76d1021953"
    assert len(corpus.cases) == 4
    assert corpus.wall_time_budget_seconds == 60
    assert {feature for case in corpus.cases for feature in case.features} == set(
        REQUIRED_FEATURES
    )
    assert all("/Benchmarks/" not in case.relative_path for case in corpus.cases)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"schema": "unknown"}, "schema"),
        ({"wall_time_budget_seconds": 121}, "wall_time_budget_seconds"),
        ({"cases": []}, "cases"),
    ],
)
def test_llvm_single_source_manifest_rejects_unreviewed_expansion(
    tmp_path, mutation, message
):
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw.update(mutation)
    candidate = tmp_path / "manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_llvm_single_source_corpus(REPO_ROOT, candidate)


def test_llvm_single_source_schema_is_not_shared_with_benchmark_manifests():
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert raw["schema"] == SCHEMA
    assert "benchmark" not in raw
    assert "score" not in raw
    assert "iterations" not in raw
