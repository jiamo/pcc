"""Fast contracts for the host-pcc/pcc1 applicability and artifact ratchets.

The expensive observable parity execution remains in
``test_self_host_oracle_diff.py`` so it reuses that file's one session pcc1.
This file owns only fast, compiler-free failure-shape checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.host_pcc_pcc1_parity import (
    ParityContractError,
    candidate_names_sha256,
    load_applicability_manifest,
    pcc1_receipt_path,
    verify_pcc1_receipt,
    write_parity_failure_report,
    write_pcc1_receipt,
)
from tests.py_corpus_support import PYTHON_CORPUS_CASES
from tests.python.test_self_host_oracle_diff import CASES, INTENT_PARITY_INPUTS


REPO_ROOT = Path(__file__).absolute().parents[2]
MANIFEST = REPO_ROOT / "tests" / "host_pcc_pcc1_parity_manifest.json"
CORPUS_MANIFEST = REPO_ROOT / "tests" / "host_pcc_pcc1_corpus_parity_manifest.json"
INTENT_MANIFEST = REPO_ROOT / "tests" / "host_pcc_pcc1_intent_parity_manifest.json"


def _manifest_document() -> dict[str, object]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_manifest(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_manifest_covers_every_current_host_pcc_candidate():
    applicability = load_applicability_manifest(MANIFEST, CASES)

    assert len(applicability.applicable) + len(applicability.exclusions) == len(CASES)
    assert applicability.candidate_names_sha256 == candidate_names_sha256(
        name for name, _source in CASES
    )

    corpus_inputs = tuple((case.name, case.source) for case in PYTHON_CORPUS_CASES)
    corpus = load_applicability_manifest(
        CORPUS_MANIFEST,
        corpus_inputs,
        registry_id="tests.py_corpus_support.PYTHON_CORPUS_CASES",
    )
    assert len(corpus.applicable) + len(corpus.exclusions) == len(corpus_inputs)

    intent = load_applicability_manifest(
        INTENT_MANIFEST,
        INTENT_PARITY_INPUTS,
        registry_id="tests.python.test_intent_constraints.HOST_PCC_PROGRAMS",
    )
    assert len(intent.applicable) + len(intent.exclusions) == len(
        INTENT_PARITY_INPUTS
    )


def test_discovery_rejects_an_unclassified_candidate(tmp_path):
    extra_cases = CASES + (("new_unclassified_host_regression", "print('new')\n"),)

    with pytest.raises(ParityContractError, match="unclassified parity candidate"):
        load_applicability_manifest(MANIFEST, extra_cases)

    extra_intent = INTENT_PARITY_INPUTS + (
        ("semantics__new_unclassified_intent", "print('new intent')\n"),
    )
    with pytest.raises(ParityContractError, match="unclassified parity candidate"):
        load_applicability_manifest(
            INTENT_MANIFEST,
            extra_intent,
            registry_id="tests.python.test_intent_constraints.HOST_PCC_PROGRAMS",
        )


def test_exclusion_requires_a_current_case_reason_and_owner(tmp_path):
    document = _manifest_document()
    excluded = CASES[0][0]
    document["exclusions"] = [
        {
            "case": excluded,
            "reason": "host_libpython_only",
            "owner": "python/libpython-boundary",
            "note": "Synthetic contract probe; the checked-in manifest has no exclusions.",
        }
    ]
    path = _write_manifest(tmp_path / "classified.json", document)

    applicability = load_applicability_manifest(path, CASES)

    assert excluded not in {name for name, _source in applicability.applicable}
    assert applicability.exclusions[0]["reason"] == "host_libpython_only"

    document["exclusions"][0]["reason"] = "temporary_skip"
    invalid = _write_manifest(tmp_path / "invalid.json", document)
    with pytest.raises(ParityContractError, match="unsupported parity exclusion reason"):
        load_applicability_manifest(invalid, CASES)


def test_pcc1_receipt_binds_source_identity_and_binary_bytes(tmp_path):
    binary = tmp_path / "pcc1"
    binary.write_bytes(b"first pcc1 bytes\n")
    binary.chmod(0o755)
    receipt = pcc1_receipt_path(binary)
    expected = write_pcc1_receipt(
        binary,
        receipt,
        source_key="source-key",
        object_cache_identity="object-key",
    )

    assert verify_pcc1_receipt(
        binary,
        receipt,
        source_key="source-key",
        object_cache_identity="object-key",
    ) == expected

    with pytest.raises(ParityContractError, match="does not match"):
        verify_pcc1_receipt(
            binary,
            receipt,
            source_key="different-source-key",
            object_cache_identity="object-key",
        )

    binary.write_bytes(b"replaced pcc1 bytes\n")
    binary.chmod(0o755)
    with pytest.raises(ParityContractError, match="does not match"):
        verify_pcc1_receipt(
            binary,
            receipt,
            source_key="source-key",
            object_cache_identity="object-key",
        )


def test_failure_report_is_written_before_fail_fast_assertion(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_HOST_PCC1_PARITY_REPORT_DIR", str(tmp_path / "reports"))

    report = write_parity_failure_report(
        source_key="source-key",
        case_name="failing_case",
        payload={
            "problem": "pcc1 compile failed",
            "host_pcc": {"returncode": 0},
            "pcc1": {"returncode": 1, "stderr": "first error"},
        },
    )

    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["case"] == "failing_case"
    assert document["problem"] == "pcc1 compile failed"
    assert document["pcc1"]["stderr"] == "first error"
