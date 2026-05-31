from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from tests import c_testsuite_cases


def test_run_native_uses_longer_timeout_under_xdist(monkeypatch, tmp_path):
    case_path = tmp_path / "case.c"
    case_path.write_text("int main(void) { return 0; }\n")
    timeouts = []

    def fake_run(args, **kwargs):
        del args
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setattr(c_testsuite_cases, "_host_cc", lambda: "cc")
    monkeypatch.setattr(c_testsuite_cases.subprocess, "run", fake_run)

    result = c_testsuite_cases.run_native(case_path, tmp_path)

    assert result.returncode == 0
    assert timeouts == [c_testsuite_cases.XDIST_TIMEOUT, c_testsuite_cases.XDIST_TIMEOUT]


def test_run_pcc_uses_longer_timeout_under_xdist(monkeypatch, tmp_path):
    case_path = tmp_path / "case.c"
    case_path.write_text("int main(void) { return 0; }\n")
    captured = {}

    def fake_run_worker_process(target, args, timeout):
        del target
        captured["args"] = args
        captured["timeout"] = timeout
        return SimpleNamespace(
            timed_out=False,
            exitcode=0,
            payload={"returncode": 0, "stdout": "", "stderr": ""},
        )

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setattr(c_testsuite_cases, "run_worker_process", fake_run_worker_process)

    result = c_testsuite_cases.run_pcc(case_path, Path("."))

    assert result.returncode == 0
    assert captured["timeout"] == c_testsuite_cases.XDIST_TIMEOUT
    assert captured["args"][2] == c_testsuite_cases.XDIST_TIMEOUT
