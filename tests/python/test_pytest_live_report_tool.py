"""Exercise crash-surviving report behavior through the public pytest hooks."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_first_failure_is_durable_before_session_finishes(tmp_path):
    case = tmp_path / "test_case.py"
    case.write_text(
        "def test_first():\n    assert False, 'durable sentinel'\n\ndef test_never():\n    raise RuntimeError('should not run')\n"
    )
    report = tmp_path / "live.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            "-n0",
            "-x",
            "--tb=short",
            "-p",
            "scripts.pytest_live_report",
            "--pcc-live-report",
            str(report),
            str(case),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    rows = [json.loads(line) for line in report.read_text().splitlines()]
    failed = [row for row in rows if row.get("outcome") == "failed"]
    assert len(failed) == 1
    assert failed[0]["nodeid"].endswith("::test_first")
    assert "durable sentinel" in failed[0]["longrepr"]
    assert rows.index(failed[0]) < len(rows) - 1
    assert rows[-1]["event"] == "finish"
    assert rows[-1]["exitstatus"] == 1
    assert not any(row.get("nodeid", "").endswith("::test_never") for row in rows)


def test_live_report_refuses_to_overwrite_previous_evidence(tmp_path):
    case = tmp_path / "test_case.py"
    case.write_text("def test_ok():\n    pass\n")
    report = tmp_path / "live.jsonl"
    report.write_text("prior evidence\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            "-n0",
            "-x",
            "-p",
            "scripts.pytest_live_report",
            "--pcc-live-report",
            str(report),
            str(case),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert report.read_text() == "prior evidence\n"


@pytest.mark.parametrize("arguments", [["-n0", "--collect-only"], ["-n2"]])
def test_selected_collection_is_recorded_once_for_serial_and_xdist(tmp_path, arguments, monkeypatch):
    monkeypatch.setenv("PCC1_BINARY", str(tmp_path / "selected-pcc1"))
    case = tmp_path / "test_case.py"
    case.write_text("def test_retain_marker():\n    pass\n\ndef test_excluded():\n    pass\n")
    report = tmp_path / "live.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            "-x",
            *arguments,
            "-k",
            "retain_marker",
            "-p",
            "scripts.pytest_live_report",
            "--pcc-live-report",
            str(report),
            str(case),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in report.read_text().splitlines()]
    collections = [row["nodeids"] for row in rows if row["event"] == "collected"]
    assert rows[0]["keyword"] == "retain_marker"
    assert rows[0]["ignore"] == []
    assert rows[0]["ignore_glob"] == []
    assert rows[0]["deselect"] == []
    assert rows[0]["collect_only"] == ("--collect-only" in arguments)
    assert rows[0]["validation_environment"]["PCC1_BINARY"] == str(tmp_path / "selected-pcc1")
    assert len(collections) == 1
    assert len(collections[0]) == 1
    assert collections[0][0].endswith("::test_retain_marker")
    assert rows[-1]["event"] == "finish"
    assert rows[-1]["testscollected"] == 1
